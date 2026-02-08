"""Friday Control Server — Remote process management via Discord slash commands.

A lightweight FastAPI server (port 2400) that:
1. Handles Discord Interactions API (slash commands: /start, /stop, /reboot, /status)
2. Manages Friday (main.py) as a subprocess

Exposed to the internet via ngrok, this server receives Discord slash commands
directly — no polling, no Cloudflare Worker, no external state.

Slow commands (start/stop/reboot) use deferred responses + background follow-ups
since Discord requires a response within 3 seconds.
"""

import os
import signal
import subprocess
import time
import logging
import threading
import asyncio
import base64
from pathlib import Path

import secrets
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import uvicorn

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FRIDAY_DIR = Path(__file__).resolve().parent.parent
FRIDAY_PYTHON = FRIDAY_DIR / ".venv" / "bin" / "python"
FRIDAY_MAIN = FRIDAY_DIR / "main.py"
FRIDAY_LOG = FRIDAY_DIR / "friday.log"
PID_FILE = FRIDAY_DIR / "friday.pid"

DISCORD_PUBLIC_KEY = os.environ["DISCORD_PUBLIC_KEY"]
DISCORD_APP_ID = os.environ["DISCORD_APP_ID"]
DISCORD_BOT_TOKEN = os.environ["DISCORD_TOKEN"]
CONTROL_PASSWORD = os.environ["CONTROL_PASSWORD"]

security = HTTPBasic()


def verify_password(credentials: HTTPBasicCredentials = Depends(security)):
    """HTTP Basic Auth — any username, password must match CONTROL_PASSWORD."""
    username_match = secrets.compare_digest(credentials.username, "appu")
    password_match = secrets.compare_digest(credentials.password, CONTROL_PASSWORD)
    if not (username_match and password_match):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("friday-control")

# ---------------------------------------------------------------------------
# Process Manager
# ---------------------------------------------------------------------------

_friday_process: subprocess.Popen | None = None
_friday_start_time: float | None = None
_friday_log_handle = None


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def recover_process() -> None:
    """On startup, recover tracking of an already-running Friday instance via PID file."""
    global _friday_process, _friday_start_time

    if not PID_FILE.exists():
        return

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return

    if _is_pid_alive(pid):
        logger.info(f"Recovered running Friday process (PID {pid})")
        _friday_start_time = PID_FILE.stat().st_mtime
    else:
        logger.info(f"Stale PID file (PID {pid} not alive), cleaning up")
        PID_FILE.unlink(missing_ok=True)


def _get_recovered_pid() -> int | None:
    """Get PID from file if we recovered a process we didn't spawn."""
    if _friday_process is not None:
        return None
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        return pid if _is_pid_alive(pid) else None
    except (ValueError, OSError):
        return None


def is_running() -> bool:
    """Check if Friday is currently running."""
    if _friday_process is not None:
        return _friday_process.poll() is None
    return _get_recovered_pid() is not None


def start_friday() -> str:
    """Start Friday as a subprocess."""
    global _friday_process, _friday_start_time, _friday_log_handle

    if is_running():
        pid = _friday_process.pid if _friday_process else _get_recovered_pid()
        return f"Friday is already running (PID {pid})"

    _friday_log_handle = open(FRIDAY_LOG, "a")
    _friday_process = subprocess.Popen(
        [str(FRIDAY_PYTHON), str(FRIDAY_MAIN)],
        cwd=str(FRIDAY_DIR),
        stdout=_friday_log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _friday_start_time = time.time()

    # Brief wait to catch immediate crashes
    time.sleep(3)
    if _friday_process.poll() is not None:
        code = _friday_process.returncode
        _friday_process = None
        _friday_start_time = None
        _close_log_handle()
        return f"Friday failed to start (exit code {code})"

    logger.info(f"Friday started (PID {_friday_process.pid})")
    return f"Friday started (PID {_friday_process.pid})"


def _close_log_handle():
    """Close the log file handle if open."""
    global _friday_log_handle
    if _friday_log_handle:
        try:
            _friday_log_handle.close()
        except Exception:
            pass
        _friday_log_handle = None


def stop_friday() -> str:
    """Stop Friday gracefully (SIGTERM, then SIGKILL after timeout)."""
    global _friday_process, _friday_start_time

    # Handle recovered process (we have PID but no Popen handle)
    recovered_pid = _get_recovered_pid()
    if recovered_pid and _friday_process is None:
        try:
            os.kill(recovered_pid, signal.SIGTERM)
            for _ in range(20):  # 10 seconds
                time.sleep(0.5)
                if not _is_pid_alive(recovered_pid):
                    break
            else:
                os.kill(recovered_pid, signal.SIGKILL)
                time.sleep(1)
        except OSError:
            pass
        PID_FILE.unlink(missing_ok=True)
        _friday_start_time = None
        _close_log_handle()
        logger.info(f"Friday stopped (was PID {recovered_pid})")
        return f"Friday stopped (was PID {recovered_pid})"

    if _friday_process is None or _friday_process.poll() is not None:
        _friday_process = None
        _friday_start_time = None
        _close_log_handle()
        return "Friday is not running"

    pid = _friday_process.pid
    _friday_process.terminate()

    try:
        _friday_process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        logger.warning(f"Friday (PID {pid}) did not stop gracefully, sending SIGKILL")
        _friday_process.kill()
        _friday_process.wait(timeout=5)

    _friday_process = None
    _friday_start_time = None
    _close_log_handle()
    PID_FILE.unlink(missing_ok=True)
    logger.info(f"Friday stopped (was PID {pid})")
    return f"Friday stopped (was PID {pid})"


def reboot_friday() -> str:
    """Stop and restart Friday."""
    stop_msg = stop_friday()
    time.sleep(2)
    start_msg = start_friday()
    return f"{stop_msg}\n{start_msg}"


def get_status() -> str:
    """Get Friday's current status."""
    global _friday_process
    if _friday_process is not None and _friday_process.poll() is None:
        uptime = time.time() - (_friday_start_time or time.time())
        mins, secs = divmod(int(uptime), 60)
        hrs, mins = divmod(mins, 60)
        uptime_str = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
        return f"Friday is running (PID {_friday_process.pid}, uptime: {uptime_str})"

    recovered_pid = _get_recovered_pid()
    if recovered_pid:
        uptime = time.time() - (_friday_start_time or time.time())
        mins, secs = divmod(int(uptime), 60)
        hrs, mins = divmod(mins, 60)
        uptime_str = f"{hrs}h {mins}m {secs}s" if hrs else f"{mins}m {secs}s"
        return f"Friday is running (PID {recovered_pid}, uptime: ~{uptime_str})"

    # Check if process crashed
    if _friday_process is not None:
        code = _friday_process.returncode
        _friday_process = None
        return f"Friday has crashed (exit code {code})"

    return "Friday is not running"


# ---------------------------------------------------------------------------
# Discord Interactions
# ---------------------------------------------------------------------------

verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))


def verify_discord_signature(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Discord request signature using Ed25519."""
    try:
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False


def send_followup(interaction_token: str, content: str):
    """Send a follow-up message to Discord after a deferred response."""
    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APP_ID}/{interaction_token}"
    try:
        resp = httpx.post(url, json={"content": content}, timeout=10)
        logger.info(f"Follow-up sent ({resp.status_code}): {content[:80]}")
    except Exception as e:
        logger.error(f"Failed to send follow-up: {e}")


def edit_original(interaction_token: str, content: str):
    """Edit the original deferred response with the final result."""
    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APP_ID}/{interaction_token}/messages/@original"
    try:
        resp = httpx.patch(url, json={"content": content}, timeout=10)
        logger.info(f"Edited original ({resp.status_code}): {content[:80]}")
    except Exception as e:
        logger.error(f"Failed to edit original: {e}")


def run_and_followup(handler, interaction_token: str):
    """Execute a slow command in a background thread and edit the original response with the result."""
    try:
        result = handler()
        edit_original(interaction_token, result)
    except Exception as e:
        logger.error(f"Background command error: {e}")
        edit_original(interaction_token, f"Error: {e}")


def clear_channel(channel_id: str) -> str:
    """Delete recent messages from a Discord channel via Bot API."""
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=100"

    try:
        resp = httpx.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return f"Failed to fetch messages (HTTP {resp.status_code})"

        messages = resp.json()
        if not messages:
            return "No messages to delete"

        msg_ids = [m["id"] for m in messages]

        if len(msg_ids) == 1:
            httpx.delete(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/{msg_ids[0]}",
                headers=headers, timeout=10,
            )
        else:
            httpx.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/bulk-delete",
                headers=headers, json={"messages": msg_ids}, timeout=10,
            )

        return f"Cleared {len(msg_ids)} messages"
    except Exception as e:
        logger.error(f"Clear channel error: {e}")
        return f"Error clearing messages: {e}"


# Commands that are fast enough for immediate response
NGROK_LOGS_URL = "https://lovella-flamy-rigorously.ngrok-free.dev/friday/logs"

IMMEDIATE_COMMANDS = {"status", "logs"}

COMMAND_HANDLERS = {
    "start": start_friday,
    "stop": stop_friday,
    "reboot": reboot_friday,
    "status": get_status,
    "logs": lambda: NGROK_LOGS_URL,
    # "clear" is handled separately (needs channel_id)
}

PROGRESS_MESSAGES = {
    "start": "Booting Friday up...",
    "stop": "Shutting Friday down...",
    "reboot": "Rebooting Friday...",
    "clear": "Clearing messages...",
}

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(title="Friday Control Server")


@app.on_event("startup")
async def startup():
    recover_process()
    logger.info("Friday Control Server started on port 2400")


@app.get("/health")
async def health():
    return {"status": "ok", "friday": "running" if is_running() else "stopped"}


LOGS_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Friday Logs</title>
<style>
  body { margin:0; background:#0a0a0a; color:#e0e0e0; font-family:'JetBrains Mono','Courier New',monospace; font-size:13px; }
  #logs { padding:12px; white-space:pre-wrap; word-wrap:break-word; line-height:1.5; }
  .error { color:#ff4444; } .warning { color:#ffaa00; } .debug { color:#666; }
  #status { position:fixed; top:8px; right:12px; font-size:11px; color:#888; }
</style>
</head><body>
<div id="status">connecting...</div>
<div id="logs"></div>
<script>
const logs = document.getElementById('logs');
const statusEl = document.getElementById('status');
const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
let ws;

function classify(line) {
  const l = line.toLowerCase();
  if (l.includes('error')) return 'error';
  if (l.includes('warning') || l.includes('warn')) return 'warning';
  if (l.includes('debug')) return 'debug';
  return '';
}

function appendLine(text) {
  const div = document.createElement('div');
  div.textContent = text;
  const cls = classify(text);
  if (cls) div.className = cls;
  logs.appendChild(div);
  if (logs.children.length > 2000) logs.removeChild(logs.firstChild);
}

function connect() {
  ws = new WebSocket(proto + '//' + location.host + '/friday/logs/ws');
  ws.onopen = () => { statusEl.textContent = 'live'; statusEl.style.color = '#00ff88'; };
  ws.onmessage = (e) => { appendLine(e.data); window.scrollTo(0, document.body.scrollHeight); };
  ws.onclose = () => { statusEl.textContent = 'reconnecting...'; statusEl.style.color = '#ffaa00'; setTimeout(connect, 3000); };
  ws.onerror = () => ws.close();
}

connect();
</script>
</body></html>"""


@app.get("/friday/logs")
async def friday_logs(_=Depends(verify_password)):
    """Serve live log viewer page (password-protected)."""
    return HTMLResponse(LOGS_HTML)


def _verify_ws_auth(authorization: str | None) -> bool:
    """Verify Basic Auth from WebSocket upgrade header."""
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:]).decode()
        username, password = decoded.split(":", 1)
        return secrets.compare_digest(username, "appu") and secrets.compare_digest(password, CONTROL_PASSWORD)
    except Exception:
        return False


@app.websocket("/friday/logs/ws")
async def friday_logs_ws(websocket: WebSocket):
    """Stream friday.log lines over WebSocket."""
    auth = websocket.headers.get("authorization")
    if not _verify_ws_auth(auth):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()

    try:
        # Send last 200 lines as backlog
        if FRIDAY_LOG.exists():
            with open(FRIDAY_LOG, "r") as f:
                all_lines = f.readlines()
                for line in all_lines[-200:]:
                    await websocket.send_text(line.rstrip("\n"))

        # Tail the file for new lines
        with open(FRIDAY_LOG, "r") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    await websocket.send_text(line.rstrip("\n"))
                else:
                    await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/")
async def discord_interactions(request: Request):
    """Handle Discord Interactions API (slash commands)."""
    body = await request.body()
    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")

    if not verify_discord_signature(body, signature, timestamp):
        return Response(status_code=401, content="Invalid signature")

    data = await request.json()
    interaction_type = data.get("type")

    # PING — required for Discord endpoint verification
    if interaction_type == 1:
        logger.info("Discord PING received, responding with PONG")
        return {"type": 1}

    # APPLICATION_COMMAND — slash commands
    if interaction_type == 2:
        command_name = data.get("data", {}).get("name", "")
        user = data.get("member", {}).get("user", {}).get("username", "unknown")
        interaction_token = data.get("token", "")
        logger.info(f"Slash command: /{command_name} from {user}")

        # /clear needs channel context — handle separately
        if command_name == "clear":
            channel_id = data.get("channel_id", "")
            threading.Thread(
                target=run_and_followup,
                args=(lambda: clear_channel(channel_id), interaction_token),
                daemon=True,
            ).start()
            return {
                "type": 4,
                "data": {"content": PROGRESS_MESSAGES.get("clear", "Working...")},
            }

        handler = COMMAND_HANDLERS.get(command_name)
        if not handler:
            return {
                "type": 4,
                "data": {"content": f"Unknown command: /{command_name}"},
            }

        # Fast commands: respond immediately
        if command_name in IMMEDIATE_COMMANDS:
            return {
                "type": 4,
                "data": {"content": handler()},
            }

        # Slow commands: respond immediately with progress message, edit with result later
        threading.Thread(
            target=run_and_followup,
            args=(handler, interaction_token),
            daemon=True,
        ).start()

        return {
            "type": 4,
            "data": {"content": PROGRESS_MESSAGES.get(command_name, "Working...")},
        }

    return Response(status_code=400, content="Unknown interaction type")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=2400, log_level="info")
