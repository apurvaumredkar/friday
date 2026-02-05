import asyncio
import threading
import signal
import sys
import logging
import time
import webbrowser
import subprocess
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
    import platform

    logger.info("Waiting for server to be ready...")
    time.sleep(3)  # Wait for server to start

    url = "http://localhost:2401"
    logger.info(f"Opening web interface in browser: {url}")
    try:
        # Check if running in WSL
        is_wsl = "microsoft" in platform.uname().release.lower()
        if is_wsl:
            subprocess.Popen(["powershell.exe", "-Command", f'Start-Process "{url}"'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
    except Exception as e:
        logger.warning(f"Failed to open browser automatically: {e}")


def ensure_docker_services():
    """Check if docker-compose services are running and start them if not."""
    logger.info("Checking Docker services (Qdrant, Neo4j)...")

    try:
        # Check if docker-compose services are running
        result = subprocess.run(
            ["docker-compose", "ps", "--services", "--filter", "status=running"],
            capture_output=True,
            text=True,
            cwd="/mnt/d/friday"
        )

        running_services = result.stdout.strip().split('\n') if result.stdout.strip() else []
        required_services = {"qdrant", "neo4j"}
        running_set = set(running_services)

        if required_services.issubset(running_set):
            logger.info("  - Qdrant: running (ports 6333, 6334)")
            logger.info("  - Neo4j: running (ports 7474, 7687)")
            return True

        # Some services not running, start them
        missing = required_services - running_set
        logger.info(f"Starting Docker services: {', '.join(missing)}")

        result = subprocess.run(
            ["docker-compose", "up", "-d"],
            capture_output=True,
            text=True,
            cwd="/mnt/d/friday"
        )

        if result.returncode != 0:
            logger.error(f"Failed to start Docker services: {result.stderr}")
            return False

        # Wait a moment for services to initialize
        time.sleep(2)
        logger.info("  - Qdrant: started (ports 6333, 6334)")
        logger.info("  - Neo4j: started (ports 7474, 7687)")
        return True

    except FileNotFoundError:
        logger.warning("docker-compose not found. Skipping Docker services.")
        return False
    except Exception as e:
        logger.error(f"Error managing Docker services: {e}")
        return False


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating graceful shutdown")
    shutdown_event.set()


async def main():
    logger.info("=" * 50)
    logger.info("Starting Friday AI Services")
    logger.info("=" * 50)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start Docker services (Qdrant, Neo4j) before other services
    ensure_docker_services()

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
