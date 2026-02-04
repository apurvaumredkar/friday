import os
import uuid
import asyncio
import logging
import httpx
import discord
from discord.ext import commands
from dotenv import load_dotenv
from ai import Friday
from services.voice import VoiceManager

load_dotenv()

# Load opus library for voice support
if not discord.opus.is_loaded():
    try:
        discord.opus.load_opus('libopus.so.0')
    except OSError:
        try:
            discord.opus.load_opus('opus')
        except OSError:
            pass  # Will fail later if voice is used without opus

logger = logging.getLogger(__name__)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix="$", intents=intents)
friday = Friday()
voice_manager = VoiceManager(friday)
conversation_history = {}

# Flag to track if speech models are ready for voice interactions
speech_models_ready = False

# Discord bot control
_discord_token = None
_bot_responding = True  # Whether bot should respond to messages


PDF_EXTENSIONS = ('.pdf',)


async def send_response(channel, response):
    if len(response) > 2000:
        chunks = [response[i:i+2000] for i in range(0, len(response), 2000)]
        logger.info(f"Response split into {len(chunks)} chunks")
        for chunk in chunks:
            await channel.send(chunk)
    else:
        await channel.send(response)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Ignore all messages if bot is disabled
    if not _bot_responding:
        return

    ctx = await bot.get_context(message)
    if ctx.valid:
        logger.info(f"Command received: {message.content}")
        await bot.invoke(ctx)
        return

    channel_id = message.channel.id

    # Check for PDF attachments
    pdf_attachments = [a for a in message.attachments if a.filename.lower().endswith(PDF_EXTENSIONS)]

    if pdf_attachments:
        logger.info(f"PDF attachment detected from {message.author} in channel {channel_id}")

        if channel_id not in conversation_history:
            conversation_history[channel_id] = []
            logger.info(f"Initialized conversation history for channel {channel_id}")

        try:
            # Download PDF from CDN URL
            pdf_attachment = pdf_attachments[0]
            pdf_url = pdf_attachment.url
            pdf_filename = pdf_attachment.filename
            logger.info(f"Downloading PDF from CDN: {pdf_url[:80]}...")
            response = httpx.get(pdf_url)
            response.raise_for_status()
            pdf_bytes = response.content

            logger.info(f"Downloaded PDF: {pdf_filename}, size: {len(pdf_bytes)} bytes")

            # Build prompt - skill will handle extraction via docs_agent
            user_prompt = message.content if message.content else "Process this paycheck"

            conversation_history[channel_id].append({
                "role": "user",
                "content": user_prompt
            })

            logger.info(f"Invoking Friday orchestrator with PDF attachment")
            result = friday.app.invoke({
                "messages": conversation_history[channel_id],
                "image_url": None,
                "original_prompt": user_prompt,
                "pdf_bytes": pdf_bytes,
                "pdf_filename": pdf_filename
            })

            conversation_history[channel_id] = result["messages"]
            response = result["messages"][-1]["content"]
            logger.info(f"PDF processing response length: {len(response)} chars")

            await send_response(message.channel, response)
            logger.info(f"PDF processing response sent to channel {channel_id}")

        except Exception as e:
            logger.error(f"Error processing PDF in channel {channel_id}: {e}")
            await message.channel.send(f"Error processing PDF: {str(e)}")
        return

    logger.info(f"Message from {message.author} in channel {channel_id}: {message.content[:50]}...")

    if channel_id not in conversation_history:
        conversation_history[channel_id] = []
        logger.info(f"Initialized conversation history for channel {channel_id}")

    conversation_history[channel_id].append({
        "role": "user",
        "content": message.content
    })

    try:
        logger.info(f"Invoking Friday agent for channel {channel_id}")
        result = friday.app.invoke({
            "messages": conversation_history[channel_id],
            "image_url": None,
            "original_prompt": None
        })
        conversation_history[channel_id] = result["messages"]
        response = result["messages"][-1]["content"]
        logger.info(f"Friday response length: {len(response)} chars")

        await send_response(message.channel, response)
        logger.info(f"Response sent successfully to channel {channel_id}")

    except Exception as e:
        logger.error(f"Error processing message in channel {channel_id}: {e}")
        await message.channel.send(f"Error: {str(e)}")


@bot.command(name="clear")
async def clear_history(ctx, limit: int = 100):
    channel_id = ctx.channel.id
    logger.info(f"Clear command invoked for channel {channel_id} with limit {limit}")

    try:
        if channel_id in conversation_history:
            conversation_history[channel_id] = []
            logger.info(f"Cleared conversation history for channel {channel_id}")

        deleted = await ctx.channel.purge(limit=limit + 1)
        logger.info(f"Purged {len(deleted)} messages from channel {channel_id}")

        msg = await ctx.send(f"Cleared conversation history and deleted {len(deleted)} messages.")
        await asyncio.sleep(5)
        await msg.delete()

    except discord.Forbidden:
        logger.warning(f"Missing permissions to delete messages in channel {channel_id}")
        await ctx.send("I don't have permission to delete messages in this channel.")
    except Exception as e:
        logger.error(f"Error clearing messages in channel {channel_id}: {e}")
        await ctx.send(f"Error clearing messages: {str(e)}")


@bot.command(name="join")
async def join_voice(ctx):
    """Join the user's voice channel and start listening."""
    if not speech_models_ready:
        await ctx.send("Voice models are still loading. Please wait a moment and try again.")
        return

    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("You need to be in a voice channel to use this command.")
        return

    voice_channel = ctx.author.voice.channel
    await voice_manager.join_channel(
        voice_channel=voice_channel,
        text_channel=ctx.channel,
        user=ctx.author,
    )


@bot.command(name="leave")
async def leave_voice(ctx):
    """Leave the current voice channel."""
    guild_id = ctx.guild.id

    if await voice_manager.leave_channel(guild_id):
        await ctx.send("Left the voice channel.")
    else:
        await ctx.send("I'm not in a voice channel.")


@bot.command(name="voice")
async def toggle_voice(ctx):
    """Toggle voice listening on/off."""
    guild_id = ctx.guild.id

    new_state = voice_manager.toggle_listening(guild_id)

    if new_state is None:
        await ctx.send("I'm not in a voice channel. Use `$join` first.")
    elif new_state:
        await ctx.send("Voice listening enabled. I'm listening!")
    else:
        await ctx.send("Voice listening paused.")


@bot.event
async def on_voice_state_update(member, before, after):
    """Handle voice state changes - auto join/leave with user."""
    # Ignore bot's own voice state changes
    if member.id == bot.user.id:
        if after.channel is None and before.channel is not None:
            guild_id = before.channel.guild.id
            if guild_id in voice_manager.sessions:
                del voice_manager.sessions[guild_id]
                logger.info(f"Bot disconnected from voice in guild {guild_id}")
        return

    guild_id = member.guild.id
    session = voice_manager.get_session(guild_id)

    # User joined a voice channel
    if after.channel is not None and before.channel is None:
        # Only auto-join if speech models are ready and not already in a session
        if session is None and speech_models_ready:
            # Find a text channel for status messages
            text_channel = (
                member.guild.system_channel
                or next(
                    (ch for ch in member.guild.text_channels if ch.permissions_for(member.guild.me).send_messages),
                    None
                )
            )
            if text_channel:
                logger.info(f"Auto-joining voice channel {after.channel.name} for {member.display_name}")
                await voice_manager.join_channel(
                    voice_channel=after.channel,
                    text_channel=text_channel,
                    user=member,
                )
        return

    # User left a voice channel
    if after.channel is None and before.channel is not None:
        # Auto-leave if this was our target user
        if session and member.id == session.target_user_id:
            logger.info(f"Auto-leaving voice channel - {member.display_name} left")
            await voice_manager.leave_channel(guild_id)
        return

    # User moved to a different voice channel
    if after.channel != before.channel and after.channel is not None and before.channel is not None:
        if session and member.id == session.target_user_id:
            # Move bot to the new channel
            logger.info(f"Following {member.display_name} to {after.channel.name}")
            try:
                await session.voice_client.move_to(after.channel)
            except Exception as e:
                logger.error(f"Failed to move to new channel: {e}")


def _load_asr():
    """Load ASR model."""
    import time
    start = time.perf_counter()
    from ai.speech.asr import get_asr_engine
    asr = get_asr_engine()
    asr._load_model()
    logger.info(f"  ASR (Parakeet TDT) loaded in {time.perf_counter() - start:.1f}s")


def _load_tts():
    """Load TTS model."""
    import time
    start = time.perf_counter()
    from ai.speech.tts import get_tts_engine
    tts = get_tts_engine()
    tts._load_model()
    logger.info(f"  TTS (Kokoro) loaded in {time.perf_counter() - start:.1f}s")


def _preload_speech_models():
    """Preload ASR and TTS models in parallel."""
    global speech_models_ready
    import time
    import threading

    logger.info("Preloading speech models in parallel...")
    start = time.perf_counter()

    # Load both models in parallel
    asr_thread = threading.Thread(target=_load_asr, name="ASR-Preload")
    tts_thread = threading.Thread(target=_load_tts, name="TTS-Preload")

    asr_thread.start()
    tts_thread.start()

    # Wait for both to complete
    asr_thread.join()
    tts_thread.join()

    # Mark models as ready - voice features now available
    speech_models_ready = True
    logger.info(f"[OK] Speech models ready in {time.perf_counter() - start:.1f}s (parallel) - voice enabled")


@bot.event
async def on_ready():
    logger.info(f"[OK] Discord bot logged in as {bot.user}")
    logger.info(f"[OK] Bot is in {len(bot.guilds)} guilds")

    # Preload models in background so they're ready immediately
    import threading

    def preload_all_models():
        """Preload speech models in background."""
        logger.info("Preloading speech models in background...")
        _preload_speech_models()
        logger.info("[OK] All background models loaded")

    preload_thread = threading.Thread(target=preload_all_models, daemon=True)
    preload_thread.start()


def start_discord_bot():
    """Start the Discord bot."""
    global _discord_token
    _discord_token = os.getenv("DISCORD_TOKEN")
    if not _discord_token:
        raise ValueError("DISCORD_TOKEN not found in environment variables")

    bot.run(_discord_token)


async def set_bot_status(online: bool):
    """
    Enable/disable the Discord bot (changes presence and stops responding).

    Args:
        online: True to enable, False to disable
    """
    global _bot_responding

    if bot.is_ready():
        if online:
            logger.info("Enabling Discord bot")
            _bot_responding = True
            await bot.change_presence(status=discord.Status.online)
            logger.info("Discord bot is now online and responding")
        else:
            logger.info("Disabling Discord bot")
            _bot_responding = False
            await bot.change_presence(status=discord.Status.invisible)
            logger.info("Discord bot is now invisible and non-responsive")
    else:
        logger.warning(f"Cannot change bot status - bot is not ready (online={online})")


def get_bot_instance():
    """Get the Discord bot instance for external control."""
    return bot


if __name__ == "__main__":
    start_discord_bot()
