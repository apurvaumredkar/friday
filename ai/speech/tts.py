import numpy as np
import logging
import onnxruntime as rt
from kokoro_onnx import Kokoro
from scipy.signal import resample_poly
from pyprojroot import here

ROOT = here()
KOKORO_PATH = ROOT / "data" / "kokoro"
VOICE = "af_heart"

logger = logging.getLogger(__name__)
_session = rt.InferenceSession(
    str(KOKORO_PATH / "kokoro-v1.0.onnx"),
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
kokoro = Kokoro.from_session(_session, str(KOKORO_PATH / "voices-v1.0.bin"))


def synthesize(text: str) -> bytes:
    samples, _ = kokoro.create(text, voice=VOICE, speed=1.0, lang="en-us")
    logger.info(f"Synthesized {len(text)} chars of audio")
    samples = resample_poly(samples, up=2, down=1)
    samples = (samples * 32767).astype(np.int16)
    samples = np.column_stack((samples, samples)).flatten()
    return samples.tobytes()
