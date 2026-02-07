"""
Audio processing pipeline for voice integration.

Handles:
- Buffering incoming 20ms frames from Discord
- Voice activity detection (energy-based RMS threshold)
- Resampling from 48kHz stereo to 16kHz mono for ASR
- Resampling from TTS output to 48kHz stereo for Discord
"""
import numpy as np
from collections import deque
from typing import Optional, Callable
import logging

logger = logging.getLogger(__name__)

# Discord audio format
DISCORD_SAMPLE_RATE = 48000
DISCORD_CHANNELS = 2
DISCORD_FRAME_MS = 20
DISCORD_FRAME_SAMPLES = int(DISCORD_SAMPLE_RATE * DISCORD_FRAME_MS / 1000)  # 960

# ASR expected format
ASR_SAMPLE_RATE = 16000

# Silence detection parameters
# Tuned for natural conversation with pauses
SILENCE_THRESHOLD_DB = -35  # dB threshold (less aggressive, allows quieter speech)
SILENCE_DURATION_MS = 1000  # 1 second of silence triggers end-of-speech
MIN_SPEECH_DURATION_MS = 300  # Minimum 300ms of speech to consider valid
MAX_SPEECH_DURATION_MS = 30000  # Maximum 30 seconds to prevent runaway recording

# Packet gap detection (for Discord VAD users)
# If no packets arrive for this duration while speaking, assume speech ended
PACKET_GAP_MS = 500  # 500ms without packets = end of speech


class AudioBuffer:
    """
    Buffers incoming audio frames and detects end-of-speech.

    Uses energy-based RMS dB threshold for silence detection, with:
    - Hangover mechanism to allow natural pauses
    - Maximum duration limit to prevent runaway recordings
    - Packet gap detection for Discord VAD users
    """

    def __init__(
        self,
        silence_threshold_db: float = SILENCE_THRESHOLD_DB,
        silence_duration_ms: int = SILENCE_DURATION_MS,
        min_speech_duration_ms: int = MIN_SPEECH_DURATION_MS,
        max_speech_duration_ms: int = MAX_SPEECH_DURATION_MS,
        packet_gap_ms: int = PACKET_GAP_MS,
        on_speech_complete: Optional[Callable[[bytes], None]] = None,
    ):
        self.silence_threshold_db = silence_threshold_db
        self.silence_duration_ms = silence_duration_ms
        self.min_speech_duration_ms = min_speech_duration_ms
        self.max_speech_duration_ms = max_speech_duration_ms
        self.packet_gap_ms = packet_gap_ms
        self.on_speech_complete = on_speech_complete

        self._frames: deque[bytes] = deque()
        self._is_speaking = False
        self._silence_frames = 0
        self._speech_frames = 0

        # Calculate frame counts from ms
        self._silence_frame_threshold = int(silence_duration_ms / DISCORD_FRAME_MS)
        self._min_speech_frames = int(min_speech_duration_ms / DISCORD_FRAME_MS)
        self._max_speech_frames = int(max_speech_duration_ms / DISCORD_FRAME_MS)

        # For packet gap detection
        import time
        self._last_packet_time = time.monotonic()

        # For debug logging
        self._last_db = -100.0

    def add_frame(self, pcm_data: bytes) -> Optional[bytes]:
        """
        Add a 20ms PCM frame to the buffer.

        Returns complete audio bytes when speech ends, None otherwise.
        """
        import time
        self._last_packet_time = time.monotonic()

        is_silent, db = self._check_silence(pcm_data)
        self._last_db = db

        if not self._is_speaking:
            if not is_silent:
                # Speech started
                self._is_speaking = True
                self._frames.append(pcm_data)
                self._speech_frames = 1
                self._silence_frames = 0
                logger.info(f"Voice activity detected ({db:.0f}dB), recording speech...")
        else:
            self._frames.append(pcm_data)

            if is_silent:
                self._silence_frames += 1

                # Check if silence duration exceeded
                if self._silence_frames >= self._silence_frame_threshold:
                    silence_ms = self._silence_frames * DISCORD_FRAME_MS
                    logger.debug(f"Silence threshold reached ({silence_ms}ms)")
                    return self._finalize_speech()
            else:
                # Speech detected - reset silence counter
                self._silence_frames = 0
                self._speech_frames += 1

            # Check maximum duration
            if self._speech_frames >= self._max_speech_frames:
                logger.warning(f"Max speech duration reached ({self.max_speech_duration_ms}ms)")
                return self._finalize_speech()

        return None

    def _check_silence(self, pcm_data: bytes) -> tuple[bool, float]:
        """
        Check if a PCM frame is silent based on energy threshold.

        Returns:
            Tuple of (is_silent, db_level)
        """
        audio = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)

        if len(audio) == 0:
            return True, -100.0

        # Calculate RMS energy
        rms = np.sqrt(np.mean(audio**2))

        if rms < 1:  # Avoid log(0)
            return True, -100.0

        db = 20 * np.log10(rms / 32768)  # Normalize to 16-bit max
        is_silent = db < self.silence_threshold_db

        return is_silent, db

    def _finalize_speech(self) -> Optional[bytes]:
        """Finalize and return buffered speech, reset state."""
        total_frames = len(self._frames)
        speech_ms = self._speech_frames * DISCORD_FRAME_MS

        if self._speech_frames < self._min_speech_frames:
            logger.debug(f"Discarding short speech: {speech_ms}ms ({self._speech_frames} frames)")
            self._reset()
            return None

        # Trim trailing silence frames from the buffer
        # Keep only a small amount of trailing silence for natural sound
        trailing_silence_to_keep = 5  # 100ms
        frames_to_remove = max(0, self._silence_frames - trailing_silence_to_keep)

        for _ in range(frames_to_remove):
            if self._frames:
                self._frames.pop()

        # Concatenate all frames
        audio_bytes = b"".join(self._frames)
        duration_ms = len(self._frames) * DISCORD_FRAME_MS

        logger.info(
            f"Speech complete: {duration_ms}ms ({len(self._frames)} frames), "
            f"speech: {speech_ms}ms, {len(audio_bytes)} bytes"
        )

        self._reset()

        if self.on_speech_complete:
            self.on_speech_complete(audio_bytes)

        return audio_bytes

    def _reset(self):
        """Reset buffer state."""
        self._frames.clear()
        self._is_speaking = False
        self._silence_frames = 0
        self._speech_frames = 0

    def force_finalize(self) -> Optional[bytes]:
        """Force finalization of current buffer (e.g., on disconnect)."""
        if self._frames:
            return self._finalize_speech()
        return None

    def check_packet_gap(self) -> Optional[bytes]:
        """
        Check if there's been a gap in packet arrival (Discord VAD ended).

        This should be called periodically (e.g., every 100ms) to detect
        when Discord's client-side VAD has stopped sending packets.

        Returns:
            Complete audio bytes if packet gap detected and speech was active,
            None otherwise.
        """
        if not self._is_speaking:
            return None

        import time
        elapsed_ms = (time.monotonic() - self._last_packet_time) * 1000

        if elapsed_ms >= self.packet_gap_ms:
            logger.info(f"Packet gap detected ({elapsed_ms:.0f}ms), finalizing speech")
            return self._finalize_speech()

        return None

    def get_status(self) -> dict:
        """Get current buffer status for debugging."""
        return {
            "is_speaking": self._is_speaking,
            "speech_frames": self._speech_frames,
            "silence_frames": self._silence_frames,
            "buffered_frames": len(self._frames),
            "last_db": self._last_db,
            "speech_ms": self._speech_frames * DISCORD_FRAME_MS,
            "silence_ms": self._silence_frames * DISCORD_FRAME_MS,
        }


def resample_discord_to_asr(pcm_bytes: bytes) -> np.ndarray:
    """
    Convert Discord PCM (48kHz stereo s16le) to ASR format (16kHz mono float32).

    Uses soxr for high-quality, fast polyphase resampling (10-20x faster than scipy FFT).

    Args:
        pcm_bytes: Raw PCM bytes from Discord

    Returns:
        numpy array of float32 samples at 16kHz mono
    """
    import soxr

    # Convert bytes to numpy array (16-bit signed integers)
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

    # Convert stereo to mono (average channels)
    if len(audio) % 2 == 0:
        audio = audio.reshape(-1, 2).mean(axis=1)

    # Normalize to [-1, 1]
    audio = audio / 32768.0

    # Resample from 48kHz to 16kHz using soxr (polyphase filtering - much faster than FFT)
    audio_resampled = soxr.resample(audio, DISCORD_SAMPLE_RATE, ASR_SAMPLE_RATE, quality='HQ')

    duration_sec = len(audio_resampled) / ASR_SAMPLE_RATE
    logger.debug(f"Resampled {len(pcm_bytes)} bytes to {len(audio_resampled)} samples ({duration_sec:.2f}s)")

    return audio_resampled.astype(np.float32)


def resample_tts_to_discord(samples: np.ndarray, sample_rate: int) -> bytes:
    """
    Convert TTS output to Discord PCM format (48kHz stereo s16le).

    Uses soxr for high-quality, fast polyphase resampling.

    Args:
        samples: TTS audio samples (float32, any sample rate)
        sample_rate: Sample rate of TTS output

    Returns:
        PCM bytes ready for Discord playback
    """
    import soxr

    # Ensure float32
    audio = samples.astype(np.float32)

    # Normalize if needed
    max_val = np.abs(audio).max()
    if max_val > 1.0:
        audio = audio / max_val

    # Resample to 48kHz if needed using soxr (polyphase filtering - much faster than FFT)
    if sample_rate != DISCORD_SAMPLE_RATE:
        audio = soxr.resample(audio, sample_rate, DISCORD_SAMPLE_RATE, quality='HQ')

    # Convert mono to stereo (duplicate channel)
    stereo = np.column_stack([audio, audio])

    # Convert to 16-bit PCM
    pcm = (stereo * 32767).astype(np.int16)

    return pcm.tobytes()
