import onnx_asr
import numpy as np
import logging
from scipy.signal import resample_poly
from pyprojroot import here

ROOT = here()
ASR_MODEL_NAME = "nemo-parakeet-tdt-0.6b-v2"
VAD_MODEL_NAME = "silero"
MODEL_PATH = ROOT / "data" / "parakeet"

logger = logging.getLogger(__name__)
vad = onnx_asr.load_vad(VAD_MODEL_NAME)
model = onnx_asr.load_model(
    model=ASR_MODEL_NAME, path=MODEL_PATH, providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
).with_vad(vad)


def preprocess_audio(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16)
    audio = audio.reshape(-1, 2).mean(axis=1).astype(np.int16)  # stereo → mono
    audio = audio.astype(np.float32) / 32768.0  # normalization
    audio = resample_poly(
        audio, up=1, down=3
    )  # discord 48kHz to 16kHz for parakeet processing
    return audio


def transcribe(pcm_bytes: bytes) -> str:
    audio = preprocess_audio(pcm_bytes)
    results = model.recognize(audio, sample_rate=16000)
    transcript = " ".join(r.text for r in results)
    logger.info(f"Audio message transcribed: {transcript}")
    return transcript
