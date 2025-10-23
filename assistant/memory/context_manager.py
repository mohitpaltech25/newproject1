from __future__ import annotations
from typing import List, Dict
import time
from utils.logger_config import logger

class ContextManager:
    buffer: List[Dict] = []
    max_len: int = 50

    @classmethod
    def update_context(cls, text: str, speaker: str = "Participant") -> None:
        if not text:
            return
        cls.buffer.append({
            "ts": time.time(),
            "speaker": speaker,
            "text": text,
        })
        if len(cls.buffer) > cls.max_len:
            # naive trim; summarization can be added later
            cls.buffer = cls.buffer[-cls.max_len :]
        logger.debug(f"[Context] {speaker}: {text}")

    @classmethod
    def get_recent(cls, n: int = 10) -> List[Dict]:
        return cls.buffer[-n:]
