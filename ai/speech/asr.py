import logging
import os
import threading
import numpy as np
from typing import Optional, Union
import io

os.environ["ORT_LOGGING_LEVEL"] = "4"  # FATAL only (suppresses TensorRT fallback noise)

logger = logging.getLogger(__name__)

# Model configuration
MODEL_NAME = "nemo-parakeet-tdt-0.6b-v2"  # English TDT model
QUANTIZATION = "int8"  # Options: None, "int8", "fp16"
MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "parakeet")

SAMPLE_RATE = 16000


def _convert_audio_with_librosa(audio_bytes: bytes, target_sr: int = SAMPLE_RATE) -> tuple:
    """
    Convert audio bytes using librosa (supports WebM, MP3, etc.).

    Args:
        audio_bytes: Audio data in any format (WebM, MP3, etc.)
        target_sr: Target sample rate

    Returns:
        Tuple of (audio_samples, sample_rate)
    """
    import tempfile
    import librosa

    try:
        # Write input bytes to temporary file
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_path = temp_file.name

        logger.info(f"Converting audio with librosa (target: {target_sr}Hz)")

        # Load audio with librosa - it handles format conversion internally
        audio, sr = librosa.load(temp_path, sr=target_sr, mono=True)
        audio = audio.astype(np.float32)

        # Cleanup
        os.unlink(temp_path)

        logger.info(f"Audio converted successfully: {len(audio)} samples at {sr}Hz")
        return audio, sr

    except Exception as e:
        logger.error(f"Librosa conversion error: {e}")
        raise RuntimeError(f"Audio conversion failed: {e}")


def load_audio(audio_input: Union[str, bytes, np.ndarray], target_sr: int = SAMPLE_RATE) -> np.ndarray:
    """
    Load audio from file path, bytes, or numpy array.

    Returns:
        Audio samples as float32 numpy array, normalized to [-1, 1]
    """
    import soundfile as sf

    if isinstance(audio_input, np.ndarray):
        audio = audio_input.astype(np.float32)
        sr = target_sr  # Assume correct sample rate
    elif isinstance(audio_input, bytes):
        try:
            # Try reading directly with soundfile (works for WAV, FLAC, OGG, etc.)
            audio, sr = sf.read(io.BytesIO(audio_input))
            audio = audio.astype(np.float32)
        except Exception as e:
            # If soundfile fails (e.g., WebM format), use librosa for conversion
            logger.info(f"Soundfile failed ({type(e).__name__}), converting with librosa")
            audio, sr = _convert_audio_with_librosa(audio_input, target_sr)
    else:
        audio, sr = sf.read(audio_input)
        audio = audio.astype(np.float32)

    # Convert stereo to mono
    if len(audio.shape) > 1:
        audio = audio.mean(axis=1)

    # Resample if necessary (use soxr if available, fallback to resampy)
    if sr != target_sr:
        try:
            import soxr
            audio = soxr.resample(audio, sr, target_sr, quality='HQ')
        except ImportError:
            import resampy
            audio = resampy.resample(audio, sr, target_sr)

    # Normalize
    if audio.max() > 1.0 or audio.min() < -1.0:
        audio = audio / max(abs(audio.max()), abs(audio.min()))

    return audio


class ASREngine:
    """
    Automatic Speech Recognition engine using NVIDIA Parakeet TDT.

    Features:
    - ONNX Runtime backend for fast inference
    - INT8 quantization for reduced memory and faster processing
    - High accuracy English transcription
    - Thread-safe singleton pattern
    """

    def __init__(self):
        self._model = None
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load_model(self):
        """Lazy load the Parakeet model with double-checked locking."""
        if self._loaded:
            return

        with self._load_lock:
            if self._loaded:  # Double-check after acquiring lock
                return

            try:
                import time
                t0 = time.perf_counter()

                # Import onnx-asr
                logger.info("Importing onnx-asr...")
                import onnx_asr
                logger.info(f"  onnx-asr imported in {time.perf_counter() - t0:.1f}s")

                # Load model with GPU acceleration if available
                import onnxruntime as ort

                t1 = time.perf_counter()
                local_path = MODEL_PATH if os.path.isdir(MODEL_PATH) else None
                if local_path:
                    logger.info(f"Loading Parakeet model from local path: {local_path} ({QUANTIZATION})")
                else:
                    logger.info(f"Loading Parakeet model from HuggingFace: {MODEL_NAME} ({QUANTIZATION})")

                # Configure session options for optimal CPU performance
                # Note: INT8-quantized model runs best on CPU — CUDA EP causes
                # massive Conv op fallbacks and memory copies that hurt performance.
                sess_options = ort.SessionOptions()
                sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                sess_options.intra_op_num_threads = 4
                sess_options.inter_op_num_threads = 1
                sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                sess_options.enable_cpu_mem_arena = True

                providers = ["CPUExecutionProvider"]
                logger.info("ASR using CPU execution provider (INT8 optimized)")

                self._model = onnx_asr.load_model(
                    MODEL_NAME,
                    path=local_path,
                    quantization=QUANTIZATION,
                    sess_options=sess_options,
                    providers=providers,
                )
                logger.info(f"  Model loaded in {time.perf_counter() - t1:.1f}s")

                # Warmup run to eliminate cold-start penalty (lazy kernel compilation, memory pool init)
                logger.info("Running ASR warmup...")
                t2 = time.perf_counter()
                warmup_audio = np.zeros(SAMPLE_RATE, dtype=np.float32)  # 1 second of silence
                _ = self._model.recognize(warmup_audio, sample_rate=SAMPLE_RATE)
                logger.info(f"  ASR warmup complete in {time.perf_counter() - t2:.1f}s")

                self._loaded = True
                logger.info(f"Parakeet ASR ready (total: {time.perf_counter() - t0:.1f}s)")

            except Exception as e:
                logger.error(f"Failed to load Parakeet model: {e}")
                raise

    def transcribe(
        self,
        audio: Union[str, bytes, np.ndarray],
        language: str = "en",
        use_vad: bool = True,
    ) -> str:
        """
        Transcribe audio to text.

        Args:
            audio: Audio file path, bytes, or numpy array (16kHz expected for np.ndarray)
            language: Language code (default "en" for English) - not used by Parakeet
            use_vad: Enable VAD filter (not used - Parakeet handles this internally)

        Returns:
            Transcribed text
        """
        self._load_model()

        try:
            import time
            start_time = time.perf_counter()

            # Load and preprocess audio
            logger.info("Loading audio...")
            audio_samples = load_audio(audio)

            load_time = time.perf_counter() - start_time
            logger.info(f"Audio loaded in {load_time*1000:.0f}ms, duration: {len(audio_samples)/SAMPLE_RATE:.1f}s")

            # Transcribe with Parakeet
            logger.info("Transcribing with Parakeet TDT...")
            transcribe_start = time.perf_counter()

            # onnx-asr accepts numpy arrays directly with sample_rate parameter
            text = self._model.recognize(audio_samples, sample_rate=SAMPLE_RATE)

            transcribe_time = time.perf_counter() - transcribe_start
            total_time = time.perf_counter() - start_time

            logger.info(
                f"Transcription complete in {transcribe_time*1000:.0f}ms "
                f"(total: {total_time*1000:.0f}ms)"
            )

            return text.strip() if text else ""

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise
        

# Thread-safe singleton
_asr_engine: Optional[ASREngine] = None
_asr_engine_lock = threading.Lock()


def get_asr_engine() -> ASREngine:
    """Get the global ASR engine instance (thread-safe)."""
    global _asr_engine
    if _asr_engine is None:
        with _asr_engine_lock:
            if _asr_engine is None:
                _asr_engine = ASREngine()
    return _asr_engine
