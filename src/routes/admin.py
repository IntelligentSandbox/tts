import secrets
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

import db
import mod
import secrets_util as sec
import sfx
import tts as eng
from roles import audit, need, who


async def _mod_on():
    if not mod.mod_enabled():
        raise HTTPException(400, "moderation disabled")


def build(cfg):
    r = APIRouter(prefix="/api/admin", dependencies=[need("admin")])
    m = APIRouter(
        prefix="/api/admin/mod",
        dependencies=[need("mod"), Depends(_mod_on)],
    )

    @r.post("/reload")
    def reload_all(req: Request):
        audit(req, "reloaded voices, sounds and mod terms")
        sfx.init_sfx_aliases(cfg)
        out = {"voices": eng.reload()}

        if mod.mod_enabled():
            out["mod"] = mod.mod_reload()

        return out

    @m.get("")
    def mod_state():
        return {"terms": mod.mod_list(), **mod.mod_mode()}

    @m.post("")
    async def mod_update(req: Request):
        j = await req.json()

        if j.get("reload"):
            mod.mod_reload()

        if "censoring" in j:
            mod.mod_set_censoring(j["censoring"])

        if j.get("mode"):
            try:
                mod.mod_set_mode(j["mode"])
            except ValueError:
                raise HTTPException(400, "bad mode") from None

        for term in j.get("add") or []:
            mod.mod_add(term.strip())
            audit(req, "added a mod term")

        for term in j.get("remove") or []:
            mod.mod_remove(term.strip())
            audit(req, "removed a mod term")

        if j.get("mode") or "censoring" in j:
            audit(req, f"set mod mode {mod.mod_mode()}")

        return {"terms": mod.mod_list(), **mod.mod_mode()}

    @r.get("/mappings")
    def list_mappings(req: Request):
        maps = sec.list_oauth_mappings(
            None,
            req.app.state.cfg.get("secrets_file"),
            base_dir=req.app.state.config_dir,
        )
        return {"mappings": maps}

    @r.put("/mappings/{provider}/{remote}")
    async def put_mapping(req: Request, provider, remote):
        j = await req.json()
        role = (j.get("role") or "").strip()

        if not provider or not remote or role not in ("admin", "mod"):
            raise HTTPException(400, "bad mapping")

        sec.save_oauth_mapping(
            provider,
            remote,
            role,
            req.app.state.cfg.get("secrets_file"),
            base_dir=req.app.state.config_dir,
        )
        audit(req, f"mapped {provider} {remote} to {role}")
        return {"ok": True}

    @r.delete("/mappings/{provider}/{remote}")
    def delete_mapping(req: Request, provider, remote):
        if sec.delete_oauth_mapping(
            provider,
            remote,
            req.app.state.cfg.get("secrets_file"),
            base_dir=req.app.state.config_dir,
        ):
            audit(req, f"unmapped {provider} {remote}")
            return {"ok": True}

        raise HTTPException(404, "mapping not found")

    @r.get("/embeds")
    def list_embeds():
        out = []

        for e in db.list_embeds():
            tk = db.get_token(e.get("jti")) or {}
            out.append(
                {
                    "embed_id": e.get("embed_id"),
                    "url": f"/api/overlay?embed={e.get('embed_id')}",
                    "origin": e.get("origin"),
                    "created_at": e.get("created_at"),
                    "note": e.get("note"),
                    "expires": tk.get("expires"),
                    "revoked": tk.get("revoked", False),
                }
            )

        return {"embeds": out}

    @r.post("/embeds")
    async def create_embed(req: Request):
        j = await req.json()
        ttl = int(j.get("ttl", 3600))

        if ttl < 0:
            raise HTTPException(400, "bad ttl")

        note = j.get("note") or ""
        jti = uuid.uuid4().hex
        now = int(time.time())
        # a zero ttl never expires
        exp = now + ttl if ttl else 0
        db.insert_token(jti, exp, who(req), now, note)
        # generate a longer unpredictable embed id
        embed_id = secrets.token_urlsafe(18)
        origin = (j.get("origin") or "") or None
        db.insert_embed(embed_id, jti, now, note, origin)
        audit(req, f"created embed {embed_id[:6]}...")
        return {
            "embed_id": embed_id,
            "url": f"/api/overlay?embed={embed_id}",
            "expires": int(exp),
        }

    @r.delete("/embeds/{embed_id}")
    def delete_embed(req: Request, embed_id):
        em = db.get_embed(embed_id)

        if not em:
            raise HTTPException(404, "embed not found")

        # an overlay already holding the token keeps working unless it is revoked
        db.revoke_token(em.get("jti"))
        db.delete_embed(embed_id)
        audit(req, f"deleted embed {embed_id[:6]}...")
        return {"ok": True}

    # the mod section is its own router so it keeps the mod role (not admin)
    return [r, m]
