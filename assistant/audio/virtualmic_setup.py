import shutil

class VirtualMic:
    @staticmethod
    def verify_virtual_mic() -> bool:
        # Stub: check tools exist
        return shutil.which("paplay") is not None and shutil.which("pactl") is not None
