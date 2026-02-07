"""Register Discord slash commands for Friday remote control.

Run once (or whenever commands change):
    python control/register_commands.py

For instant testing, pass your guild ID:
    python control/register_commands.py --guild 123456789
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DISCORD_APP_ID = os.environ["DISCORD_APP_ID"]
DISCORD_BOT_TOKEN = os.environ["DISCORD_TOKEN"]

COMMANDS = [
    {"name": "start", "description": "Start Friday AI assistant", "type": 1},
    {"name": "stop", "description": "Stop Friday AI assistant", "type": 1},
    {"name": "reboot", "description": "Reboot Friday AI assistant", "type": 1},
    {"name": "status", "description": "Check Friday's current status", "type": 1},
    {"name": "clear", "description": "Clear messages in this channel", "type": 1},
    {"name": "logs", "description": "Get link to Friday's live logs", "type": 1},
]


def register(guild_id: str | None = None):
    if guild_id:
        url = f"https://discord.com/api/v10/applications/{DISCORD_APP_ID}/guilds/{guild_id}/commands"
        print(f"Registering guild commands (guild {guild_id}) — instant propagation")
    else:
        url = f"https://discord.com/api/v10/applications/{DISCORD_APP_ID}/commands"
        print("Registering global commands — may take up to 1 hour to propagate")

    resp = httpx.put(
        url,
        json=COMMANDS,
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
        timeout=30,
    )

    if resp.status_code == 200:
        names = [c["name"] for c in resp.json()]
        print(f"Registered {len(names)} commands: {', '.join('/' + n for n in names)}")
    else:
        print(f"Error {resp.status_code}: {resp.text}")
        sys.exit(1)


if __name__ == "__main__":
    guild = sys.argv[sys.argv.index("--guild") + 1] if "--guild" in sys.argv else None
    register(guild)
