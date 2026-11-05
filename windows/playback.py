from ctypes import WinDLL, create_unicode_buffer


class Player:
    """One Windows MCI playback device for the lifetime of the workbench."""

    def __init__(self):
        self.api = WinDLL("winmm")
        self.opened = False

    def command(self, command):
        result = create_unicode_buffer(256)
        error = self.api.mciSendStringW(command, result, len(result), None)
        if error:
            self.api.mciGetErrorStringW(error, result, len(result))
            raise OSError(result.value or f"MCI error {error}")
        return result.value

    def play(self, path):
        self.stop()
        self.command(f'open "{path}" type mpegvideo alias tts_audio')
        self.opened = True
        try:
            self.command("play tts_audio from 0")
        except OSError:
            self.stop()
            raise

    def playing(self):
        return self.opened and self.command("status tts_audio mode") == "playing"

    def stop(self):
        if self.opened:
            self.command("close tts_audio")
            self.opened = False
