import io
import threading
from collections import defaultdict
import discord
from ai.speech.asr import transcribe
from ai.speech.tts import synthesize

SILENCE_TIMEOUT = 1.5


class FridaySink(discord.sinks.Sink):
    def __init__(self, user_id, on_transcription, voice_client):
        self.user_id = user_id
        self.on_transcription = on_transcription
        self._buffers = defaultdict(bytearray)
        self._timers = {}
        self.voice_client = voice_client
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
            if not transcript.strip():
                return
            res = self.on_transcription(transcript)
            if not res:
                return
            out = synthesize(res)
            source = discord.PCMAudio(io.BytesIO(out))
            if not self.voice_client.is_playing():
                self.voice_client.play(source)

        process()

    def cleanup(self):
        for timer in self._timers.values():
            timer.cancel()
        self._buffers.clear()
