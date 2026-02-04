"""
Custom AudioSink for receiving Discord voice audio.

Implements the discord-ext-voice-recv AudioSink interface.
"""
import logging
from typing import Optional, Callable
from discord.ext import voice_recv
from discord import User, Member

logger = logging.getLogger(__name__)


class FridayAudioSink(voice_recv.AudioSink):
    """
    Audio sink that filters for a specific user and forwards to the audio pipeline.

    This sink:
    1. Receives decoded PCM audio (not opus)
    2. Filters to only process audio from the focused user
    3. Forwards PCM data to the AudioBuffer for silence detection
    """

    def __init__(
        self,
        target_user_id: int,
        on_audio_data: Callable[[bytes], None],
    ):
        """
        Initialize the audio sink.

        Args:
            target_user_id: Discord user ID to listen to (single user focus)
            on_audio_data: Callback for each audio frame
        """
        super().__init__()
        self.target_user_id = target_user_id
        self.on_audio_data = on_audio_data
        self._packet_count = 0
        self._log_interval = 100  # Log every N packets
        logger.info(f"FridayAudioSink initialized, listening to user {target_user_id}")

    def wants_opus(self) -> bool:
        """We want decoded PCM, not raw opus."""
        return False

    def write(self, user: Optional[User | Member], data: voice_recv.VoiceData):
        """
        Process incoming audio data.

        Called for each audio packet received. The data contains PCM
        audio at 48kHz stereo, 16-bit signed little-endian.

        Args:
            user: The user who sent this audio (may be None if unknown)
            data: VoiceData container with .pcm attribute
        """
        # Filter: only process audio from target user
        if user is None or user.id != self.target_user_id:
            return

        # Extract PCM data
        pcm_data = data.pcm

        if pcm_data:
            self._packet_count += 1
            if self._packet_count == 1:
                logger.info(f"Receiving voice packets from user {user.id}")
            elif self._packet_count % self._log_interval == 0:
                logger.debug(f"Received {self._packet_count} voice packets")
            self.on_audio_data(pcm_data)

    def cleanup(self):
        """Clean up resources when sink is stopped."""
        logger.info("FridayAudioSink cleanup")
