import numpy as np
import onnxruntime as rt
import onnx_asr

# 0.5s of 48kHz stereo int16 PCM
VAD_CHUNK_BYTES = 48000 * 2 * 2 // 2

_so = rt.SessionOptions()
_so.intra_op_num_threads = 4
_so.inter_op_num_threads = 2
_so.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL

asr_model = onnx_asr.load_model(
    "nemo-parakeet-tdt-0.6b-v2",
    path="./data/nemo-parakeet-tdt-0.6b-v2-int8",
    quantization="int8",
    providers=["CPUExecutionProvider"],
    sess_options=_so,
)

vad_model = onnx_asr.load_vad("silero", path="./data/silero")


def transcribe(pcm_bytes: bytes) -> str:
    """Transcribe 48kHz stereo int16 PCM bytes. Returns transcript string."""
    waveform = (
        np.frombuffer(pcm_bytes, dtype=np.int16)
        .reshape(-1, 2)
        .mean(axis=1)
        .astype(np.float32)
        / 32768.0
    )
    return asr_model.recognize(waveform, sample_rate=48000)


def run_vad(chunk_bytes: bytes) -> bool:
    """Detect speech in a 0.5s chunk of 48kHz stereo int16 PCM."""
    chunk = (
        np.frombuffer(chunk_bytes, dtype=np.int16)
        .reshape(-1, 2)
        .mean(axis=1)
        .astype(np.float32)
        / 32768.0
    )
    mono_16k = chunk[::3]
    waveforms = mono_16k[np.newaxis, :]
    waveforms_len = np.array([len(mono_16k)], dtype=np.int64)
    batch = next(
        vad_model.segment_batch(waveforms, waveforms_len, 16000, min_speech_duration_ms=100),
        None,
    )
    return bool(list(batch)) if batch is not None else False
