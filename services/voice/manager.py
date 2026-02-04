"""
Voice Manager - orchestrates voice interaction for Friday.

Handles:
- Voice channel connection/disconnection
- Audio pipeline coordination
- ASR -> Friday -> TTS flow
- State management per guild

Performance optimizations:
- Dedicated thread pools for ASR, TTS, and LLM to avoid GIL contention
- Uses Parakeet TDT via onnx-asr for low-latency ASR
- Uses soxr for fast audio resampling
- Streaming LLM -> TTS pipeline for 1-3s time-to-first-audio
"""
import asyncio
import logging
import re
from typing import Optional, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from discord.ext import voice_recv
import discord

from ai import Friday
from ai.speech.asr import get_asr_engine
from ai.speech.tts import get_tts_engine, clean_text_for_tts
from .sink import FridayAudioSink
from .playback import create_tts_source
from .audio_pipeline import AudioBuffer, resample_discord_to_asr, resample_tts_to_discord

logger = logging.getLogger(__name__)

# Limit TTS length to avoid very long audio responses
MAX_TTS_CHARS = 1000

# Sentence boundary pattern for streaming TTS
SENTENCE_END_PATTERN = re.compile(r'[.!?]\s+|[.!?]$|\n')

# Minimum chars before attempting sentence split (avoid tiny chunks)
MIN_SENTENCE_CHARS = 20

@dataclass
class VoiceSession:
    """State for an active voice session in a guild."""

    guild_id: int
    voice_client: voice_recv.VoiceRecvClient
    text_channel: discord.TextChannel  # For status messages
    target_user_id: int
    audio_buffer: AudioBuffer
    listening: bool = True
    processing: bool = False  # True when processing speech
    conversation_history: list = field(default_factory=list)
    packet_gap_task: Optional[asyncio.Task] = field(default=None, repr=False)


class VoiceManager:
    """
    Manages voice interactions across guilds.

    One VoiceManager instance handles all voice sessions for the bot.
    """

    def __init__(self, friday: Friday):
        """
        Initialize the voice manager.

        Args:
            friday: The Friday orchestrator instance for processing messages
        """
        self.friday = friday
        self.sessions: Dict[int, VoiceSession] = {}  # guild_id -> VoiceSession
        self._asr = None  # Lazy load
        self._tts = None  # Lazy load
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None  # Main event loop reference

        # Dedicated thread pools for CPU-bound operations (prevents GIL contention)
        self._asr_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ASR")
        self._tts_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="TTS")
        self._llm_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="LLM")

    def _get_asr(self):
        """Lazy load ASR engine."""
        if self._asr is None:
            logger.info("Loading ASR (Parakeet TDT) engine...")
            self._asr = get_asr_engine()
            logger.info("ASR engine loaded successfully")
        return self._asr

    def _get_tts(self):
        """Lazy load TTS engine."""
        if self._tts is None:
            logger.info("Loading TTS (Kokoro) engine...")
            self._tts = get_tts_engine()
            logger.info("TTS engine loaded successfully")
        return self._tts

    async def join_channel(
        self,
        voice_channel: discord.VoiceChannel,
        text_channel: discord.TextChannel,
        user: discord.Member,
    ) -> bool:
        """
        Join a voice channel and start listening to a specific user.

        Args:
            voice_channel: The voice channel to join
            text_channel: Text channel for status updates
            user: The user to listen to (single user focus)

        Returns:
            True if successfully joined, False otherwise
        """
        guild_id = voice_channel.guild.id

        # Check if already in a voice channel in this guild
        if guild_id in self.sessions:
            await text_channel.send(
                "I'm already in a voice channel. Use `$leave` first."
            )
            return False

        try:
            # Store reference to the main event loop for thread-safe scheduling
            self._event_loop = asyncio.get_running_loop()

            # Connect using VoiceRecvClient
            voice_client: voice_recv.VoiceRecvClient = await voice_channel.connect(
                cls=voice_recv.VoiceRecvClient
            )

            # Create audio buffer with balanced silence detection
            # Allows natural pauses in speech while still being responsive
            audio_buffer = AudioBuffer(
                silence_threshold_db=-35,  # Less aggressive threshold
                silence_duration_ms=1000,  # 1 second silence triggers end
                min_speech_duration_ms=300,  # Minimum 300ms of speech
            )

            # Create session
            session = VoiceSession(
                guild_id=guild_id,
                voice_client=voice_client,
                text_channel=text_channel,
                target_user_id=user.id,
                audio_buffer=audio_buffer,
            )

            self.sessions[guild_id] = session

            # Create and start the audio sink
            sink = FridayAudioSink(
                target_user_id=user.id,
                on_audio_data=lambda data: self._handle_audio_frame(guild_id, data),
            )

            voice_client.listen(sink)

            # Start background task for packet gap detection (Discord VAD support)
            session.packet_gap_task = asyncio.create_task(
                self._packet_gap_checker(guild_id),
                name=f"packet-gap-{guild_id}"
            )

            logger.info(
                f"Joined voice channel {voice_channel.name} in guild {guild_id}"
            )
            await text_channel.send(
                f"Joined **{voice_channel.name}**! Listening to {user.display_name}.\n"
                f"Use `$voice` to toggle listening, `$leave` to disconnect."
            )

            return True

        except Exception as e:
            logger.error(f"Failed to join voice channel: {e}")
            await text_channel.send(f"Failed to join voice channel: {e}")
            return False

    async def leave_channel(self, guild_id: int) -> bool:
        """
        Leave the voice channel in a guild.

        Args:
            guild_id: The guild to leave voice in

        Returns:
            True if successfully left, False if not in voice
        """
        session = self.sessions.get(guild_id)

        if not session:
            return False

        try:
            # Cancel packet gap checker task
            if session.packet_gap_task and not session.packet_gap_task.done():
                session.packet_gap_task.cancel()
                try:
                    await session.packet_gap_task
                except asyncio.CancelledError:
                    pass
                logger.debug(f"Cancelled packet gap checker for guild {guild_id}")

            # Finalize any pending speech
            session.audio_buffer.force_finalize()

            # Stop listening and disconnect
            session.voice_client.stop_listening()
            await session.voice_client.disconnect()

            # Clean up session
            del self.sessions[guild_id]

            logger.info(f"Left voice channel in guild {guild_id}")
            return True

        except Exception as e:
            logger.error(f"Error leaving voice channel: {e}")
            # Force cleanup
            if guild_id in self.sessions:
                del self.sessions[guild_id]
            return False

    def toggle_listening(self, guild_id: int) -> Optional[bool]:
        """
        Toggle listening state for a guild.

        Args:
            guild_id: The guild to toggle

        Returns:
            New listening state, or None if not in voice
        """
        session = self.sessions.get(guild_id)

        if not session:
            return None

        session.listening = not session.listening

        if session.listening:
            logger.info(f"Resumed listening in guild {guild_id}")
        else:
            logger.info(f"Paused listening in guild {guild_id}")

        return session.listening

    async def _packet_gap_checker(self, guild_id: int):
        """
        Background task that checks for packet gaps (Discord VAD users).

        When Discord's client-side VAD stops sending packets (user stops speaking),
        this detects the gap and finalizes the speech buffer. This works as a
        complement to energy-based silence detection.

        Args:
            guild_id: The guild to monitor
        """
        logger.info(f"Started packet gap checker for guild {guild_id}")
        check_interval_ms = 100  # Check every 100ms

        try:
            while True:
                await asyncio.sleep(check_interval_ms / 1000)

                session = self.sessions.get(guild_id)
                if not session:
                    break

                # Skip if not listening or already processing
                if not session.listening or session.processing:
                    continue

                # Check for packet gap
                complete_audio = session.audio_buffer.check_packet_gap()

                if complete_audio:
                    # Schedule async processing on the main event loop
                    audio_duration_ms = len(complete_audio) / (48000 * 2 * 2) * 1000
                    logger.info(f"Packet gap triggered speech! Duration: {audio_duration_ms:.0f}ms")

                    asyncio.create_task(
                        self._process_speech(guild_id, complete_audio),
                        name=f"speech-{guild_id}"
                    )

        except asyncio.CancelledError:
            logger.debug(f"Packet gap checker cancelled for guild {guild_id}")
            raise
        except Exception as e:
            logger.error(f"Error in packet gap checker for guild {guild_id}: {e}")

    def _handle_audio_frame(self, guild_id: int, pcm_data: bytes):
        """
        Handle an incoming audio frame from the sink.

        This is called synchronously from the audio thread.
        Schedules async processing on the event loop.
        """
        session = self.sessions.get(guild_id)

        if not session:
            return

        # Skip if not listening or already processing
        if not session.listening or session.processing:
            return

        # Add frame to buffer, check if speech complete
        complete_audio = session.audio_buffer.add_frame(pcm_data)

        if complete_audio:
            # Schedule async processing on the main event loop (thread-safe)
            if self._event_loop is not None:
                audio_duration_ms = len(complete_audio) / (48000 * 2 * 2) * 1000  # 48kHz, stereo, 16-bit
                logger.info(f"Speech detected! Duration: {audio_duration_ms:.0f}ms, Size: {len(complete_audio)} bytes")
                # Use full orchestrator pipeline (handles SEARCH:, PAYCHECK: routing properly)
                # Streaming pipeline bypasses LangGraph and speaks intermediate responses
                asyncio.run_coroutine_threadsafe(
                    self._process_speech(guild_id, complete_audio),
                    self._event_loop
                )

    async def _process_speech(self, guild_id: int, audio_bytes: bytes):
        """
        Process completed speech through ASR -> Friday -> TTS pipeline.

        Args:
            guild_id: The guild this speech came from
            audio_bytes: Raw PCM audio bytes (Discord format)
        """
        session = self.sessions.get(guild_id)

        if not session:
            logger.warning(f"No session found for guild {guild_id}, skipping speech processing")
            return

        if not session.voice_client.is_connected():
            logger.warning("Voice client not connected, skipping speech processing")
            return

        session.processing = True
        logger.info(f"Starting speech processing pipeline for guild {guild_id}")

        try:
            # Get event loop for running blocking operations in dedicated executors
            loop = asyncio.get_running_loop()

            # 1. Resample audio for ASR (run in ASR executor to avoid blocking)
            logger.info("Resampling audio for ASR...")
            audio_for_asr = await loop.run_in_executor(
                self._asr_executor, resample_discord_to_asr, audio_bytes
            )

            # 2. Transcribe with ASR (run in ASR executor - Parakeet TDT)
            logger.info("Transcribing audio with Parakeet TDT...")
            text = await loop.run_in_executor(
                self._asr_executor, self._get_asr().transcribe, audio_for_asr
            )

            if not text or text.strip() == "":
                logger.info("Empty transcription, ignoring")
                return

            logger.info(f"Transcribed: {text}")

            # Send transcription to text channel
            await session.text_channel.send(f"**You said:** {text}")

            # 3. Process through Friday (run in LLM executor to avoid blocking)
            logger.info("Processing through Friday...")
            session.conversation_history.append({"role": "user", "content": text})

            def invoke_friday():
                return self.friday.app.invoke(
                    {
                        "messages": session.conversation_history,
                        "image_url": None,
                        "original_prompt": None,
                    }
                )

            result = await loop.run_in_executor(self._llm_executor, invoke_friday)

            session.conversation_history = result["messages"]
            response_text = result["messages"][-1]["content"]

            logger.info(f"Friday response: {response_text[:100]}...")

            # Send text response
            if len(response_text) > 2000:
                chunks = [
                    response_text[i : i + 2000]
                    for i in range(0, len(response_text), 2000)
                ]
                for chunk in chunks:
                    await session.text_channel.send(chunk)
            else:
                await session.text_channel.send(response_text)

            # 4. Clean and prepare text for TTS
            tts_text = clean_text_for_tts(response_text)
            if len(tts_text) > MAX_TTS_CHARS:
                tts_text = tts_text[:MAX_TTS_CHARS] + "..."
                logger.info(f"Truncated TTS text to {MAX_TTS_CHARS} chars")

            # 5. Synthesize TTS (run in TTS executor to avoid blocking event loop)
            logger.info(f"Synthesizing TTS response ({len(tts_text)} chars)...")
            samples, sample_rate = await loop.run_in_executor(
                self._tts_executor, self._get_tts().synthesize, tts_text
            )

            # 5. Play audio response - check if still connected
            if not session.voice_client.is_connected():
                logger.warning("Voice client disconnected before TTS playback, skipping audio")
                return

            logger.info("Playing TTS audio...")
            audio_source = create_tts_source(samples, sample_rate)

            # Wait for any current playback to finish
            while session.voice_client.is_connected() and session.voice_client.is_playing():
                await asyncio.sleep(0.1)

            # Final check before playing
            if not session.voice_client.is_connected():
                logger.warning("Voice client disconnected while waiting, skipping audio")
                return

            # Pause listening during playback to avoid opus decode errors from own audio
            session.listening = False
            logger.info("Paused listening during TTS playback")

            def on_playback_complete(error):
                if error:
                    logger.error(f"Playback error: {error}")
                # Re-enable listening after playback
                current_session = self.sessions.get(guild_id)
                if current_session:
                    current_session.listening = True
                    logger.info("Resumed listening after TTS playback")

            # Play the response
            session.voice_client.play(audio_source, after=on_playback_complete)

        except Exception as e:
            logger.error(f"Error processing speech: {e}")
            # Only try to send to text channel if it's a real error, not a disconnect
            if "not connected" not in str(e).lower():
                try:
                    await session.text_channel.send(f"Error processing speech: {e}")
                except Exception:
                    pass  # Channel may also be unavailable

        finally:
            # Session may have been removed during processing
            current_session = self.sessions.get(guild_id)
            if current_session:
                current_session.processing = False
            logger.info(f"Speech processing pipeline complete for guild {guild_id}")

    def _extract_sentence(self, buffer: str) -> tuple[Optional[str], str]:
        """
        Extract a complete sentence from the buffer if available.

        Args:
            buffer: Accumulated text from LLM stream

        Returns:
            Tuple of (sentence, remaining_buffer) or (None, buffer) if no complete sentence
        """
        if len(buffer) < MIN_SENTENCE_CHARS:
            return None, buffer

        match = SENTENCE_END_PATTERN.search(buffer)
        if match:
            # Found sentence boundary
            end_pos = match.end()
            sentence = buffer[:end_pos].strip()
            remaining = buffer[end_pos:]
            return sentence, remaining

        return None, buffer

    def get_session(self, guild_id: int) -> Optional[VoiceSession]:
        """Get the voice session for a guild."""
        return self.sessions.get(guild_id)

    async def cleanup_all(self):
        """Clean up all voice sessions and thread pools (for shutdown)."""
        # Cancel all packet gap checker tasks first
        for session in self.sessions.values():
            if session.packet_gap_task and not session.packet_gap_task.done():
                session.packet_gap_task.cancel()

        # Leave all voice channels
        for guild_id in list(self.sessions.keys()):
            await self.leave_channel(guild_id)

        # Shutdown thread pools
        self._asr_executor.shutdown(wait=False)
        self._tts_executor.shutdown(wait=False)
        self._llm_executor.shutdown(wait=False)
        logger.info("Voice manager thread pools shut down")
