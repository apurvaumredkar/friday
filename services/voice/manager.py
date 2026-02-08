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
import threading
from typing import Optional, Dict
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from discord.ext import voice_recv
from discord.ext.voice_recv.router import PacketRouter
import discord

from ai import Friday
from ai.agents.root_agent import root_agent_stream, TOOL_CALL_SENTINEL


# Monkey-patch PacketRouter._do_run to survive corrupted opus packets.
# The upstream library crashes the entire router on a single bad packet.
_original_do_run = PacketRouter._do_run


def _patched_do_run(self):
    while not self._end_thread.is_set():
        self.waiter.wait()
        with self._lock:
            for decoder in self.waiter.items:
                try:
                    data = decoder.pop_data()
                except Exception:
                    continue
                if data is not None:
                    self.sink.write(data.source, data)


PacketRouter._do_run = _patched_do_run
from ai.speech.asr import get_asr_engine
from ai.speech.tts import get_tts_engine, clean_text_for_tts
from .sink import FridayAudioSink
from .playback import create_tts_source, StreamingTTSSource
from .audio_pipeline import AudioBuffer, resample_discord_to_asr, resample_tts_to_discord
from .transcript_filter import is_valid_transcript

logger = logging.getLogger(__name__)

# Limit TTS length to avoid very long audio responses
MAX_TTS_CHARS = 1000

# Cap voice conversation history to prevent unbounded memory growth
MAX_VOICE_HISTORY = 30

# Sentence boundary pattern for streaming TTS
SENTENCE_END_PATTERN = re.compile(r'[.!?]\s+|[.!?]$|\n')

# Minimum chars before attempting sentence split (avoid tiny chunks)
MIN_SENTENCE_CHARS = 20

# Lower threshold for first sentence to reduce time-to-first-audio
MIN_FIRST_SENTENCE_CHARS = 10


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
    processing_lock: threading.Lock = field(default_factory=threading.Lock)
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

            audio_buffer = AudioBuffer(
                silence_threshold_db=-35,
                silence_duration_ms=1000,
                min_speech_duration_ms=300,
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

                if not session.listening:
                    continue

                complete_audio = session.audio_buffer.check_packet_gap()

                if complete_audio:
                    with session.processing_lock:
                        if session.processing:
                            continue
                        session.processing = True

                    audio_duration_ms = len(complete_audio) / (48000 * 2 * 2) * 1000
                    logger.info(f"Packet gap triggered speech! Duration: {audio_duration_ms:.0f}ms")

                    asyncio.create_task(
                        self._process_speech_streaming(guild_id, complete_audio),
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
        if not session.listening:
            return

        complete_audio = session.audio_buffer.add_frame(pcm_data)

        if complete_audio:
            with session.processing_lock:
                if session.processing:
                    return
                session.processing = True
            # Schedule async processing on the main event loop (thread-safe)
            if self._event_loop is not None:
                audio_duration_ms = len(complete_audio) / (48000 * 2 * 2) * 1000  # 48kHz, stereo, 16-bit
                logger.info(f"Speech detected! Duration: {audio_duration_ms:.0f}ms, Size: {len(complete_audio)} bytes")
                asyncio.run_coroutine_threadsafe(
                    self._process_speech_streaming(guild_id, complete_audio),
                    self._event_loop
                )

    async def _process_speech_streaming(self, guild_id: int, audio_bytes: bytes):
        """
        Process speech through ASR -> streaming LLM -> streaming TTS pipeline.

        Falls back to batch LangGraph invoke if a tool call is detected
        (TOOL_CALL_SENTINEL), since tool calls need the full agent graph.
        """
        session = self.sessions.get(guild_id)
        if not session:
            logger.warning(f"No session found for guild {guild_id}, skipping speech processing")
            return

        if not session.voice_client.is_connected():
            logger.warning("Voice client not connected, skipping speech processing")
            return

        session.processing = True
        logger.info(f"[STREAMING] Starting speech processing pipeline for guild {guild_id}")

        try:
            loop = asyncio.get_running_loop()

            # 1. Resample audio for ASR
            audio_for_asr = await loop.run_in_executor(
                self._asr_executor, resample_discord_to_asr, audio_bytes
            )

            # 2. Transcribe with ASR
            logger.info("[STREAMING] Transcribing audio...")
            text = await loop.run_in_executor(
                self._asr_executor, self._get_asr().transcribe, audio_for_asr
            )

            if not text or text.strip() == "":
                logger.info("[STREAMING] Empty transcription, ignoring")
                return

            # Filter out noise-induced hallucinations before processing
            is_valid, reason = is_valid_transcript(text)
            if not is_valid:
                logger.info(f"[STREAMING] Rejected transcript: '{text}' — {reason}")
                return

            logger.info(f"[STREAMING] Transcribed: {text}")
            await session.text_channel.send(f"**You said:** {text}")

            # Add to conversation history (tag with [VOICE] so LLM applies voice mode rules)
            session.conversation_history.append({"role": "user", "content": f"[VOICE] {text}"})

            # 3. Stream LLM -> TTS
            response_text, fell_back = await self._stream_llm_with_tts(guild_id, session, loop)

            if response_text:
                # Update conversation history
                session.conversation_history.append({"role": "assistant", "content": response_text})
                session.conversation_history = session.conversation_history[-MAX_VOICE_HISTORY:]

                # Send text response to channel
                if len(response_text) > 2000:
                    for i in range(0, len(response_text), 2000):
                        await session.text_channel.send(response_text[i:i + 2000])
                else:
                    await session.text_channel.send(response_text)

        except Exception as e:
            logger.error(f"[STREAMING] Error processing speech: {e}")
            if "not connected" not in str(e).lower():
                try:
                    await session.text_channel.send(f"Error processing speech: {e}")
                except Exception:
                    pass

        finally:
            current_session = self.sessions.get(guild_id)
            if current_session:
                current_session.processing = False
            logger.info(f"[STREAMING] Speech processing complete for guild {guild_id}")

    async def _stream_llm_with_tts(self, guild_id: int, session: VoiceSession, loop: asyncio.AbstractEventLoop) -> tuple[str, bool]:
        """
        Stream LLM tokens, extract sentences, synthesize and play incrementally.

        Returns:
            Tuple of (full_response_text, fell_back_to_batch)
        """
        token_queue: asyncio.Queue[str | None] = asyncio.Queue()

        # Run the streaming LLM call in the LLM executor, bridging tokens via queue
        def stream_tokens():
            try:
                for token in root_agent_stream(session.conversation_history):
                    asyncio.run_coroutine_threadsafe(token_queue.put(token), loop)
            except Exception as e:
                logger.error(f"[STREAMING] LLM stream error: {e}")
            finally:
                asyncio.run_coroutine_threadsafe(token_queue.put(None), loop)

        llm_future = loop.run_in_executor(self._llm_executor, stream_tokens)

        # Phase 1: Check first token for tool call sentinel
        # If the model returns a tool call, root_agent_stream yields TOOL_CALL_SENTINEL
        # instead of content tokens — fall back to batch LangGraph invoke
        first_token = await token_queue.get()

        if first_token is None:
            logger.info("[STREAMING] Empty response from LLM")
            return "", False

        if first_token == TOOL_CALL_SENTINEL:
            logger.info("[STREAMING] Tool call detected, falling back to batch LangGraph invoke")
            # Drain remaining stream tokens
            while True:
                token = await token_queue.get()
                if token is None:
                    break

            # Fall back to full LangGraph invoke (includes tool execution)
            def invoke_friday():
                return self.friday.app.invoke({
                    "messages": session.conversation_history,
                    "original_prompt": None,
                })

            result = await loop.run_in_executor(self._llm_executor, invoke_friday)
            session.conversation_history = result["messages"][-MAX_VOICE_HISTORY:]
            response_text = result["messages"][-1]["content"]
            logger.info(f"[STREAMING] Batch fallback response: {response_text[:100]}...")

            # Use batch TTS for fallback response
            await self._batch_tts_and_play(guild_id, session, response_text)
            return response_text, True

        # Phase 2: Streaming TTS — direct text response, sentence-by-sentence
        accumulated = first_token
        logger.info("[STREAMING] Text response, starting streaming TTS pipeline")
        streaming_source = StreamingTTSSource()
        sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
        full_response = accumulated
        text_buffer = accumulated
        is_first_sentence = True
        tts_chars_fed = 0

        # TTS consumer task — processes sentences sequentially
        async def tts_consumer():
            nonlocal tts_chars_fed
            playback_started = False

            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    streaming_source.mark_finished()
                    # If playback never started, restore listening
                    if not playback_started:
                        current = self.sessions.get(guild_id)
                        if current:
                            current.listening = True
                    break

                # Respect MAX_TTS_CHARS limit
                if tts_chars_fed >= MAX_TTS_CHARS:
                    continue
                if tts_chars_fed + len(sentence) > MAX_TTS_CHARS:
                    sentence = sentence[:MAX_TTS_CHARS - tts_chars_fed] + "..."
                tts_chars_fed += len(sentence)

                await self._synthesize_and_feed(sentence, streaming_source, loop)

                # Start playback after first chunk is actually added to the source
                if not playback_started and streaming_source.is_streaming and session.voice_client.is_connected():
                    playback_started = True
                    # Pause listening during playback
                    session.listening = False
                    logger.info("[STREAMING] Paused listening, starting streaming playback")

                    def on_playback_complete(error):
                        if error:
                            logger.error(f"[STREAMING] Playback error: {error}")
                        current = self.sessions.get(guild_id)
                        if current:
                            current.listening = True
                            logger.info("[STREAMING] Resumed listening after playback")

                    session.voice_client.play(streaming_source, after=on_playback_complete)

        consumer_task = asyncio.create_task(tts_consumer())

        # Check accumulated buffer from Phase 1 for complete sentences before reading more tokens
        min_chars = MIN_FIRST_SENTENCE_CHARS if is_first_sentence else MIN_SENTENCE_CHARS
        sentence, text_buffer = self._extract_sentence(text_buffer, min_chars=min_chars)
        if sentence:
            logger.info(f"[STREAMING] Extracted sentence ({len(sentence)} chars): {sentence[:60]}...")
            await sentence_queue.put(sentence)
            is_first_sentence = False

        # Continue consuming tokens from LLM stream
        while True:
            token = await token_queue.get()
            if token is None:
                break
            full_response += token
            text_buffer += token

            # Try to extract a complete sentence
            min_chars = MIN_FIRST_SENTENCE_CHARS if is_first_sentence else MIN_SENTENCE_CHARS
            sentence, text_buffer = self._extract_sentence(text_buffer, min_chars=min_chars)
            if sentence:
                logger.info(f"[STREAMING] Extracted sentence ({len(sentence)} chars): {sentence[:60]}...")
                await sentence_queue.put(sentence)
                is_first_sentence = False

        # Flush remaining buffer as final sentence
        remaining = text_buffer.strip()
        if remaining:
            logger.info(f"[STREAMING] Flushing remaining buffer ({len(remaining)} chars): {remaining[:60]}...")
            await sentence_queue.put(remaining)

        # Signal end to TTS consumer
        await sentence_queue.put(None)
        await consumer_task

        logger.info(f"[STREAMING] Complete response ({len(full_response)} chars): {full_response[:100]}...")
        return full_response, False

    async def _synthesize_and_feed(self, text: str, streaming_source: StreamingTTSSource, loop: asyncio.AbstractEventLoop):
        """Synthesize a sentence and feed PCM audio into the streaming source."""
        tts_text = clean_text_for_tts(text)
        if not tts_text.strip():
            return

        logger.info(f"[STREAMING] Synthesizing: {tts_text[:60]}...")
        samples, sr = await loop.run_in_executor(
            self._tts_executor, self._get_tts().synthesize, tts_text
        )
        pcm = await loop.run_in_executor(
            self._tts_executor, resample_tts_to_discord, samples, sr
        )
        streaming_source.add_chunk(pcm)
        logger.info(f"[STREAMING] Fed {len(pcm)} bytes to streaming source")

    async def _batch_tts_and_play(self, guild_id: int, session: VoiceSession, response_text: str):
        """Batch TTS synthesis and playback — used as fallback for routed responses."""
        loop = asyncio.get_running_loop()
        tts_text = clean_text_for_tts(response_text)
        if len(tts_text) > MAX_TTS_CHARS:
            tts_text = tts_text[:MAX_TTS_CHARS] + "..."

        logger.info(f"[BATCH] Synthesizing TTS response ({len(tts_text)} chars)...")
        samples, sample_rate = await loop.run_in_executor(
            self._tts_executor, self._get_tts().synthesize, tts_text
        )

        if not session.voice_client.is_connected():
            logger.warning("[BATCH] Voice client disconnected before TTS playback")
            return

        audio_source = create_tts_source(samples, sample_rate)

        # Wait for any current playback to finish
        while session.voice_client.is_connected() and session.voice_client.is_playing():
            await asyncio.sleep(0.1)

        if not session.voice_client.is_connected():
            return

        session.listening = False
        logger.info("[BATCH] Paused listening during TTS playback")

        def on_playback_complete(error):
            if error:
                logger.error(f"[BATCH] Playback error: {error}")
            current = self.sessions.get(guild_id)
            if current:
                current.listening = True
                logger.info("[BATCH] Resumed listening after playback")

        session.voice_client.play(audio_source, after=on_playback_complete)

    def _extract_sentence(self, buffer: str, min_chars: int = MIN_SENTENCE_CHARS) -> tuple[Optional[str], str]:
        """
        Extract a complete sentence from the buffer if available.

        Args:
            buffer: Accumulated text from LLM stream
            min_chars: Minimum characters before attempting sentence split

        Returns:
            Tuple of (sentence, remaining_buffer) or (None, buffer) if no complete sentence
        """
        if len(buffer) < min_chars:
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
