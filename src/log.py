import sys
import logging
from typing import Optional


class TTSLogger:
    def __init__(
        self, name: str = "tts", log_file: str = "tts-error.log", debug: bool = False
    ):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG if debug else logging.INFO)

        ch = logging.StreamHandler(stream=sys.stdout)
        ch.setLevel(logging.DEBUG if debug else logging.INFO)

        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.ERROR)

        fmt = logging.Formatter(f"%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        ch.setFormatter(fmt)
        fh.setFormatter(fmt)

        if not self.logger.hasHandlers():
            self.logger.addHandler(ch)
            self.logger.addHandler(fh)

    def info(self, msg, *args, **kwargs):
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self.logger.error(msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self.logger.exception(msg, *args, **kwargs)
