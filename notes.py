"""Local meeting recorder and transcriber with speaker labels. Everything stays on this computer.

  python notes.py record              record mic + meeting sound until Ctrl+C, then transcribe
  python notes.py record --seconds 10 record for a fixed time (good for a quick test)
  python notes.py transcribe FOLDER   transcribe a recording folder under meetings/

Speaker labels: your mic is "You". The meeting sound is split into "Speaker 1", "Speaker 2", ...
by voice. Use headphones: with speakers, your mic hears the others and repeats their words as "You".
Windows captures meeting sound directly. On macOS it needs the free BlackHole driver (see README).
"""
import argparse
import os
import platform
import subprocess
import threading
import time
import warnings
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
warnings.filterwarnings("ignore", message="data discontinuity")

RATE = 16000  # Whisper and the speaker model work at 16 kHz mono
CHUNK = 1600  # 0.1 s
HERE = Path(__file__).parent
MEETINGS = HERE / "meetings"
MODEL = "small"  # downloaded once (~460 MB), then cached; use "base" for faster, rougher output
SEG_MODEL = HERE / "models" / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBED_MODEL = HERE / "models" / "embed.onnx"

RECAP_INSTRUCTIONS = (
    "First, work out who each speaker is. 'You' is me; Speaker 1, 2... are other attendees. "
    "Infer names only from what people say (how they address each other, introductions, roles). "
    "Show a small table: speaker, proposed name, evidence (a short quote with its timestamp), "
    "confidence (sure / guess). For any speaker you cannot name, show 2 or 3 sample lines with "
    "timestamps so I can recognise them, and do not invent a name. "
    "Then recap the meeting: a 4-sentence overview, decisions, action items (owner and due date "
    "if mentioned), and open questions. Use real names where sure, 'Speaker N' where not. "
    "I will correct names afterwards.\n\n"
)


def save_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16).tobytes())


def load_wav(path):
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def find_sources():
    """Return ({'mic': device, 'meeting': device or None}, notes). Meeting sound needs a loopback."""
    import soundcard as sc

    notes = []
    sources = {"mic": sc.default_microphone(), "meeting": None}
    if platform.system() == "Darwin":
        for m in sc.all_microphones():
            if "blackhole" in m.name.lower():
                sources["meeting"] = m
                break
        if sources["meeting"] is None:
            notes.append(
                "No BlackHole device found, so only your microphone will be recorded. "
                "See the README, section 'Mac: capture meeting sound'."
            )
    else:
        try:
            sources["meeting"] = sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
        except Exception as e:  # no speaker or no loopback support
            notes.append(f"Could not open meeting sound capture ({e}). Only your microphone will be recorded.")
    return sources, notes


class Recorder:
    """Records mic and meeting sound in the background until stop() is called."""

    def __init__(self):
        self._stop = threading.Event()
        self._threads = []
        self._chunks = {}
        self.started_at = None
        self.notes = []

    @property
    def running(self):
        return bool(self._threads) and not self._stop.is_set()

    def start(self):
        if self.running:
            raise RuntimeError("Already recording.")
        sources, self.notes = find_sources()
        self._stop.clear()
        self._chunks = {name: [] for name, dev in sources.items() if dev is not None}
        self.devices = {name: dev.name for name, dev in sources.items() if dev is not None}

        def capture(name, device):
            with device.recorder(samplerate=RATE, channels=1) as rec:
                while not self._stop.is_set():
                    self._chunks[name].append(rec.record(numframes=CHUNK)[:, 0])

        self._threads = [
            threading.Thread(target=capture, args=(n, d), daemon=True)
            for n, d in sources.items() if d is not None
        ]
        for t in self._threads:
            t.start()
        self.started_at = time.time()

    def stop(self):
        """Stop recording, save the audio, and return the meeting folder."""
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        self._threads = []
        if not any(self._chunks.values()):
            raise RuntimeError("Nothing was recorded. Check your microphone and speaker settings.")
        folder = MEETINGS / datetime.now().strftime("%Y-%m-%d_%H%M")
        folder.mkdir(parents=True, exist_ok=True)
        for name, parts in self._chunks.items():
            if parts:
                save_wav(folder / f"{name}.wav", np.concatenate(parts))
        return folder


def diarize(samples):
    """Return a list of (start, end, speaker_number) for the voices in the audio."""
    import sherpa_onnx as so

    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_MODEL))
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(EMBED_MODEL)),
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    result = so.OfflineSpeakerDiarization(config).process(samples).sort_by_start_time()
    return [(r.start, r.end, r.speaker + 1) for r in result]


def speaker_at(turns, start, end):
    """Speaker with the most overlap with [start, end]; nearest turn if none overlaps."""
    best, best_overlap = None, 0.0
    for s, e, spk in turns:
        overlap = min(end, e) - max(start, s)
        if overlap > best_overlap:
            best, best_overlap = spk, overlap
    if best is None and turns:
        best = min(turns, key=lambda t: min(abs(t[0] - start), abs(t[1] - end)))[2]
    return f"Speaker {best}" if best else "Speaker"


def transcribe(folder, log=print):
    from faster_whisper import WhisperModel

    folder = Path(folder)
    log(f"Loading speech model '{MODEL}' (first run downloads it)...")
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")

    tracks = []  # (file, fixed label or None to detect speakers)
    if (folder / "mic.wav").exists():
        tracks.append((folder / "mic.wav", "You"))
    if (folder / "meeting.wav").exists():
        tracks.append((folder / "meeting.wav", None))
    if not tracks and (folder / "audio.wav").exists():  # older single-file recordings
        tracks.append((folder / "audio.wav", None))
    if not tracks:
        raise FileNotFoundError(f"No audio found in {folder}")

    rows, language = [], None
    for path, label in tracks:
        log(f"Transcribing {path.name}...")
        samples = load_wav(path)
        segments, info = model.transcribe(samples, vad_filter=True)
        language = language or info.language
        segments = list(segments)
        turns = diarize(samples) if label is None and segments else []
        for s in segments:
            if label == "You":  # a quiet, unsure mic line is almost always made-up text from silence or leakage
                x = samples[int(s.start * RATE): int(s.end * RATE)]
                quiet = x.size == 0 or float(np.sqrt((x ** 2).mean())) < 0.01
                if quiet and (s.no_speech_prob > 0.3 or s.avg_logprob < -0.8):
                    continue
            who = label or speaker_at(turns, s.start, s.end)
            rows.append((s.start, who, s.text.strip()))

    rows.sort()
    lines = []
    for start, who, text in rows:
        m, sec = divmod(int(start), 60)
        lines.append(f"[{m:02d}:{sec:02d}] {who}: {text}")
    out = folder / "transcript.md"
    out.write_text(
        f"# Transcript {folder.name}\n\nDetected language: {language}\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    (folder / "recap-request.md").write_text(
        RECAP_INSTRUCTIONS + out.read_text(encoding="utf-8"), encoding="utf-8"
    )
    log(f"Saved {out}")
    return out


def copy_to_clipboard(text):
    """Copy text to the clipboard. Returns True on success."""
    try:
        if platform.system() == "Windows":
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
        elif platform.system() == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        else:
            return False
        return True
    except Exception:
        return False


def finish(folder):
    out = transcribe(folder)
    for line in out.read_text(encoding="utf-8").splitlines():
        if line.startswith("["):
            print(line)
    request = (Path(folder) / "recap-request.md").read_text(encoding="utf-8")
    if copy_to_clipboard(request):
        print("Recap request copied to your clipboard. Paste it into Claude.")
    else:
        print(f"Recap request saved: {Path(folder) / 'recap-request.md'}")


def cli_record(seconds):
    rec = Recorder()
    rec.start()
    for note in rec.notes:
        print("NOTE:", note)
    for name, device in rec.devices.items():
        print(f"Recording {name}: {device}")
    print("Press Ctrl+C to stop." if seconds is None else f"Recording for {seconds} seconds...")
    try:
        end = time.time() + seconds if seconds else None
        while end is None or time.time() < end:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    folder = rec.stop()
    print(f"Saved recordings in {folder}")
    return folder


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--seconds", type=int)
    t = sub.add_parser("transcribe")
    t.add_argument("folder")
    a = p.parse_args()
    finish(cli_record(a.seconds) if a.cmd == "record" else a.folder)
