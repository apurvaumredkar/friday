from pathlib import Path

import numpy as np
import onnxruntime as rt
import requests
from fastapi import FastAPI
from kokoro_onnx import Kokoro
from scipy.signal import resample_poly

_KOKORO_DIR = Path("./data/kokoro-82m")
_KOKORO_DIR.mkdir(parents=True, exist_ok=True)
_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

for _fname in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
    _dest = _KOKORO_DIR / _fname
    if not _dest.exists():
        _dest.write_bytes(requests.get(f"{_BASE_URL}/{_fname}").content)

_session = rt.InferenceSession(
    str(_KOKORO_DIR / "kokoro-v1.0.onnx"),
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
tts_model = Kokoro.from_session(_session, str(_KOKORO_DIR / "voices-v1.0.bin"))

api = FastAPI()


def synthesize(text: str) -> bytes:
    """Return 48kHz stereo int16 PCM bytes for the given text."""
    samples, _ = tts_model.create(text, voice="af_heart")
    pcm_24k = (samples * 32767).clip(-32768, 32767).astype(np.float32)
    pcm_48k = resample_poly(pcm_24k, up=2, down=1).astype(np.int16)
    return np.column_stack([pcm_48k, pcm_48k]).tobytes()
