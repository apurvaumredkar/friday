import os
from dotenv import load_dotenv

load_dotenv()

import asyncio
import io
import threading
from pathlib import Path

import numpy as np
import onnxruntime as rt
from scipy.signal import resample_poly
import requests
import discord
import onnx_asr
from kokoro_onnx import Kokoro

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
DISCORD_USER_ID = int(os.environ["DISCORD_USER_ID"])

asr_model = onnx_asr.load_model(
    "nemo-parakeet-tdt-0.6b-v2",
    path="./data/nemo-parakeet-tdt-0.6b-v2-int8",
    quantization="int8",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)

_KOKORO_DIR = Path("./data/kokoro-82m")
_KOKORO_DIR.mkdir(parents=True, exist_ok=True)
_KOKORO_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
for _fname in ("kokoro-v1.0.int8.onnx", "voices-v1.0.bin"):
    _dest = _KOKORO_DIR / _fname
    if not _dest.exists():
        _dest.write_bytes(requests.get(f"{_KOKORO_BASE}/{_fname}").content)

_tts_session = rt.InferenceSession(
    str(_KOKORO_DIR / "kokoro-v1.0.int8.onnx"),
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
tts_model = Kokoro.from_session(_tts_session, str(_KOKORO_DIR / "voices-v1.0.bin"))

vad_model = onnx_asr.load_vad("silero", path="./data/silero")

SILENCE_TIMEOUT = 1.5
_VAD_CHUNK_BYTES = 48000 * 2 * 2 // 2  # 0.5s at 48kHz stereo int16


class FridaySink(discord.sinks.PCMSink):
    def __init__(self):
        super().__init__(filters={"time": 0, "users": [], "max_size": 0})
        self._vad_buf = bytearray()
        self._speech_seen = False
        self._timer: threading.Timer | None = None

    def _on_silence(self):
        if self._speech_seen:
            self.vc.stop_recording()

    @discord.sinks.Filters.container
    def write(self, data, user):
        super().write(data, user)
        self._vad_buf.extend(data)
        if len(self._vad_buf) < _VAD_CHUNK_BYTES:
            return
        chunk = (
            np.frombuffer(self._vad_buf, dtype=np.int16)
            .reshape(-1, 2)
            .mean(axis=1)
            .astype(np.float32)
            / 32768.0
        )
        self._vad_buf.clear()
        mono_16k = chunk[::3]
        waveforms = mono_16k[np.newaxis, :]
        waveforms_len = np.array([len(mono_16k)], dtype=np.int64)
        _batch = next(
            vad_model.segment_batch(waveforms, waveforms_len, 16000, min_speech_duration_ms=100),
            None,
        )
        has_speech = bool(list(_batch)) if _batch is not None else False
        if has_speech:
            self._speech_seen = True
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(SILENCE_TIMEOUT, self._on_silence)
            self._timer.start()


async def process(sink: FridaySink, vc: discord.VoiceClient):
    loop = asyncio.get_running_loop()
    try:
        for user_id, audio_data in sink.audio_data.items():
            pcm = audio_data.file.getvalue()
            waveform = (
                np.frombuffer(pcm, dtype=np.int16)
                .reshape(-1, 2)
                .mean(axis=1)
                .astype(np.float32)
                / 32768.0
            )
            text = await loop.run_in_executor(
                None, lambda: asr_model.recognize(waveform, sample_rate=48000)
            )
            print(f"[{user_id}] {text!r}")
            if not text.strip():
                continue
            r = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    WEBHOOK_URL,
                    json={"user_id": str(user_id), "text": text},
                    auth=(os.environ["N8N_USER"], os.environ["N8N_SECRET"]),
                ),
            )
            print(f"webhook: {r.status_code}")
            if not r.text.strip():
                continue
            data = r.json()
            print(f"webhook response: {data!r}")
            reply = data[0]["output"] if isinstance(data, list) else data["output"]
            samples, _ = await loop.run_in_executor(
                None, lambda: tts_model.create(reply, voice="af_heart")
            )
            pcm_24k = (samples * 32767).clip(-32768, 32767).astype(np.float32)
            pcm_48k = resample_poly(pcm_24k, up=2, down=1).astype(np.int16)
            vc.play(discord.PCMAudio(io.BytesIO(np.column_stack([pcm_48k, pcm_48k]).tobytes())))
    except Exception as e:
        print(f"process error: {e!r}")
    if vc.is_connected():
        vc.start_recording(FridaySink(), process, vc)


intents = discord.Intents.default()
intents.voice_states = True
bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    for guild in bot.guilds:
        for channel in guild.voice_channels:
            if any(m.id == DISCORD_USER_ID for m in channel.members):
                vc = await channel.connect()
                vc.start_recording(FridaySink(), process, vc)
                break


@bot.event
async def on_voice_state_update(member, before, after):
    if member.id != DISCORD_USER_ID:
        return
    vc = discord.utils.get(bot.voice_clients, guild=member.guild)
    if after.channel:
        if vc and vc.channel == after.channel:
            return
        if vc:
            await vc.disconnect()
        new_vc = await after.channel.connect()
        new_vc.start_recording(FridaySink(), process, new_vc)
    elif vc:
        await vc.disconnect()


bot.run(os.environ["DISCORD_TOKEN"])
