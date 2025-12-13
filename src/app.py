import os
import argparse
import uvicorn
import yaml
from api import make_app
from log import TTSLogger


def load_cfg(p):
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cfg",
        default=os.getenv(
            "CFG",
            os.path.join(os.path.dirname(__file__), "private", "config.yaml"),
        ),
    )
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--debug", action="store_true", help="debug tts")
    a = ap.parse_args()

    logger = TTSLogger(debug=a.debug)
    logger.info(f"debug={a.debug}")
    logger.info(f"config: {a.cfg}")

    try:
        cfg = load_cfg(a.cfg)
    except Exception as e:
        logger.exception(f"failed to load config: {e}")
        raise

    app = make_app(cfg, a.cfg)

    uvicorn.run(app, host=a.host, port=a.port, log_level="debug" if a.debug else "info")
