import logging


def configure(debug: bool = False):
    """Configure logging for TTS service."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"
    )


logger = logging.getLogger("tts")
