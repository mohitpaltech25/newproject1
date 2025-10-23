import threading
import queue
import asyncio
import time
import os
import edge_tts
from config.config import Config
from utils.logger_config import logger
from state.state import State
from utils.profiling import profile

class TTSWorkerThread(threading.Thread):
    def __init__(self, tts_queue):
        super().__init__(name="TTSWorker")
        self.tts_queue = tts_queue
        self.running = threading.Event()
        self.running.set()
        self.loop = asyncio.new_event_loop()

    @profile
    def run(self):
        asyncio.set_event_loop(self.loop)
        logger.info("[TTSWorker] Thread started.")
        self.loop.run_until_complete(self._process_tts())

    @profile
    async def _process_tts(self):
        while self.running.is_set():
            try:
                item = self.tts_queue.get(timeout=1.0)
                if len(item) > 0:
                    State.speaking = True
                sentence = item.get("sentence", "").strip()
                post_action = item.get("post_action")

                if not sentence:
                    continue

                if sentence == "[INTERRUPT]":
                    logger.info("[TTSWorker] Received interrupt signal.")
                    continue

                logger.info(f"[TTSWorker] Speaking: {sentence}")
                success = await self._speak_sentence(sentence)

                if success:
                    logger.info(f"[TTSWorker] Finished: {sentence}")
                    State.speaking = False
                else:
                    logger.warning(f"[TTSWorker] interrupted :{State.interrupted},sentence:{sentence}")
                    logger.warning(f"[TTSWorker] Failed or interrupted: {sentence}")

                if post_action:
                    post_action()

            except queue.Empty:
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.exception(f"[TTSWorker] Error: {e}")

    @profile
    async def _speak_sentence(self, sentence: str) -> bool:
        if not sentence or State.interrupted:
            return False

        mp3_path = os.path.join(Config.TEMP_DIR, f"tts_{int(time.time()*1e6)}.mp3")
        wav_path = os.path.join(Config.TEMP_DIR, f"tts_{int(time.time()*1e6)}.wav")

        try:
            start_time = time.time()
            communicate = edge_tts.Communicate(sentence, Config.TTS_VOICE, rate="+5%")
            await communicate.save(mp3_path)

            if not os.path.exists(mp3_path):
                return False

            convert_cmd = f"ffmpeg -y -i {mp3_path} -f wav -acodec pcm_s16le -ac 1 -ar 44100 -loglevel quiet {wav_path}"
            proc = await asyncio.create_subprocess_shell(convert_cmd)
            await proc.communicate()

            if not os.path.exists(wav_path):
                return False

            return await self._play_audio(wav_path)
        except Exception:
            logger.exception("[TTSWorker] TTS synthesis error")
            return False
        finally:
            for f in [mp3_path, wav_path]:
                if os.path.exists(f):
                    os.remove(f)

    @profile
    async def _play_audio(self, wav_path: str) -> bool:
        try:
            # Resolve sink id for Virtual-Sink
            import asyncio.subprocess as asp
            sink_cmd = "pactl list short sinks | rg Virtual-Sink | cut -f1"
            proc = await asyncio.create_subprocess_shell(sink_cmd, stdout=asp.PIPE)
            stdout, _ = await proc.communicate()
            sink_id = stdout.decode().strip() or "Virtual-Sink"

            cmd = f"paplay --device={sink_id} {wav_path}"
            play_proc = await asyncio.create_subprocess_shell(cmd)

            while True:
                if State.interrupted:
                    logger.info("[TTSWorker] Interrupting audio")
                    play_proc.terminate()
                    return False

                try:
                    await asyncio.wait_for(play_proc.communicate(), timeout=0.05)
                    return play_proc.returncode == 0
                except asyncio.TimeoutError:
                    continue
        finally:
            pass

    @profile
    def stop(self):
        logger.info("[TTSWorker] Stopping thread...")
        self.running.clear()
        self.loop.call_soon_threadsafe(self.loop.stop)
