#assistant/intent/intent_handler.py
import asyncio
import threading
import queue
import time
from utils.logger_config import logger
from assistant.memory.context_manager import ContextManager
from assistant.memory.transcript_manager import TranscriptManager
from state.state import State

# Placeholder for future imports
# from actions import summary_generator, email_sender, scheduler
from assistant.llm_client.generate_llm_response import generate_llm_response  
from assistant.llm_client.response_handler import ResponseHandler
from assistant.actions import exit_handler, send_notes, introduction_handler, schedule_follow_up, general_query_handler


class IntentHandlerThread(threading.Thread):
    def __init__(self, intent_queue, tts_queue, transcript_manager, bot, graph, next_meeting):
        super().__init__(name="IntentHandler")
        self.intent_queue = intent_queue
        self.tts_queue = tts_queue
        self.transcript_manager = transcript_manager
        self.running = threading.Event()
        self.running.set()
        self.loop = asyncio.new_event_loop()
        self.bot = bot
        self.graph = graph
        self.next_meeting = next_meeting
        
    def run(self):
        asyncio.set_event_loop(self.loop)
        logger.info("[IntentHandler] Thread started.")
        self.loop.run_until_complete(self._main_loop())

    async def _main_loop(self):
        while self.running.is_set():
            try:
                data = self.intent_queue.get(timeout=1.0)
                intent = data.get("intent", "general_query")
                user_text = data.get("text", "").strip()

                logger.info(f"[IntentHandler] Intent: {intent} | Text: {user_text}")

                # Update context and transcript with user query
                ContextManager.update_context(user_text, speaker="Participant")
                # self.transcript_manager.add_entry("Participant", user_text)
                

                # # Handle intent
                if intent == "introduce":
                    introduction_handler.handle(tts_queue=self.tts_queue, transcript_manager=self.transcript_manager)
                    
                elif intent == "leave_call":
                    exit_handler.handle(tts_queue=self.tts_queue,transcript_manager=self.transcript_manager, bot=self.bot)
                elif intent == "send_docs_and_deck":
                    await send_notes.handle(tts_queue=self.tts_queue,
                                                transcript_manager=self.transcript_manager,
                                                bot=self.bot,
                                                graph=self.graph,
                                                next_meeting=self.next_meeting,
                                                attach_deck=True)

                elif intent == "send_docs":
                    await send_notes.handle(tts_queue=self.tts_queue,transcript_manager=self.transcript_manager, bot=self.bot , graph=self.graph , next_meeting=self.next_meeting)

                elif intent == "follow_up":
                    schedule_follow_up.handle(tts_queue=self.tts_queue,transcript_manager=self.transcript_manager)
                    
                elif intent == "general_query":
                    start_time = time.time()
                    await general_query_handler.handle(tts_queue=self.tts_queue,transcript_manager=self.transcript_manager,user_text=user_text)
                    duration = time.time()-start_time
                    logger.info(f"[IntentHandler] intent: {intent}| Duration: {duration:.2f}s  ")          

                else:
                    logger.warning(f"[IntentHandler] Unknown intent: {intent}")

            except queue.Empty:
                continue
            except Exception as e:
                logger.exception(f"[IntentHandler] Error: {e}")
                
    def stop(self):
        logger.info("[IntentHandler] Stopping thread...")
        self.running.clear()
        self.loop.call_soon_threadsafe(self.loop.stop)
