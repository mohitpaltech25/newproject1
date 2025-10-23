from assistant.memory.context_manager import ContextManager
from assistant.llm_client.response_handler import ResponseHandler

async def handle(tts_queue, transcript_manager, user_text: str):
    for sentence in ResponseHandler.stream_sentences(user_text):
        tts_queue.put({"sentence": sentence})
        ContextManager.update_context(sentence, speaker="Assistant")
        transcript_manager.add_entry("Assistant", sentence)
