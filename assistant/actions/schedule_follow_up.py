from assistant.memory.context_manager import ContextManager


def handle(tts_queue, transcript_manager):
    sentence = "I'll schedule a follow-up meeting and send a calendar invite."
    tts_queue.put({"sentence": sentence})
    ContextManager.update_context(sentence, speaker="Assistant")
    transcript_manager.add_entry("Assistant", sentence)
