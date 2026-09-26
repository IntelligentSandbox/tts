from fastapi import APIRouter, HTTPException, Request

import config_store
import db
import sfx
import tts as eng
from roles import audit, need


def build(cfg):
    r = APIRouter(prefix="/api/catalog")

    def _value_for_voice_alias(j):
        v = (j.get("voice") or "").strip()

        if not v:
            raise HTTPException(400, "voice required")

        return v

    def _value_for_sfx_alias(j):
        t = (j.get("target_id") or "").strip()

        if not t:
            raise HTTPException(400, "target_id required")

        return t

    kinds = {
        "voice_alias": (
            _value_for_voice_alias,
            eng.set_alias,
            eng.del_alias,
            "aliases",
            lambda n: (n or "").strip().lower(),
        ),
        "profile": (
            lambda j: {k: v for k, v in j.items() if k != "name"},
            eng.set_profile,
            eng.del_profile,
            "profiles",
            lambda n: (n or "").strip().lower(),
        ),
        "sfx_alias": (
            _value_for_sfx_alias,
            lambda n, v: sfx.set_sfx_alias(n, v, cfg),
            sfx.del_sfx_alias,
            "sfx_aliases",
            lambda n: n,
        ),
    }

    def _kind(kind):
        if kind not in kinds:
            raise HTTPException(404, "unknown kind")

        return kinds[kind]

    def _everything():
        return {
            "voices": eng.voices(),
            "voice_aliases": eng.get_aliases(),
            "profiles": eng.get_profiles(),
            "presets": sorted(eng.presets),
            "sounds": sfx.get_sfx_index(cfg),
            "sfx_aliases": sfx.get_sfx_aliases(),
            # only what the panel made is safe to remove (the rest is hand written)
            "removable": {
                kind: sorted(db.panel_entries(section))
                for kind, (_, _, _, section, _) in kinds.items()
            },
        }

    @r.get("", dependencies=[need("service")])
    def get_catalog():
        return _everything()

    @r.put("/{kind}/{name}", dependencies=[need("admin")])
    async def put_entry(kind, name, req: Request):
        value_of, setter, _, section, norm = _kind(kind)
        n = norm(name)

        if not n:
            raise HTTPException(400, "name required")

        try:
            j = await req.json()
        except Exception:
            raise HTTPException(400, "invalid json") from None

        value = value_of(j)

        try:
            setter(n, value)
        except ValueError:
            raise HTTPException(400, "bad entry") from None

        # panel edits belong in the config or they vanish on the next restart
        config_store.save_entry(req.app.state.cfg_path, section, n, value)
        db.mark_panel_entry(section, n)
        audit(req, f"set {kind} {n}")
        return _everything()

    @r.delete("/{kind}/{name}", dependencies=[need("admin")])
    def delete_entry(kind, name, req: Request):
        _, _, deleter, section, norm = _kind(kind)
        n = norm(name)

        if n not in db.panel_entries(section):
            raise HTTPException(403, "defined in the config (edit the file)")

        deleter(n)
        config_store.delete_entry(req.app.state.cfg_path, section, n)
        db.unmark_panel_entry(section, n)
        audit(req, f"deleted {kind} {n}")
        return _everything()

    return r
