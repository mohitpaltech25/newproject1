import os

class Config:
    # URLs and APIs
    stt_url: str = os.getenv("STT_URL", "http://localhost:8000/transcribe")

    # TTS
    TTS_VOICE: str = os.getenv("TTS_VOICE", "en-US-AriaNeural")

    # Temp and output directories
    BASE_DIR: str = os.getenv("BASE_DIR", "/workspace")
    TEMP_DIR: str = os.path.join(BASE_DIR, "temp")
    AUDIO_SEGMENTS_DIR: str = os.path.join(BASE_DIR, "audio_segments")
    TRANSCRIPTS_DIR: str = os.path.join(BASE_DIR, "meeting_transcripts")
    LOG_DIR: str = os.path.join(BASE_DIR, "logs")

    @staticmethod
    def ensure_dirs():
        for d in [Config.TEMP_DIR, Config.AUDIO_SEGMENTS_DIR, Config.TRANSCRIPTS_DIR, Config.LOG_DIR]:
            os.makedirs(d, exist_ok=True)

Config.ensure_dirs()
