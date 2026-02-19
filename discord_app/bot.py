import os
import logging
import discord
import asyncio
from discord.ext import commands
from ai.orchestrator import Orchestrator
from discord_app.voice_handler import FridaySink

logger = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
USER_ID = int(os.getenv("DISCORD_USER_ID"))
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="$", intents=intents)
friday = Orchestrator()


@bot.event
async def on_ready():
    logger.info("Discord bot ACTIVE.")
    return


@bot.event
async def on_voice_state_update(member, before, after):
    if member.id != USER_ID:
        return
    if before.channel == after.channel:
        return
    if after.channel:
        voice_client = discord.utils.get(bot.voice_clients, guild=after.channel.guild)
        if voice_client:
            if voice_client.channel != after.channel:
                await voice_client.move_to(after.channel)
        else:
            voice_client = await after.channel.connect()
            voice_client.start_recording(
                FridaySink(USER_ID, friday.run, asyncio.get_event_loop()),
                _on_recording_stopped,
            )
            logger.info(f"Friday joined voice channel.")
    else:
        voice_client = discord.utils.get(bot.voice_clients, guild=before.channel.guild)
        if voice_client:
            await voice_client.disconnect()
            logger.info(f"Friday left voice channel.")


async def _on_recording_stopped(sink, *args):
    pass


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    async with message.channel.typing():
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, friday.run, message.content)
        await message.channel.send(response)
    await bot.process_commands(message)


def run_discord_bot():
    bot.run(BOT_TOKEN)
