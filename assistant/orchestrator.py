import threading
import queue
import time
from assistant.audio.virtualmic_setup import VirtualMic
from assistant.audio.audio_recorder_VAD import AudioRecorderThread
from assistant.audio.stt_worker import STTWorkerThread
from assistant.wakeword.wake_word_listener import WakeWordListenerThread
from assistant.intent.intent_handler import IntentHandlerThread
from assistant.wakeword.tts_worker import TTSWorkerThread
from assistant.memory.transcript_manager import TranscriptManager
from assistant.memory.context_manager import ContextManager
from utils.logger_config import logger


def run_orchestrator(bot=None, graph=None, next_meeting=None):
    print("[orchestrator] started ")
    if not VirtualMic.verify_virtual_mic():
        print("[orchestartor] Virtual Mic not configured properly. Exiting.")
        return
    audio_queue = queue.Queue(maxsize=50)
    stt_queue = queue.Queue(maxsize=50)
    transcript_queue = queue.Queue(maxsize=50)
    intent_queue = queue.Queue()
    tts_queue = queue.Queue(maxsize=20)

    threads = []

    # Start audio recorder thread
    recorder = AudioRecorderThread(segment_duration=5, output_dir="audio_segments", device_name="Virtual-Sink.monitor")
    recorder.audio_queue = audio_queue
    recorder.start()
    threads.append(recorder)

    # Start STT worker thread
    stt_worker = STTWorkerThread(audio_queue=audio_queue, stt_queue=stt_queue, transcript_queue=transcript_queue, max_workers=3)
    stt_worker.start()
    threads.append(stt_worker)

    transcript_manager = TranscriptManager()

    # wake word listener thread
    wake_word_listener = WakeWordListenerThread(
        stt_queue=stt_queue,
        intent_queue=intent_queue,
        tts_queue=tts_queue,
        transcript_manager=transcript_manager,
        next_meeting=next_meeting,
        graph=graph,
    )
    wake_word_listener.start()
    threads.append(wake_word_listener)

    # intent handler thread
    intent_thread = IntentHandlerThread(
        intent_queue=intent_queue,
        tts_queue=tts_queue,
        transcript_manager=transcript_manager,
        bot=bot,
        graph=graph,
        next_meeting=next_meeting,
    )
    intent_thread.start()
    threads.append(intent_thread)

    # Start TTS worker thread
    tts_thread = TTSWorkerThread(tts_queue=tts_queue)
    tts_thread.start()
    threads.append(tts_thread)

    if next_meeting and bot and not getattr(bot, "started", False):
        url = next_meeting.get("url")
        print("next meeting url:", url)
        if url:
            bot.run(url)

    try:
        while True:
            if bot and not getattr(bot, "started", True):
                logger.info("[Orchestrator] Bot has exited. Stopping all threads.")
                break
            try:
                result = transcript_queue.get(timeout=1.0)
                if result:
                    text = result.get("text", "")
                    logger.info(f"[Main] Received from transcript_queue: {text}")
                    timestamp = result.get("timestamp", time.time())
                    transcript_manager.add_entry("Participant", text)
            except queue.Empty:
                pass
            except Exception as e:
                logger.error(f"[Main] Failed to process Transcript result: {e}")

    except KeyboardInterrupt:
        print("Stopping threads...")

    for thread in threads:
        thread.stop()
    for thread in threads:
        thread.join()
        logger.info(f"[Main] Thread stopped: {thread.name}")

    transcript_manager.save_json()
    if bot:
        try:
            bot.leave()
        except Exception:
            pass

if __name__ == "__main__":
    run_orchestrator()
