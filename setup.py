"""Download the speaker-recognition models once. Safe to run again."""
import tarfile
import urllib.request
from pathlib import Path

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MODELS = Path(__file__).parent / "models"
SEG_DIR = MODELS / "sherpa-onnx-pyannote-segmentation-3-0"
EMBED = MODELS / "embed.onnx"


def fetch(url, dest):
    print(f"Downloading {dest.name} ...")
    urllib.request.urlretrieve(url, dest)


MODELS.mkdir(exist_ok=True)
if not (SEG_DIR / "model.onnx").exists():
    archive = MODELS / "seg.tar.bz2"
    fetch(f"{BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2", archive)
    with tarfile.open(archive, "r:bz2") as t:
        t.extractall(MODELS, filter="data")
    archive.unlink()
if not EMBED.exists():
    fetch(f"{BASE}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx", EMBED)

# Pre-download the speech model too, so the first meeting does not wait.
from faster_whisper import WhisperModel  # noqa: E402

print("Downloading the speech model (about 460 MB, first time only) ...")
WhisperModel("small", device="cpu", compute_type="int8")
print("Setup finished.")
