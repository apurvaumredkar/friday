import asyncio
import threading
from collections import defaultdict
import discord
from ai.speech.asr import transcribe

SILENCE_TIMEOUT = 1.5


class FridaySink(discord.sinks.Sink):
    def __init__(self, user_id, on_transcription, loop):
        super().__init__()
        self.user_id = user_id
        self.on_transcription = on_transcription
        self.loop = loop
        self._buffers = defaultdict(bytearray)
        self._timers = {}
        super().__init__()

    def write(self, data, user):
        if user != self.user_id:
            return
        self._buffers[user].extend(data)
        if user in self._timers:
            self._timers[user].cancel()
        timer = threading.Timer(SILENCE_TIMEOUT, self._on_silence, args=[user])
        timer.daemon = True
        timer.start()
        self._timers[user] = timer

    def _on_silence(self, user):
        if not self._buffers[user]:
            return
        pcm_bytes = bytes(self._buffers.pop(user))

        def process():
            transcript = transcribe(pcm_bytes)
            if transcript.strip():
                self.on_transcription(transcript)

        asyncio.run_coroutine_threadsafe(
            self.loop.run_in_executor(None, process), self.loop
        )

    def cleanup(self):
        for timer in self._timers.values():
            timer.cancel()
        self._buffers.clear()
