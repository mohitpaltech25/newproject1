from __future__ import annotations
import os
import json
import time
from typing import List, Dict
from config.config import Config
from utils.logger_config import logger

class TranscriptManager:
    def __init__(self) -> None:
        os.makedirs(Config.TRANSCRIPTS_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.run_id = ts
        self.txt_path = os.path.join(Config.TRANSCRIPTS_DIR, f"transcript_{ts}.txt")
        self.json_path = os.path.join(Config.TRANSCRIPTS_DIR, f"transcript_{ts}.json")
        self.entries: List[Dict] = []

    def add_entry(self, speaker: str, text: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {speaker}: {text}\n"
        with open(self.txt_path, "a", encoding="utf-8") as f:
            f.write(line)
        self.entries.append({
            "ts": time.time(),
            "speaker": speaker,
            "text": text,
        })

    def save_json(self) -> None:
        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(self.entries, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[TranscriptManager] Failed to save JSON: {e}")
