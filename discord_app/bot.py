import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging

from ai.agents import root

logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='$', intents=intents)


@bot.event
async def on_ready():
    logger.info("Discord bot ACTIVE.")
    return


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    async with message.channel.typing():
        response = root.reply(message.content)

        await message.channel.send(response)

    await bot.process_commands(message)


def run_discord_bot():
    bot.run(BOT_TOKEN)
