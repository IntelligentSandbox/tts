import argparse
import os

import uvicorn
from echo_common import configure_logging, logger, service_version

from api import make_app
from config import load_cfg

DEFAULT_CFG = os.path.join(os.path.dirname(__file__), "private", "config.yaml")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfg", default=os.getenv("CFG", DEFAULT_CFG))
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    configure_logging(debug=args.debug)
    logger.info(f"debug={args.debug}")
    logger.info(f"config: {args.cfg}")

    try:
        cfg = load_cfg(args.cfg)
    except Exception as e:
        logger.exception(f"failed to load config: {e}")
        raise

    host = args.host or cfg.get("server", {}).get("host", "0.0.0.0")
    port = args.port or cfg.get("server", {}).get("port", 47100)

    app = make_app(cfg, args.cfg)

    logger.info(f"service version: {service_version(__file__)}")

    uvicorn.run(app, host=host, port=port, log_level="debug" if args.debug else "info")
