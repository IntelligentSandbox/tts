import os

from echo_common import resolve_path, service_root
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db import PanelEntries
from engine import Engine
from routes import catalog, moderation, synth

SERVICE_ROOT = service_root(__file__)


def make_app(cfg, cfg_path=None):
    # all relative config paths resolve against the service root
    engine = Engine(cfg, base_dir=SERVICE_ROOT)
    engine.voices.warmup()
    entries = PanelEntries(
        resolve_path(cfg.get("db_file", "data/tts.db"), SERVICE_ROOT)
    )

    app = FastAPI(title="tts")
    app.state.cfg_path = cfg_path

    if os.path.isdir(engine.sounds.dir):
        app.mount("/sounds", StaticFiles(directory=engine.sounds.dir), name="sounds")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get("cors_allow_origins") or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    routers = [
        synth.build(engine),
        catalog.build(engine, entries),
        moderation.build(engine.moderator),
    ]

    for r in routers:
        app.include_router(r)

    public_dir = os.path.join(SERVICE_ROOT, "public")

    if os.path.isdir(public_dir):
        app.mount("/", StaticFiles(directory=public_dir, html=True), name="ui")

    return app
