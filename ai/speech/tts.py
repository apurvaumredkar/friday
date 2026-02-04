"""
Text-to-Speech engine using Kokoro 82M ONNX model with GPU acceleration.

Thread-safe singleton pattern for concurrent voice processing.
Uses CUDA for 3-5x faster synthesis when available.
"""
import logging
import os
import re
import threading
import numpy as np
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Model paths - models are in ai/models/
MODELS_DIR = Path(__file__).parent.parent / "models"
MODEL_PATH = MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx"
VOICES_PATH = MODELS_DIR / "kokoro" / "voices-v1.0.bin"

# CUDA library paths (installed via pip nvidia-* packages)
VENV_SITE_PACKAGES = Path(__file__).parent.parent.parent / ".venv" / "lib" / "python3.12" / "site-packages"
CUDA_LIB_PATHS = [
    VENV_SITE_PACKAGES / "nvidia" / "cublas" / "lib",
    VENV_SITE_PACKAGES / "nvidia" / "cuda_runtime" / "lib",
    VENV_SITE_PACKAGES / "nvidia" / "cudnn" / "lib",
    VENV_SITE_PACKAGES / "nvidia" / "cufft" / "lib",
    VENV_SITE_PACKAGES / "nvidia" / "curand" / "lib",
    VENV_SITE_PACKAGES / "nvidia" / "cuda_nvrtc" / "lib",
    Path("/usr/lib/wsl/lib"),  # WSL2 CUDA driver
]

# Available voices
VOICES = {
    "af_heart": "American Female (warm, friendly)",
    "af_bella": "American Female (bella)",
    "am_adam": "American Male (adam)",
    "bf_emma": "British Female (emma)",
    "bm_george": "British Male (george)",
}

DEFAULT_VOICE = "af_heart"

# Emoji pattern for TTS text cleaning
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # misc
    "]+",
    flags=re.UNICODE
)


def clean_text_for_tts(text: str) -> str:
    """
    Clean text for TTS output by removing markdown formatting, URLs, and emojis.

    This ensures text sounds natural when spoken, removing:
    - Emojis
    - Markdown formatting (bold, italic, headers, links, code blocks)
    - Plain URLs (http://, https://)
    - Bullet points and numbered lists
    - Extra whitespace and punctuation

    Args:
        text: Raw response text from LLM

    Returns:
        Cleaned text suitable for speech synthesis
    """
    # Remove emojis
    text = EMOJI_PATTERN.sub('', text)

    # Remove markdown bold/italic (**, *, __, _)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)      # *italic*
    text = re.sub(r'__(.+?)__', r'\1', text)      # __bold__
    text = re.sub(r'_(.+?)_', r'\1', text)        # _italic_

    # Remove markdown headers (# ## ###)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Remove markdown links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)

    # Remove plain URLs (http://, https://)
    text = re.sub(r'https?://\S+', '', text)

    # Remove markdown code blocks and inline code
    text = re.sub(r'```[\s\S]*?```', '', text)    # code blocks
    text = re.sub(r'`([^`]+)`', r'\1', text)      # inline code

    # Remove bullet points
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)

    # Remove numbered lists (1. 2. etc)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # Convert line breaks to periods (creates natural pauses in speech)
    text = re.sub(r'\n+', '. ', text)

    # Clean up multiple periods or spaces
    text = re.sub(r'\.+', '.', text)              # multiple periods -> single
    text = re.sub(r'\.\s*\.', '.', text)          # ". ." -> "."
    text = re.sub(r'  +', ' ', text)              # multiple spaces
    text = text.strip()

    return text


def _setup_cuda_paths():
    """Set up LD_LIBRARY_PATH and preload CUDA libraries."""
    import ctypes

    # Set LD_LIBRARY_PATH for any subprocess
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    cuda_paths = ":".join(str(p) for p in CUDA_LIB_PATHS if p.exists())
    if cuda_paths:
        os.environ["LD_LIBRARY_PATH"] = f"{cuda_paths}:{existing}" if existing else cuda_paths

    # Preload CUDA libraries using ctypes (must happen before onnxruntime import)
    # Order matters - load base libraries first, then dependent ones
    cuda_libs = [
        "libcudart.so.12",
        "libcublas.so.12",
        "libcublasLt.so.12",
        "libcufft.so.11",
        "libcurand.so.10",
        "libnvrtc.so.12",
        # cuDNN libraries (must be loaded in order)
        "libcudnn.so.9",
        "libcudnn_ops.so.9",
        "libcudnn_cnn.so.9",
        "libcudnn_adv.so.9",
        "libcudnn_graph.so.9",
        "libcudnn_engines_precompiled.so.9",
        "libcudnn_engines_runtime_compiled.so.9",
        "libcudnn_heuristic.so.9",
    ]

    for lib_path in CUDA_LIB_PATHS:
        if not lib_path.exists():
            continue
        for lib_name in cuda_libs:
            lib_file = lib_path / lib_name
            if lib_file.exists():
                try:
                    ctypes.CDLL(str(lib_file), mode=ctypes.RTLD_GLOBAL)
                    logger.debug(f"Preloaded {lib_file}")
                except OSError as e:
                    logger.debug(f"Could not preload {lib_file}: {e}")


class TTSEngine:
    """Text-to-Speech engine using Kokoro ONNX model with GPU acceleration.

    Thread-safe with double-checked locking for model initialization.
    Uses CUDA when available for 3-5x faster synthesis.
    """

    def __init__(self):
        self._kokoro = None
        self._loaded = False
        self._load_lock = threading.Lock()
        self._use_gpu = False

    def _load_model(self):
        """Lazy load the Kokoro model with GPU acceleration if available."""
        if self._loaded:
            return

        with self._load_lock:
            if self._loaded:  # Double-check after acquiring lock
                return

            try:
                import time
                t0 = time.perf_counter()

                # Set up CUDA paths before importing onnxruntime
                _setup_cuda_paths()

                import onnxruntime as ort
                from kokoro_onnx import Kokoro

                # Check for GPU availability
                available_providers = ort.get_available_providers()
                logger.info(f"Available ONNX providers: {available_providers}")

                if "CUDAExecutionProvider" in available_providers:
                    # Create GPU-accelerated session
                    logger.info("Creating Kokoro TTS with CUDA acceleration...")
                    sess_options = ort.SessionOptions()
                    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                    session = ort.InferenceSession(
                        str(MODEL_PATH),
                        sess_options,
                        providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
                    )

                    # Verify CUDA is actually being used
                    active_providers = session.get_providers()
                    if "CUDAExecutionProvider" in active_providers:
                        self._use_gpu = True
                        logger.info("CUDA provider active - GPU acceleration enabled")
                    else:
                        logger.warning("CUDA provider requested but not active, falling back to CPU")

                    self._kokoro = Kokoro.from_session(session, str(VOICES_PATH))
                else:
                    # Fall back to CPU
                    logger.info("CUDA not available, using CPU for TTS")
                    self._kokoro = Kokoro(str(MODEL_PATH), str(VOICES_PATH))

                load_time = time.perf_counter() - t0
                logger.info(f"Kokoro TTS loaded in {load_time:.1f}s (GPU: {self._use_gpu})")

                # Warmup synthesis to avoid cold start penalty
                logger.info("Running TTS warmup...")
                t1 = time.perf_counter()
                _ = self._kokoro.create("Hello", voice=DEFAULT_VOICE, speed=1.0)
                warmup_time = time.perf_counter() - t1
                logger.info(f"TTS warmup complete in {warmup_time:.1f}s")

                self._loaded = True
                logger.info(f"Kokoro TTS ready (total: {time.perf_counter() - t0:.1f}s)")

            except Exception as e:
                logger.error(f"Failed to load Kokoro model: {e}")
                raise

    def synthesize(
        self,
        text: str,
        voice: str = DEFAULT_VOICE,
        speed: float = 1.0,
    ) -> tuple[np.ndarray, int]:
        """
        Synthesize speech from text.

        Args:
            text: Text to synthesize
            voice: Voice ID (e.g., "af_heart", "am_adam")
            speed: Speech speed multiplier (default 1.0)

        Returns:
            Tuple of (audio_samples, sample_rate)
        """
        self._load_model()

        if voice not in VOICES:
            logger.warning(f"Unknown voice '{voice}', using default '{DEFAULT_VOICE}'")
            voice = DEFAULT_VOICE

        try:
            import time
            start = time.perf_counter()

            # Kokoro returns (samples, sample_rate)
            samples, sample_rate = self._kokoro.create(
                text,
                voice=voice,
                speed=speed,
            )

            synth_time = time.perf_counter() - start
            duration = len(samples) / sample_rate
            rtf = synth_time / duration if duration > 0 else 0

            logger.info(
                f"TTS: {len(text)} chars -> {duration:.1f}s audio in {synth_time*1000:.0f}ms "
                f"(RTF: {rtf:.2f}x, GPU: {self._use_gpu})"
            )
            return samples, sample_rate

        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            raise

    def get_available_voices(self) -> dict[str, str]:
        """Get available voice options."""
        return VOICES.copy()

    @property
    def is_gpu_enabled(self) -> bool:
        """Check if GPU acceleration is enabled."""
        return self._use_gpu


# Thread-safe singleton
_tts_engine: Optional[TTSEngine] = None
_tts_engine_lock = threading.Lock()


def get_tts_engine() -> TTSEngine:
    """Get the global TTS engine instance (thread-safe)."""
    global _tts_engine
    if _tts_engine is None:
        with _tts_engine_lock:
            if _tts_engine is None:
                _tts_engine = TTSEngine()
    return _tts_engine
