#assistant/audio/stt_worker.py
import threading
import queue
import requests
import time
import os
from concurrent.futures import ThreadPoolExecutor
from utils.logger_config import logger
from config.config import Config
from assistant.memory.transcript_manager import TranscriptManager
from state.state import State

class STTWorkerThread(threading.Thread):
    def __init__(self, audio_queue, stt_queue, transcript_queue, name="STTWorker", max_workers=3):
        super().__init__(name=name)
        self.audio_queue = audio_queue
        self.stt_queue = stt_queue
        self.transcript_queue = transcript_queue
        self.running = threading.Event()
        self.running.set()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)

    def run(self):
        logger.info("[STT] STT worker thread started.")
        while self.running.is_set():
            if self.audio_queue.qsize() > 10:
                logger.warning(f"[STT] Audio queue high: {self.audio_queue.qsize()} items")
            try:
                audio_file = self.audio_queue.get(timeout=1.0)
                self.executor.submit(self.handle_file, audio_file)
            except queue.Empty:
                continue
            except Exception as e:
                logger.exception(f"[STT] Unexpected error: {e}")

    def handle_file(self, audio_file):
        start_time = time.time()
        text = self.transcribe_audio(audio_file)
        duration = time.time() - start_time
        # transcript_manager = TranscriptManager()

        if text :
            try:
                # transcript_manager.add_entry("User", text)
                self.stt_queue.put_nowait({
                    "file": audio_file,
                    "text": text,
                    "timestamp": start_time
                })
                self.transcript_queue.put_nowait({
                    "file": audio_file,
                    "text": text,
                    "timestamp": start_time
                })
                logger.info(f"[STT] File: {audio_file} | Duration: {duration:.2f}s | Text: {text}")
            except queue.Full:
                logger.warning(f"[STT] STT queue full. Dropping result of: {audio_file}")

        else:
            logger.warning(f"[STT] Empty result for {audio_file}")
        try:
            os.remove(audio_file)
        except Exception as e:
            logger.warning(f"[STT] Could not remove {audio_file}: {e}")

    def transcribe_audio(self, file_path):
        for attempt in range(3):
            try:
                with open(file_path, 'rb') as f:
                    files = {'file': f}
                    response = requests.post(Config.stt_url, files=files)
                if response.status_code == 200:
                    result = response.json()
                    return result.get("text", "").strip()
                
                logger.error(f"[STT] HTTP {response.status_code}: {response.text}")
                break
            except requests.RequestException as e:
                logger.warning(f"[STT] Attempt {attempt + 1}/3 failed for {file_path}: {e}")
                time.sleep(2 ** attempt)  # Exponential backoff
            except Exception as e:
                logger.exception(f"[STT] Transcription failed for {file_path}: {e}")
        return ""

    def stop(self):
        logger.info("[STT] Stopping...")
        self.running.clear()
        self.executor.shutdown(wait=True)
        