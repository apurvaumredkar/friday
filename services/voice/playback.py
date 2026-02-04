"""
Audio playback for Discord voice channels.

Converts TTS output to Discord-compatible audio format.
Supports both batch and streaming playback modes.
"""
import io
import logging
import threading
from collections import deque
import discord
import numpy as np

logger = logging.getLogger(__name__)


class TTSAudioSource(discord.AudioSource):
    """
    Audio source that plays TTS-generated audio.

    Wraps TTS numpy array output and streams it as PCM audio
    compatible with Discord voice channels.
    """

    # Discord expects 48kHz stereo s16le, 20ms frames = 3840 bytes
    FRAME_SIZE = 3840

    def __init__(self, pcm_data: bytes):
        """
        Initialize with pre-converted PCM data.

        Args:
            pcm_data: PCM audio bytes (48kHz stereo s16le)
        """
        self._buffer = io.BytesIO(pcm_data)
        self._finished = False

    def read(self) -> bytes:
        """Read next 20ms frame of audio."""
        data = self._buffer.read(self.FRAME_SIZE)

        if len(data) == 0:
            self._finished = True
            return b""

        # Pad with silence if last frame is incomplete
        if len(data) < self.FRAME_SIZE:
            data = data + b"\x00" * (self.FRAME_SIZE - len(data))

        return data

    def is_opus(self) -> bool:
        """We provide raw PCM, not opus."""
        return False

    def cleanup(self):
        """Clean up resources."""
        self._buffer.close()

    @property
    def finished(self) -> bool:
        return self._finished


class StreamingTTSSource(discord.AudioSource):
    """
    Audio source that plays chunks as they arrive from streaming TTS.

    Enables sentence-by-sentence playback while LLM is still generating,
    dramatically reducing perceived latency (time-to-first-audio).

    Thread-safe: add_chunk() can be called from any thread while
    Discord's voice thread calls read().
    """

    # Discord expects 48kHz stereo s16le, 20ms frames = 3840 bytes
    FRAME_SIZE = 3840

    def __init__(self):
        """Initialize empty streaming source."""
        self._buffer: deque[bytes] = deque()
        self._lock = threading.Lock()
        self._finished = False
        self._started = False
        self._partial_frame = b""  # Buffer for incomplete frames

    def add_chunk(self, pcm_chunk: bytes):
        """
        Add synthesized audio chunk to playback queue.

        Thread-safe: can be called from TTS executor thread.

        Args:
            pcm_chunk: PCM audio bytes (48kHz stereo s16le)
        """
        with self._lock:
            # Combine with any partial frame from previous chunk
            data = self._partial_frame + pcm_chunk
            self._partial_frame = b""

            # Split into Discord frame-sized chunks
            offset = 0
            while offset + self.FRAME_SIZE <= len(data):
                self._buffer.append(data[offset:offset + self.FRAME_SIZE])
                offset += self.FRAME_SIZE

            # Save any remaining partial frame
            if offset < len(data):
                self._partial_frame = data[offset:]

            self._started = True

    def read(self) -> bytes:
        """
        Read next 20ms frame of audio.

        Called by Discord every 20ms from voice thread.

        Returns:
            Audio frame bytes, silence if waiting, empty if finished
        """
        with self._lock:
            if self._buffer:
                return self._buffer.popleft()
            elif self._finished:
                # Flush any remaining partial frame with padding
                if self._partial_frame:
                    frame = self._partial_frame + b"\x00" * (self.FRAME_SIZE - len(self._partial_frame))
                    self._partial_frame = b""
                    return frame
                return b""  # Signal end of audio
            else:
                # Return silence while waiting for more chunks
                return b"\x00" * self.FRAME_SIZE

    def mark_finished(self):
        """
        Mark stream as complete - no more chunks will be added.

        Call this after the last chunk has been added.
        """
        with self._lock:
            self._finished = True
            logger.info("Streaming TTS source marked as finished")

    def is_opus(self) -> bool:
        """We provide raw PCM, not opus."""
        return False

    def cleanup(self):
        """Clean up resources."""
        with self._lock:
            self._buffer.clear()
            self._partial_frame = b""

    @property
    def is_streaming(self) -> bool:
        """Check if stream has started receiving data."""
        with self._lock:
            return self._started

    @property
    def buffer_size(self) -> int:
        """Get current buffer size in frames."""
        with self._lock:
            return len(self._buffer)


def create_tts_source(samples: np.ndarray, sample_rate: int) -> TTSAudioSource:
    """
    Create a Discord audio source from TTS output.

    Args:
        samples: TTS audio samples (numpy array)
        sample_rate: Sample rate of TTS output

    Returns:
        TTSAudioSource ready for voice_client.play()
    """
    from .audio_pipeline import resample_tts_to_discord

    pcm_data = resample_tts_to_discord(samples, sample_rate)
    logger.info(f"Created TTS source: {len(pcm_data)} bytes")

    return TTSAudioSource(pcm_data)
