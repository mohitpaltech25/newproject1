import logging
import os
from logging.handlers import RotatingFileHandler

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR = os.getenv("LOG_DIR", "/workspace/logs")
LOG_PATH = os.path.join(LOG_DIR, "mira.log")

os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("MIRA")
logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

# Console handler
ch = logging.StreamHandler()
ch.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
ch_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
ch.setFormatter(ch_formatter)

# File handler
fh = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3)
fh.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
fh_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(threadName)s %(module)s:%(lineno)d - %(message)s")
fh.setFormatter(fh_formatter)

# Avoid duplicate handlers
if not logger.handlers:
    logger.addHandler(ch)
    logger.addHandler(fh)
