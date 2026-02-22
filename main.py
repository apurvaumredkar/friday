import logging
from dotenv import load_dotenv

load_dotenv()

import discord

discord.opus.load_opus("/usr/lib/x86_64-linux-gnu/libopus.so.0")

from utils import google_workspace_auth
from discord_app.bot import run_discord_bot

LOG_FILE = "friday.log"
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
logging.getLogger("discord.opus").setLevel(logging.CRITICAL)


if __name__ == "__main__":
    google_workspace_auth.run_auth()
    run_discord_bot()
