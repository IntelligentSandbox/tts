import os
from collections import deque

from echo_common import resolve_path, service_root
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import db
import secrets_util as sec
import sfx
import tts as eng
from routes import admin, auth, catalog, overlay, queue, synth

SERVICE_ROOT = service_root(__file__)


def make_app(cfg, cfg_path=None):
    global app, Q
    Q = deque(maxlen=256)

    # all relative config paths resolve against the service root
    config_dir = SERVICE_ROOT

    app = FastAPI(title="tts")
    app.state.config_dir = config_dir
    app.state.cfg_path = cfg_path
    app.state.public_dir = os.path.join(os.path.dirname(__file__), "public")
    eng.init(cfg, base_dir=config_dir)
    sfx.init_sfx_aliases(cfg)
    eng.warmup()

    sd = cfg.get(
        "sounds_dir",
        os.path.join(os.path.dirname(__file__), "..", "sounds"),
    )

    if os.path.isdir(sd):
        app.mount("/sounds", StaticFiles(directory=sd), name="sounds")

    s = cfg.get("session") or {}
    app.state.cfg = cfg

    secrets_file = (
        s.get("file")
        or (cfg.get("auth") or {}).get("file")
        or cfg.get("secrets_file")
        or os.path.join(os.path.dirname(__file__), "private", "secrets.yaml")
    )

    secret = s.get("secret") or sec.ensure_session_secret(
        secrets_file, base_dir=config_dir
    )

    db.init_db(resolve_path(cfg.get("db_file", "data/tts.db"), SERVICE_ROOT))

    app.state.jwt_secret = cfg.get("jwt_secret") or sec.ensure_jwt_secret(
        secrets_file, base_dir=config_dir
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie=s.get("cookie_name", "sid"),
        same_site=s.get("same_site", "lax"),
        https_only=bool(s.get("secure", False)),
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get("cors_allow_origins") or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    routers = [
        synth.build(cfg),
        catalog.build(cfg),
        queue.build(Q),
        auth.build(),
        overlay.build(),
        *admin.build(cfg),
    ]

    for r in routers:
        app.include_router(r)

    if os.path.isdir(app.state.public_dir):
        app.mount(
            "/", StaticFiles(directory=app.state.public_dir, html=True), name="ui"
        )

    return app
