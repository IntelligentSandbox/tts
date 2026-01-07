import os
import argparse

import uvicorn

from api import make_app
from log import configure, logger
from config import load_cfg
from util import DEFAULT_HOST, DEFAULT_PORT

DEFAULT_CFG = os.path.join(os.path.dirname(__file__), "private", "config.yaml")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfg", default=os.getenv("CFG", DEFAULT_CFG))
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()

    configure(debug=a.debug)
    logger.info(f"debug={a.debug}")
    logger.info(f"config: {a.cfg}")

    try:
        cfg = load_cfg(a.cfg)
    except Exception as e:
        logger.exception(f"failed to load config: {e}")
        raise

    app = make_app(cfg, a.cfg)

    uvicorn.run(app, host=a.host, port=a.port, log_level="debug" if a.debug else "info")
