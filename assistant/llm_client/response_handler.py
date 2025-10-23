from typing import Iterable
from assistant.llm_client.generate_llm_response import generate_llm_response

class ResponseHandler:
    @staticmethod
    def stream_sentences(prompt: str) -> Iterable[str]:
        for chunk in generate_llm_response(prompt):
            yield chunk
