from collections.abc import Callable
from dataclasses import dataclass

from echo_common import logger
from fastapi import APIRouter, HTTPException, Request

import config_store
from routes.models import ProfileReq, SfxAliasReq, VoiceAliasReq


def _normal_name(n):
    return (n or "").strip().lower()


@dataclass(frozen=True)
class EntryKind:
    model: type
    value_of: Callable
    setter: Callable
    deleter: Callable
    section: str


def build(engine, entries):
    r = APIRouter(prefix="/api/catalog")
    res = engine.resolver
    sounds = engine.sounds

    kinds = {
        "voice_alias": EntryKind(
            model=VoiceAliasReq,
            value_of=lambda b: b.voice,
            setter=res.set_alias,
            deleter=res.del_alias,
            section="aliases",
        ),
        "profile": EntryKind(
            model=ProfileReq,
            value_of=lambda b: b.model_dump(exclude_none=True),
            setter=res.set_profile,
            deleter=res.del_profile,
            section="profiles",
        ),
        "sfx_alias": EntryKind(
            model=SfxAliasReq,
            value_of=lambda b: b.target_id,
            setter=sounds.set_alias,
            deleter=sounds.del_alias,
            section="sfx_aliases",
        ),
    }

    def _everything():
        return {
            "voices": engine.voices.all(),
            "voice_aliases": res.aliases,
            "profiles": res.profiles,
            "presets": sorted(res.presets),
            "sounds": sounds.index(),
            "sfx_aliases": dict(sounds.aliases),
            # only what the panel made is safe to remove (the rest is hand written)
            "removable": {
                kind: sorted(entries.names(spec.section))
                for kind, spec in kinds.items()
            },
        }

    @r.get("")
    def get_catalog():
        return _everything()

    @r.post("/reload")
    def reload_all():
        logger.info("[catalog] reloaded voices, sounds and mod terms")
        engine.reload()
        return _everything()

    def put_route(spec):
        def put_entry(name, req: Request, body: spec.model):
            n = _normal_name(name)

            if not n:
                raise HTTPException(400, "name required")

            value = spec.value_of(body)

            try:
                spec.setter(n, value)
            except ValueError:
                raise HTTPException(400, "bad entry") from None

            # panel edits belong in the config or they vanish on the next restart
            config_store.save_entry(req.app.state.cfg_path, spec.section, n, value)
            entries.mark(spec.section, n)
            return _everything()

        return put_entry

    def delete_route(spec):
        def delete_entry(name, req: Request):
            n = _normal_name(name)

            if n not in entries.names(spec.section):
                raise HTTPException(403, "defined in the config (edit the file)")

            spec.deleter(n)
            config_store.delete_entry(req.app.state.cfg_path, spec.section, n)
            entries.unmark(spec.section, n)
            return _everything()

        return delete_entry

    # Each kind gets its own route so its body is checked against its own model
    for kind, spec in kinds.items():
        path = f"/{kind}/{{name}}"
        r.add_api_route(path, put_route(spec), methods=["PUT"])
        r.add_api_route(path, delete_route(spec), methods=["DELETE"])

    return r
