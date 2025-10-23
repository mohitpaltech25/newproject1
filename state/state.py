class State:
    # Speaking/interruption state
    speaking: bool = False
    interrupted: bool = False

    # Follow-up/email flow flags
    follow_up_confirmation: bool = False
    follow_up_mentioned: bool = False
    follow_up_requested: bool = False
    follow_up_text: str | None = None

    file_request_confirmation: bool = False
    files_requested: bool = False
    deck_attachment_confirmation: bool = False
