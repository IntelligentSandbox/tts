import logging

LOG_FMT = "%(asctime)s %(levelname)s %(message)s"
TIME_FMT = "%H:%M:%S"


def configure(debug=False):
    """Configure logging."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format=LOG_FMT, datefmt=TIME_FMT)


logger = logging.getLogger("tts")
