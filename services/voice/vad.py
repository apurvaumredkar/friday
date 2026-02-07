"""
Voice Activity Detection using Silero VAD v5 (ONNX).

Lightweight neural VAD for speech/non-speech classification.
Replaces energy-based silence detection for robust noise handling.

Model: ~2MB ONNX, <1ms inference per 512-sample chunk on CPU.
"""
import logging
import threading
import numpy as np
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Model path
MODEL_PATH = Path(__file__).parent.parent.parent / "ai" / "models" / "silero" / "silero_vad.onnx"

# VAD parameters
SAMPLE_RATE = 16000  # Silero expects 16kHz
CHUNK_SIZE = 512  # Samples per inference (32ms at 16kHz)
DEFAULT_THRESHOLD = 0.5  # Speech probability threshold


class SileroVAD:
    """
    Silero VAD v5 wrapper using ONNX Runtime.

    Thread-safe singleton with lazy loading and LSTM state management.
    Processes 512-sample chunks at 16kHz, returning speech probability.
    """

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self._session = None
        self._loaded = False
        self._load_lock = threading.Lock()
        self.threshold = threshold

        # LSTM hidden states (2, 1, 128) — reset between utterances
        self._h = np.zeros((2, 1, 128), dtype=np.float32)
        self._c = np.zeros((2, 1, 128), dtype=np.float32)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)

    def _load_model(self):
        """Load Silero VAD ONNX model with double-checked locking."""
        if self._loaded:
            return

        with self._load_lock:
            if self._loaded:
                return

            try:
                import time
                import onnxruntime as ort

                t0 = time.perf_counter()

                if not MODEL_PATH.exists():
                    raise FileNotFoundError(f"Silero VAD model not found at {MODEL_PATH}")

                sess_options = ort.SessionOptions()
                sess_options.inter_op_num_threads = 1
                sess_options.intra_op_num_threads = 1
                sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                self._session = ort.InferenceSession(
                    str(MODEL_PATH),
                    sess_options,
                    providers=["CPUExecutionProvider"],
                )

                load_time = time.perf_counter() - t0
                self._loaded = True
                logger.info(f"Silero VAD loaded in {load_time*1000:.0f}ms")

            except Exception as e:
                logger.error(f"Failed to load Silero VAD: {e}")
                raise

    def is_speech(self, audio_chunk: np.ndarray) -> tuple[bool, float]:
        """
        Check if a 512-sample audio chunk contains speech.

        Args:
            audio_chunk: float32 numpy array, 512 samples at 16kHz, normalized to [-1, 1]

        Returns:
            Tuple of (is_speech, probability)
        """
        self._load_model()

        # Reshape for batch dimension: (1, 512)
        input_data = audio_chunk.reshape(1, -1).astype(np.float32)

        # Run inference
        output, self._state = self._session.run(
            ["output", "stateN"],
            {
                "input": input_data,
                "state": self._state,
                "sr": self._sr,
            },
        )

        probability = float(output[0][0])
        return probability >= self.threshold, probability

    def reset_states(self):
        """Reset LSTM hidden states. Call between utterances."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)


# Thread-safe singleton
_vad_engine: Optional[SileroVAD] = None
_vad_engine_lock = threading.Lock()


def get_vad_engine() -> SileroVAD:
    """Get the global Silero VAD instance (thread-safe)."""
    global _vad_engine
    if _vad_engine is None:
        with _vad_engine_lock:
            if _vad_engine is None:
                _vad_engine = SileroVAD()
    return _vad_engine
