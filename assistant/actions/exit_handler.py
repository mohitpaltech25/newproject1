from assistant.memory.context_manager import ContextManager

def handle(tts_queue, transcript_manager, bot):
    sentence = "Okay, I will leave the call now."
    tts_queue.put({"sentence": sentence, "post_action": bot.leave})
    ContextManager.update_context(sentence, speaker="Assistant")
    transcript_manager.add_entry("Assistant", sentence)
