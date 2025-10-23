import threading
import queue
import re
import time
from utils.logger_config import logger
from assistant.memory.context_manager import ContextManager
from state.state import State
from assistant.actions.send_notes import send_summary_after_speaking
from config.intent_and_sentence_config import (
    INTENT_PATTERNS,
    WAKE_WORD_VARIANTS,
    POSITIVE_CONFIRMATIONS as positive,
    NEGATIVE_CONFIRMATIONS as negative,
    SKIP_CONFIRMATIONS_PHRASES,
    ASSISTANT_SENTENCES,
)
from utils.profiling import profile

class WakeWordListenerThread(threading.Thread):
    def __init__(self, stt_queue, intent_queue, tts_queue, transcript_manager, next_meeting, graph, wake_word="MIRA", segment_wait_time=5):
        super().__init__(name="WakeWordListener")
        self.stt_queue = stt_queue
        self.intent_queue = intent_queue
        self.running = threading.Event()
        self.running.set()
        self.wake_word = wake_word.lower()
        self.wake_word_variants = WAKE_WORD_VARIANTS
        self.segment_wait_time = segment_wait_time
        self.post_wake_segments = []
        self.capturing_post_wake = False
        self.wake_buffer = []
        self.last_capture_time = None
        self.tts_queue = tts_queue
        self.transcript_manager = transcript_manager
        self.next_meeting = next_meeting
        self.graph = graph

    @profile
    def run(self):
        logger.info("[WakeWord] Listener started.")
        while self.running.is_set():
            try:
                result = self.stt_queue.get(timeout=1.0)
                text = result.get("text", "").strip().lower()
                ts = result.get("timestamp", time.time())

                if not text:
                    continue

                elif (State.follow_up_confirmation or State.file_request_confirmation):
                    if (self.detect_confirmation(text) == 1 or State.deck_attachment_confirmation):
                        if State.follow_up_confirmation:
                            State.follow_up_requested = True
                            State.follow_up_text = text
                            State.follow_up_confirmation = False
                            sentence = ASSISTANT_SENTENCES["follow_up_yes"]
                            self.tts_queue.put({"sentence": sentence, "post_action": lambda: send_summary_after_speaking(transcript_manager=self.transcript_manager, next_meeting=self.next_meeting, graph=self.graph)})
                            ContextManager.update_context(sentence, speaker="Assistant")
                            self.transcript_manager.add_entry("Assistant", sentence)
                        if State.file_request_confirmation:
                            State.files_requested = True
                            State.file_request_confirmation = False
                            State.deck_attachment_confirmation = False
                            sentence = ASSISTANT_SENTENCES["files_with_deck"]
                            ContextManager.update_context(sentence, speaker="Assistant")
                            self.transcript_manager.add_entry("Assistant", sentence)
                            self.ask_followup_after_email()
                            continue

                    elif self.detect_confirmation(text) == 0:
                        if State.follow_up_confirmation:
                            State.follow_up_requested = False
                            State.follow_up_text = text
                            State.follow_up_confirmation = False
                            sentence = ASSISTANT_SENTENCES["follow_up_no"]
                            self.tts_queue.put({"sentence": sentence, "post_action": lambda: send_summary_after_speaking(transcript_manager=self.transcript_manager, next_meeting=self.next_meeting, graph=self.graph)})
                            ContextManager.update_context(sentence, speaker="Assistant")
                            self.transcript_manager.add_entry("Assistant", sentence)
                        if State.file_request_confirmation:
                            State.files_requested = False
                            State.file_request_confirmation = False
                            sentence = ASSISTANT_SENTENCES["files_without_deck"]
                            ContextManager.update_context(sentence, speaker="Assistant")
                            self.transcript_manager.add_entry("Assistant", sentence)
                            self.ask_followup_after_email()
                            continue
                    elif self.detect_confirmation(text) == -1:
                        continue

                else:
                    logger.debug(f"[WakeWord] STT input: {text}")

                    if self.capturing_post_wake:
                        self.post_wake_segments.append(text)
                        logger.info(f"[WakeWord] Wake word phrase captured: {self.post_wake_segments}")
                        self.process_wake_phrase(self.wake_buffer, self.post_wake_segments)
                        self.post_wake_segments = []
                        self.wake_buffer = []
                        self.capturing_post_wake = False
                    elif self.contains_wake_word(text):
                        logger.info(f"[WakeWord] Detected wake word in: {text}")
                        self.wake_buffer = [text]
                        self.process_wake_phrase(self.wake_buffer, self.post_wake_segments)
                        self.capturing_post_wake = False
                        self.last_capture_time = time.time()
                    else:
                        ContextManager.update_context(text, speaker="Participant")

                t_intent = self.match_intent(text)
                if t_intent == "follow_up":
                    State.follow_up_mentioned = True
                    State.follow_up_text = text
                    State.follow_up_requested = True

            except queue.Empty:
                continue

    @profile
    def contains_wake_word(self, text: str) -> bool:
        for variant in self.wake_word_variants:
            if re.search(rf"\b{re.escape(variant)}\b", text):
                return True
        return "hey mira" in text

    @profile
    def detect_confirmation(self, text: str) -> int:
        text_lower = text.lower()
        if text_lower in [phrase.lower() for phrase in SKIP_CONFIRMATIONS_PHRASES]:
            return -1
        elif any(keyword in text_lower for keyword in positive):
            return 1
        else:
            return 0

    @profile
    def process_wake_phrase(self, before_segments, after_segments):
        combined = " ".join(before_segments + after_segments).lower()
        intent = self.match_intent(combined)
        logger.info(f"[WakeWord] Detected intent: {intent}")
        self.intent_queue.put({
            "text": combined,
            "intent": intent,
            "timestamp": time.time(),
        })

    @profile
    def match_intent(self, phrase: str) -> str:
        for intent, patterns in INTENT_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, phrase):
                    return intent
        return "general_query"

    @profile
    def ask_followup_after_email(self):
        sentence = ASSISTANT_SENTENCES["ask_follow_up"]
        if not State.follow_up_mentioned:
            self.tts_queue.put({"sentence": sentence})
            ContextManager.update_context(sentence, speaker="Assistant")
            self.transcript_manager.add_entry("Assistant", sentence)
            State.follow_up_confirmation = True
        else:
            sentence = ASSISTANT_SENTENCES["follow_up_yes"]
            self.tts_queue.put({"sentence": sentence, "post_action": lambda: send_summary_after_speaking(transcript_manager=self.transcript_manager, next_meeting=self.next_meeting, graph=self.graph)})
            State.follow_up_mentioned = False

    @profile
    def stop(self):
        logger.info("[WakeWord] Stopping...")
        self.running.clear()
