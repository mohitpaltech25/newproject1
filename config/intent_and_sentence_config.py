import re

# Wake word variants
WAKE_WORD_VARIANTS = [
    "mira", "hey mira", "hi mira", "okay mira", "ok mira"
]

# Intent regex patterns
INTENT_PATTERNS = {
    "introduce": [r"\bintroduce\b", r"\bintroduce yourself\b"],
    "leave_call": [r"\bleave( the)? call\b", r"\bdrop off\b", r"\bleave now\b"],
    "send_docs_and_deck": [r"send (the )?(docs|documents|notes).*(deck|slides)", r"send deck and (docs|notes)"],
    "send_docs": [r"send (the )?(docs|documents|notes)", r"share (the )?notes"],
    "follow_up": [r"schedule (a )?follow(-| )?up", r"set up (a )?follow up"],
}

# Confirmation phrases
POSITIVE_CONFIRMATIONS = [
    "yes", "sure", "please", "go ahead", "yep", "do it"
]
NEGATIVE_CONFIRMATIONS = [
    "no", "nope", "not now", "don't"
]
SKIP_CONFIRMATIONS_PHRASES = [
    "skip", "ignore", "not needed"
]

# Assistant sentences used by wake word listener and actions
ASSISTANT_SENTENCES = {
    "ask_follow_up": "Would you like me to schedule a follow-up?",
    "follow_up_yes": "Alright, I will schedule a follow-up and send a summary.",
    "follow_up_no": "Okay, I will just send the meeting summary.",
    "files_with_deck": "I will send the documents along with the deck after the call.",
    "files_without_deck": "I will send the documents after the call without the deck.",
}
