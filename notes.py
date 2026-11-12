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
import shutil
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
DIARIZE_THREADS = 8  # 2.4x faster than 1 thread on a 10-core laptop, same speakers found
LIVE_THREADS = 4  # speech model threads while a meeting is on
LIVE_CHUNK = 30 * RATE  # transcribe this much at a time while recording
LIVE_CUT_WINDOW = 5 * RATE  # end each chunk at the quietest moment in its last seconds, not mid-word
AGENT_TOOLS = ["Read(./**)", "Glob", "Edit(./meetings/**)", "mcp__claude_ai_Microsoft_365__outlook_calendar_search"]

RECAP_INSTRUCTIONS = (
    "First, work out who each speaker is. 'You' is me; 'Others' or Speaker 1, 2... are other attendees. "
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


def recover_audio(folder):
    """Turn the .raw files written during recording into .wav files. Also rescues interrupted recordings."""
    folder = Path(folder)
    for raw in folder.glob("*.raw"):
        samples = np.fromfile(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if samples.size:
            save_wav(folder / f"{raw.stem}.wav", samples)
        raw.unlink()
    return folder


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
        self._chunks = {name: 0 for name, dev in sources.items() if dev is not None}  # samples written so far
        self.devices = {name: dev.name for name, dev in sources.items() if dev is not None}
        # Audio is written to disk as it is recorded, so a crash or a forced stop loses only seconds.
        self.folder = MEETINGS / datetime.now().strftime("%Y-%m-%d_%H%M")
        self.folder.mkdir(parents=True, exist_ok=True)

        def capture(name, device):
            with device.recorder(samplerate=RATE, channels=1) as rec, open(self.folder / f"{name}.raw", "wb") as raw:
                while not self._stop.is_set():
                    block = rec.record(numframes=CHUNK)[:, 0]
                    self._chunks[name] += block.size
                    raw.write((np.clip(block, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
                    raw.flush()

        self._threads = [
            threading.Thread(target=capture, args=(n, d), daemon=True)
            for n, d in sources.items() if d is not None
        ]
        for t in self._threads:
            t.start()
        self.started_at = time.time()

    def halt(self):
        """Stop capturing. The audio stays in the .raw files."""
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        self._threads = []
        if not any(self._chunks.values()):
            raise RuntimeError("Nothing was recorded. Check your microphone and speaker settings.")

    def stop(self):
        """Stop recording, save the audio, and return the meeting folder."""
        self.halt()
        return recover_audio(self.folder)


class LiveRecorder(Recorder):
    """Records and transcribes at the same time, so the transcript is ready soon after stop().

    No speaker separation: lines are "You" or "Others". If live transcription fails, stop() falls back
    to the full transcribe(), so a meeting is never lost.
    """

    def start(self):
        super().start()
        self._rows = []
        self._position = {name: 0 for name in self._chunks}
        self._language = None
        self._error = None
        self._captured = threading.Event()
        self._worker = threading.Thread(target=self._transcribe_live, daemon=True)
        self._worker.start()

    def _transcribe_live(self):
        try:
            model = load_model(cpu_threads=LIVE_THREADS)
            while not self._captured.is_set():
                if not any([self._take(model, name, False) for name in self._position]):
                    time.sleep(1)
            for name in self._position:
                while self._take(model, name, True):
                    pass
        except Exception as e:
            self._error = e

    def _take(self, model, name, final):
        """Transcribe the next chunk of one track. Returns False if there is not enough audio yet."""
        start = self._position[name]
        ready = self._chunks[name] - start - (0 if final else CHUNK)  # the newest block may not be on disk yet
        if ready <= 0 or (ready < LIVE_CHUNK and not final):
            return False
        size = min(ready, LIVE_CHUNK)
        samples = np.fromfile(self.folder / f"{name}.raw", dtype=np.int16, count=size, offset=start * 2)
        samples = samples.astype(np.float32) / 32768.0
        if samples.size == 0:
            return False
        if ready > size:
            size = quiet_cut(samples)
            samples = samples[:size]
        rows, language = segment_rows(model, samples, "You" if name == "mic" else "Others", start / RATE)
        self._rows += rows
        self._language = self._language or language
        self._position[name] = start + size
        return True

    def stop(self):
        try:
            self.halt()
        finally:
            self._captured.set()
            self._worker.join()
        folder = recover_audio(self.folder)
        if self._error:
            print(f"Live transcription failed ({self._error}). Transcribing the saved audio instead.")
            transcribe(folder)
        else:
            write_transcript(folder, self._rows, self._language)
        return folder


def quiet_cut(samples):
    """Where to end a chunk: the quietest quarter second inside its last seconds."""
    step = RATE // 4
    starts = range(samples.size - LIVE_CUT_WINDOW, samples.size - step, step)
    return min(starts, key=lambda s: float((samples[s:s + step] ** 2).mean())) + step // 2


def diarize(samples):
    """Return a list of (start, end, speaker_number) for the voices in the audio."""
    import sherpa_onnx as so

    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(SEG_MODEL)),
            num_threads=DIARIZE_THREADS,
        ),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=str(EMBED_MODEL), num_threads=DIARIZE_THREADS),
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


def segment_rows(model, samples, label, offset=0.0, turns=None):
    """Transcribe audio. Returns ([(start, who, text)], language, or None if nothing was said)."""
    segments, info = model.transcribe(samples, vad_filter=True, condition_on_previous_text=False)
    rows = []
    for s in segments:
        if label == "You":  # a quiet, unsure mic line is almost always made-up text from silence or leakage
            x = samples[int(s.start * RATE): int(s.end * RATE)]
            quiet = x.size == 0 or float(np.sqrt((x ** 2).mean())) < 0.01
            if quiet and (s.no_speech_prob > 0.3 or s.avg_logprob < -0.8):
                continue
        rows.append((offset + s.start, label or speaker_at(turns, s.start, s.end), s.text.strip()))
    return rows, info.language if rows else None


def write_transcript(folder, rows, language):
    folder = Path(folder)
    lines = []
    for start, who, text in sorted(rows):
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
    return out


def load_model(**options):
    """The speech model. Uses the copy on disk first: an update check over a stalled network
    once froze a meeting at "Transcribing..." for hours. Goes online only if it is not downloaded yet."""
    from faster_whisper import WhisperModel

    try:
        return WhisperModel(MODEL, device="cpu", compute_type="int8", local_files_only=True, **options)
    except Exception:  # first run
        return WhisperModel(MODEL, device="cpu", compute_type="int8", **options)


def transcribe(folder, log=print):
    folder = Path(folder)
    log(f"Loading speech model '{MODEL}' (first run downloads it)...")
    model = load_model()

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
        turns = diarize(samples) if label is None else None
        track_rows, track_language = segment_rows(model, samples, label, turns=turns)
        rows += track_rows
        language = language or track_language

    out = write_transcript(folder, rows, language)
    log(f"Saved {out}")
    return out


def find_claude():
    """The Claude command: on PATH, or the newest one installed by the desktop app."""
    installed = Path(os.environ.get("LOCALAPPDATA", HERE)).glob("Claude-Data/claude_*/claude-code/*/*/claude.exe")
    found = shutil.which("claude") or max(installed, key=lambda p: p.stat().st_mtime, default=None)
    if not found:
        raise RuntimeError("Claude Code is not installed.")
    return str(found)


def run_agent(folder):
    """Run the minutes agent (agent-instructions.md) on one meeting. Returns minutes.md.

    The agent can only read this project, write inside meetings/, and search the Outlook calendar.
    Every other tool is refused without asking, so nothing can be sent or changed.
    """
    folder = Path(folder)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC"))}
    env["MCP_CONNECTION_NONBLOCKING"] = "false"  # wait for the Outlook connection before starting
    done = subprocess.run(
        [find_claude(), "-p", "--tools", "Read,Write,Edit,Glob", "--allowedTools", " ".join(AGENT_TOOLS),
         "--permission-mode", "dontAsk", "--no-session-persistence", "--model", "sonnet"],
        input=f"Read agent-instructions.md and follow it exactly. Only process the meeting folder {folder.name}.",
        capture_output=True, text=True, encoding="utf-8", timeout=1200, cwd=HERE, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = folder / "minutes.md"
    if not out.exists():
        raise RuntimeError(f"The agent wrote no minutes: {done.stdout.strip()} {done.stderr.strip()}")
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
    rc = sub.add_parser("recover", help="rescue and transcribe a recording that was interrupted")
    rc.add_argument("folder")
    a = p.parse_args()
    if a.cmd == "record":
        finish(cli_record(a.seconds))
    elif a.cmd == "recover":
        finish(recover_audio(a.folder))
    else:
        finish(a.folder)
