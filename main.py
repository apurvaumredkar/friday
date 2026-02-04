import asyncio
import threading
import signal
import sys
import logging
import time
import webbrowser
import uvicorn
from services.discord_bot import start_discord_bot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('friday.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Suppress noisy voice recv logs (rtcp packets, extra ws keys)
logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)
logging.getLogger("discord.ext.voice_recv.gateway").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

shutdown_event = threading.Event()


def run_api():
    logger.info("Starting FastAPI server on port 2401")
    try:
        uvicorn.run("services.api:app", host="0.0.0.0", port=2401, log_level="info")
    except Exception as e:
        logger.error(f"FastAPI server error: {e}")
    finally:
        logger.info("FastAPI server stopped")


def run_discord():
    logger.info("Starting Discord bot")
    try:
        start_discord_bot()
    except Exception as e:
        logger.error(f"Discord bot error: {e}")
    finally:
        logger.info("Discord bot stopped")


def open_browser():
    """Open web interface in browser after server starts."""
    logger.info("Waiting for server to be ready...")
    time.sleep(3)  # Wait for server to start

    url = "http://localhost:2401"
    logger.info(f"Opening web interface in browser: {url}")
    try:
        webbrowser.open(url)
    except Exception as e:
        logger.warning(f"Failed to open browser automatically: {e}")


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating graceful shutdown")
    shutdown_event.set()


async def main():
    logger.info("=" * 50)
    logger.info("Starting Friday AI Services")
    logger.info("=" * 50)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Speech models are now preloaded in discord_bot.on_ready()
    api_thread = threading.Thread(target=run_api, daemon=True, name="API-Thread")
    discord_thread = threading.Thread(target=run_discord, daemon=True, name="Discord-Thread")
    browser_thread = threading.Thread(target=open_browser, daemon=True, name="Browser-Thread")

    logger.info("Launching service threads")
    api_thread.start()
    discord_thread.start()
    browser_thread.start()

    logger.info("[OK] All services started!")
    logger.info("  - FastAPI: http://localhost:2401")
    logger.info("  - Discord: Bot is running")
    logger.info("  - Web UI: Opening in browser...")
    logger.info("Press Ctrl+C to stop all services")

    try:
        while not shutdown_event.is_set():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        logger.info("Initiating graceful shutdown")
        shutdown_event.set()

        logger.info("Waiting for threads to complete")
        api_thread.join(timeout=5)
        discord_thread.join(timeout=5)

        logger.info("All services stopped")
        logger.info("Shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
