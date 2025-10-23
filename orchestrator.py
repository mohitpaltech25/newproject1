#assistant/orchestrator.py
import threading
import queue
import time
import os
from assistant.audio.virtualmic_setup import VirtualMic

# from assistant.audio.audio_recorder import AudioRecorderThread
from assistant.audio.audio_recorder_VAD import AudioRecorderThread
from assistant.audio.stt_worker import STTWorkerThread
from assistant.wakeword.wake_word_listener import WakeWordListenerThread
from assistant.intent.intent_handler import IntentHandlerThread
from assistant.wakeword.tts_worker import TTSWorkerThread

from assistant.memory.transcript_manager import TranscriptManager
from assistant.memory.context_manager import ContextManager

from utils.logger_config import logger
from utils.profiler import start_profiling, stop_profiling


def run_orchestrator(bot = None, graph = None , next_meeting=None):
    print("[orchestrator] started ")
    # Start global profiler early if enabled
    if os.getenv("MIRA_PROFILING", "0") == "1":
        try:
            start_profiling(
                workspace_root=os.getenv("WORKSPACE_ROOT", "/workspace"),
                report_path=os.getenv("MIRA_PROFILING_REPORT", "/workspace/logs/profiling_report.txt"),
                include_stdlib=False,
            )
            logger.info("[Orchestrator] Profiling enabled; report will be written at exit.")
        except Exception as e:
            logger.warning(f"[Orchestrator] Failed to start profiler: {e}")
    if not VirtualMic.verify_virtual_mic():
        print("[orchestartor] Virtual Mic not configured properly. Exiting.")
        return
    bot=bot
    graph=graph
    next_meeting = next_meeting
    audio_queue = queue.Queue(maxsize=50)
    stt_queue = queue.Queue(maxsize=50)
    transcript_queue=queue.Queue(maxsize=50)
    intent_queue=queue.Queue()
    tts_queue=queue.Queue(maxsize=20)
    
    threads=[]

    # Start audio recorder thread
    recorder = AudioRecorderThread(segment_duration=5, output_dir="audio_segments", device_name="Virtual-Sink.monitor")
    recorder.audio_queue = audio_queue
    recorder.start()
    threads.append(recorder)

    # Start STT worker thread
    stt_worker = STTWorkerThread(audio_queue=audio_queue, stt_queue=stt_queue, transcript_queue=transcript_queue, max_workers=3)
    stt_worker.start()
    threads.append(stt_worker)

    # Transcript manager to save transcripts
    transcript_manager = TranscriptManager()
    
    # wake word listener thread
    wake_word_listener=WakeWordListenerThread(stt_queue=stt_queue,intent_queue=intent_queue , tts_queue=tts_queue , transcript_manager=transcript_manager, next_meeting=next_meeting, graph=graph)
    wake_word_listener.start()
    threads.append(wake_word_listener)
    
    
    #intent handler thread
    intent_thread = IntentHandlerThread(intent_queue=intent_queue, tts_queue=tts_queue, transcript_manager=transcript_manager, bot=bot , graph=graph, next_meeting=next_meeting)
    intent_thread.start()
    threads.append(intent_thread)
    
    # Start TTS worker thread
    tts_thread = TTSWorkerThread(tts_queue=tts_queue)
    tts_thread.start()
    threads.append(tts_thread)
    
    print("next meeting url:",next_meeting['url'])
    if not bot.started:
        bot.run(next_meeting['url'])

    try:
        while True:
            if not bot.started:
                logger.info("[Orchestrator] Bot has exited. Stopping all threads.")
                break
            try:
                # Wait for transcribed text from STT worker
                result = transcript_queue.get(timeout=1.0)
                if result:
                    text = result.get("text", "")
                    logger.info(f"[Main] Received from transcript_queue: {text}")
                    timestamp = result.get("timestamp", time.time())
                    # Add to transcript (assume speaker is "User" for now)
                    transcript_manager.add_entry("Participant", text)
            except queue.Empty:
                pass
            except Exception as e:
                logger.error(f"[Main] Failed to process Transcript result: {e}")
                
                
    except KeyboardInterrupt:
        print("Stopping threads...")

    # Stop threads gracefully
    for thread in threads:
        thread.stop()
    for thread in threads:
        thread.join()
        logger.info(f"[Main] Thread stopped: {thread.name}")

    # Save final transcript json
    transcript_manager.save_json()
    bot.leave()
    # Stop profiler and flush report if enabled
    if os.getenv("MIRA_PROFILING", "0") == "1":
        try:
            stop_profiling(write_report=True)
            logger.info("[Orchestrator] Profiling report written.")
        except Exception as e:
            logger.warning(f"[Orchestrator] Failed to stop profiler: {e}")

if __name__ == "__main__":
    run_orchestrator()
