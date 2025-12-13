import os
from collections import deque
from fastapi import FastAPI, APIRouter, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse
import requests
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import secrets_util as sec
import tts as eng
import uuid
import json
import time
import secrets
import jwt
import db

MAX_SOUNDS = 10


ROLE_TREE = {
    "admin": {"admin", "mod", "tts", "push", "pull"},
    "mod": {"mod", "tts"},
    "tts": {"tts"},
    "push": {"push"},
    "pull": {"pull"},
    "overlay": {"tts", "pull"},
}


def _eff_from_session(req):
    eff = set()
    for r, grants in ROLE_TREE.items():
        if req.session.get(r):
            eff |= grants
    return eff


def _eff_from_key(k):
    if not k:
        return set()
    import tts as eng

    eff = set()
    for r, grants in ROLE_TREE.items():
        if eng.auth_ok(r, k):
            eff |= grants
    return eff


def need(role):
    async def dep(req: Request):
        import tts as eng

        if not eng.auth_enabled():
            return
        if role in _eff_from_session(req):
            return
        k = req.headers.get("x-api-key") or req.headers.get("authorization") or ""
        if k.lower().startswith("bearer "):
            k = k[7:]
        if role in _eff_from_key(k):
            return
        if k:
            try:
                pl = jwt.decode(k, req.app.state.jwt_secret, algorithms=["HS256"])
            except jwt.ExpiredSignatureError:
                raise HTTPException(401, "token expired")
            except Exception:
                pl = None
            if pl:
                jti = pl.get("jti")
                roles = pl.get("roles") or []
                if role in roles:
                    tk = db.get_token(jti)
                    if tk and tk.get("revoked"):
                        raise HTTPException(401, "revoked")
                    return
        raise HTTPException(401, "unauthorized")

    return Depends(dep)


def make_app(cfg, config_path: str | None = None):
    global app, Q
    Q = deque(maxlen=256)

    # derive base dir from provided config_path when available
    config_dir = None
    if config_path:
        try:
            config_dir = os.path.dirname(os.path.abspath(config_path))
        except Exception:
            config_dir = None
    else:
        possible = [
            os.getenv("CFG") or "",
            os.path.join(os.path.dirname(__file__), "private", "config.yaml"),
            os.path.join(os.path.dirname(__file__), "config.yaml"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml"),
            os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "private", "config.yaml"
            ),
            os.path.join(os.getcwd(), "config.yaml"),
        ]
        for p in possible:
            if not p:
                continue
            try:
                if os.path.exists(p):
                    config_dir = os.path.dirname(os.path.abspath(p))
                    break
            except Exception:
                continue
    app = FastAPI(title="tts")
    app.state.config_dir = config_dir
    eng.init(cfg, base_dir=config_dir)
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
    db.init_db(
        cfg.get(
            "db_file",
            os.path.join(os.path.dirname(__file__), "private", "data", "tts.db"),
        )
    )
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

    ori = cfg.get("cors_allow_origins", "*")
    if ori == "*":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[s.strip() for s in ori.split(",") if s.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    r = APIRouter(prefix="/api")

    @r.get("/sounds", dependencies=[need("tts")])
    def list_sounds():
        return {"index": eng.get_sfx_index(), "aliases": eng.get_sfx_aliases()}

    @r.post("/sfx_aliases", dependencies=[need("admin")])
    async def add_sfx_alias(req: Request):
        j = await req.json()
        eng.set_sfx_alias(
            (j.get("name") or "").strip().lower(), (j.get("target") or "").strip()
        )
        return {"aliases": eng.get_sfx_aliases()}

    @r.delete("/sfx_aliases/{name}", dependencies=[need("admin")])
    def del_sfx_alias(name: str):
        eng.del_sfx_alias(name)
        return {"aliases": eng.get_sfx_aliases()}

    @r.post("/tts_batch", dependencies=[need("tts")])
    async def tts_batch(req: Request):
        j = await req.json()
        parts = j.get("parts") or []
        fmt = (j.get("format") or "mp3").lower()
        norm = bool(
            j.get("normalize")
            if j.get("normalize") is not None
            else eng.cfg.get("normalize", False)
        )

        ls = j.get("length_scale")
        ns = j.get("noise_scale")
        nw = j.get("noise_w")
        ss = j.get("sentence_silence")
        spk = j.get("speaker_id")

        segs, rm, sfx_count = [], [], 0
        try:
            for p in parts:
                if "sfx" in p:
                    if sfx_count > MAX_SOUNDS:
                        continue
                    _, ap = eng._resolve_sfx(p.get("sfx"))
                    if not ap:
                        continue
                    wav48 = eng._to_48k_mono_wav(ap)
                    segs.append(wav48)
                    if wav48 != ap:
                        rm.append(wav48)
                    sfx_count += 1
                else:
                    txt = (p.get("text") or "").strip()
                    if not txt:
                        continue
                    reqv = (p.get("voice") or "").strip()
                    vid, _ = eng._resolve_voice_id(reqv)
                    wav, tmp = eng._render_tts_wav(txt, vid, ls, ns, nw, ss, spk, norm)
                    rm += tmp
                    wav48 = eng._to_48k_mono_wav(wav)
                    segs.append(wav48)
                    if wav48 != wav:
                        rm.append(wav48)

            if not segs:
                raise HTTPException(400, "empty parts")

            b, m = eng._concat_wavs(segs, fmt=fmt, bitrate=j.get("bitrate"))
            rid = uuid.uuid4().hex[:8]
            h = {
                "Content-Disposition": f'inline; filename="batch-{rid}.{"mp3" if m=="audio/mpeg" else "wav"}"',
                "Cache-Control": "no-store",
            }
            return Response(content=b, media_type=m, headers=h)
        finally:
            for pth in rm:
                try:
                    os.remove(pth)
                except:
                    pass

    @r.get("/peek", dependencies=[need("mod")])
    def peek():
        if not Q:
            return Response(status_code=204)
        it = Q.popleft()
        if not it.get("id"):
            it["id"] = uuid.uuid4().hex[:8]
        Q.appendleft(it)
        return dict(it)

    @r.delete("/queue/{qid}", dependencies=[need("mod")])
    def queue_delete(qid: str):
        if not qid:
            raise HTTPException(400, "bad id")
        n = 0
        tmp = []
        while Q:
            it = Q.popleft()
            if str(it.get("id")) == str(qid):
                n += 1
            else:
                tmp.append(it)
        for it in tmp:
            Q.append(it)
        return {"deleted": n}

    @r.post("/panel/login")
    async def panel_login(req: Request):
        import tts as eng

        if not eng.auth_enabled():
            raise HTTPException(400, "auth disabled")
        j = await req.json()
        role = (j.get("role") or "").strip()
        key = (j.get("key") or "").strip()
        if role not in ("admin", "mod"):
            raise HTTPException(400, "bad role")
        if not eng.auth_ok(role, key):
            raise HTTPException(401, "invalid key")
        if role == "admin":
            req.session["admin"] = True
            req.session["mod"] = True
            req.session["tts"] = True
            req.session["push"] = True
            req.session["pull"] = True
        else:
            req.session["mod"] = True
            req.session["tts"] = True
        return {"ok": True, "role": role}

    # OAuth: initiate login with provider (e.g., twitch)
    @r.get("/auth/login")
    def auth_login(provider: str = "twitch", req: Request = None):
        # read provider creds from secrets
        cfg = sec.get_oauth_provider(
            provider,
            (
                req.app.state.cfg.get("secrets_file")
                if req and req.app.state.cfg
                else None
            ),
            base_dir=(req.app.state.config_dir if req and req.app.state else None),
        )
        client_id = cfg.get("client_id")
        redirect = cfg.get("redirect_uri") or (req.url_for("overlay") if req else None)
        if not client_id or not redirect:
            raise HTTPException(400, "oauth not configured")
        # build twitch authorize URL
        if provider == "twitch":
            from urllib.parse import urlencode

            params = {
                "client_id": client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": "user:read:email",
            }
            url = "https://id.twitch.tv/oauth2/authorize?" + urlencode(params)
            return Response(status_code=302, headers={"Location": url})
        raise HTTPException(400, "unsupported provider")

    @r.get("/auth/callback")
    async def auth_callback(
        request: Request,
        code: str | None = None,
        state: str | None = None,
        provider: str = "twitch",
    ):
        # exchange code for token and fetch user identity
        cfg = sec.get_oauth_provider(
            provider,
            (
                request.app.state.cfg.get("secrets_file")
                if request and request.app.state.cfg
                else None
            ),
            base_dir=(
                request.app.state.config_dir if request and request.app.state else None
            ),
        )
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        redirect = cfg.get("redirect_uri")
        if not client_id or not client_secret or not redirect:
            raise HTTPException(400, "oauth not configured")
        if provider != "twitch":
            raise HTTPException(400, "unsupported provider")
        if not code:
            raise HTTPException(400, "missing code")
        # exchange code
        token_url = "https://id.twitch.tv/oauth2/token"
        try:
            r = requests.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect,
                },
                timeout=10,
            )
            r.raise_for_status()
            tok = r.json()
        except Exception as e:
            raise HTTPException(400, f"token exchange failed: {e}")
        access = tok.get("access_token")
        if not access:
            raise HTTPException(400, "no access token")
        # get user info
        try:
            hr = requests.get(
                "https://api.twitch.tv/helix/users",
                headers={
                    "Authorization": f"Bearer {access}",
                    "Client-Id": client_id,
                },
                timeout=10,
            )
            hr.raise_for_status()
            u = hr.json()
        except Exception as e:
            raise HTTPException(400, f"user lookup failed: {e}")
        # extract id/login
        data = u.get("data") or []
        if not data:
            raise HTTPException(400, "no user data")
        user = data[0]
        twitch_id = user.get("id")
        login = user.get("login")
        # store oauth identity in session so UI can fetch it (whoami)
        try:
            request.session[f"oauth_{provider}_id"] = str(twitch_id)
            request.session[f"oauth_{provider}_login"] = (login or "").lower()
        except Exception:
            pass
        # map to role if mapping exists
        mapped = sec.list_oauth_mappings(
            "twitch",
            (
                request.app.state.cfg.get("secrets_file")
                if request and request.app.state.cfg
                else None
            ),
            base_dir=(
                request.app.state.config_dir if request and request.app.state else None
            ),
        )
        role = mapped.get(str(twitch_id)) or mapped.get((login or "").lower())
        if role == "admin":
            request.session["admin"] = True
            request.session["mod"] = True
            request.session["tts"] = True
            request.session["push"] = True
            request.session["pull"] = True
        elif role == "mod":
            request.session["mod"] = True
            request.session["tts"] = True
        else:
            # unmapped: show simple HTML telling user to ask admin to map their account
            text = f"<html><body>Login OK (user={login}). Account not mapped to a role. Ask an admin to map your Twitch id {twitch_id} to a role.</body></html>"
            return HTMLResponse(text)
        return Response(status_code=302, headers={"Location": "/"})

    @r.get("/auth/me")
    def auth_me(provider: str = "twitch", req: Request = None):
        # return the last oauth identity stored in session (if any)
        prov = provider or "twitch"
        sid = None
        slogin = None
        try:
            sid = req.session.get(f"oauth_{prov}_id")
            slogin = req.session.get(f"oauth_{prov}_login")
        except Exception:
            pass
        if not sid and not slogin:
            return {"ok": False}
        return {"ok": True, "provider": prov, "id": sid, "login": slogin}

    @r.get("/auth/mappings", dependencies=[need("admin")])
    def auth_mappings():
        maps = sec.list_oauth_mappings(
            None, app.state.cfg.get("secrets_file"), base_dir=app.state.config_dir
        )
        return {"mappings": maps}

    @r.post("/auth/mapping", dependencies=[need("admin")])
    async def auth_mapping(req: Request):
        j = await req.json()
        provider = (j.get("provider") or "twitch").strip()
        remote = str(j.get("remote") or "").strip()
        role = (j.get("role") or "").strip()
        if not provider or not remote or role not in ("admin", "mod"):
            raise HTTPException(400, "bad mapping")
        sec.save_oauth_mapping(
            provider,
            remote,
            role,
            app.state.cfg.get("secrets_file"),
            base_dir=app.state.config_dir,
        )
        return {"ok": True}

    @r.delete("/auth/mapping/{provider}/{remote}", dependencies=[need("admin")])
    def auth_mapping_delete(provider: str, remote: str):
        if sec.delete_oauth_mapping(
            provider,
            remote,
            app.state.cfg.get("secrets_file"),
            base_dir=app.state.config_dir,
        ):
            return {"ok": True}
        raise HTTPException(404, "mapping not found")

    @r.post("/panel/logout")
    async def panel_logout(req: Request):
        req.session.clear()
        return {"ok": True}

    @r.get("/panel/status")
    def panel_status(req: Request):
        return {
            "admin": bool(req.session.get("admin")),
            "mod": bool(req.session.get("mod")),
            "tts": bool(req.session.get("tts")),
            "push": bool(req.session.get("push")),
            "pull": bool(req.session.get("pull")),
        }

    @r.post("/mod/mask", dependencies=[need("tts")])
    async def mod_mask(req: Request):
        j = await req.json()
        text = (j.get("text") or "").strip()
        if not text:
            return {"masked": "", "flags": {"urls": 0, "emojis": 0, "slurs": 0}}
        if not eng.mod_enabled():
            return {"masked": text, "flags": {"urls": 0, "emojis": 0, "slurs": 0}}
        masked, flags = eng.moder.filter(text, mode="mask")
        return {"masked": masked, "flags": flags}

    @r.get("/healthz")
    def healthz():
        return eng.health()

    @r.get("/voices", dependencies=[need("tts")])
    def voices():
        return eng.voices()

    @r.post("/reload", dependencies=[need("admin")])
    def reload_voices():
        return {"reloaded": eng.reload()}

    @r.get("/aliases", dependencies=[need("admin")])
    def get_aliases():
        return eng.get_aliases()

    @r.post("/aliases", dependencies=[need("admin")])
    async def set_alias(req: Request):
        j = await req.json()
        n = (j.get("name") or "").strip().lower()
        v = (j.get("voice") or "").strip()
        if not n or not v:
            raise HTTPException(400, "bad alias")
        eng.set_alias(n, v)
        return eng.get_aliases()

    @r.delete("/aliases/{name}", dependencies=[need("admin")])
    def del_alias(name):
        eng.del_alias((name or "").strip().lower())
        return eng.get_aliases()

    @r.post("/tts", dependencies=[need("tts")])
    async def tts_post(req: Request):
        j = await req.json()
        b, m, h = eng.tts(j)
        return Response(content=b, media_type=m, headers=h)

    @r.get("/tts", dependencies=[need("tts")])
    def tts_get(
        text: str,
        voice: str | None = None,
        format: str | None = None,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w: float | None = None,
        sentence_silence: float | None = None,
        normalize: bool | None = None,
        bitrate: str | None = None,
        speaker_id: int | None = None,
        preset: str | None = None,
    ):
        q = {k: v for k, v in locals().items()}
        b, m, h = eng.tts(q)
        return Response(content=b, media_type=m, headers=h)

    @r.get("/metrics")
    def metrics():
        return eng.metrics()

    @r.post("/push", dependencies=[need("push")])
    async def push(req: Request):
        try:
            j = await req.json()
        except Exception:
            raise HTTPException(400, "invalid json")
        t = (j.get("text") or "").strip()
        if not t:
            raise HTTPException(400, "text required")
        mx = eng.cfg.get("max_text_chars", 500)
        if len(t) > mx:
            t = t[:mx]
        j["text"] = t
        # assign id for deletion
        import uuid

        j["id"] = j.get("id") or uuid.uuid4().hex[:8]
        Q.append(j)
        return {"ok": True, "id": j["id"], "queued": len(Q)}

    @r.get("/pull", dependencies=[need("pull")])
    def pull():
        if not Q:
            return Response(status_code=204)
        it = Q.popleft()
        if not it.get("id"):
            it["id"] = uuid.uuid4().hex[:8]
        return it

    @r.get("/overlay")
    def overlay(req: Request, embed: str | None = None):
        from fastapi.responses import HTMLResponse

        p = os.path.join(os.getcwd(), "public", "overlay.html")
        if not os.path.isfile(p):
            raise HTTPException(404, "overlay not found")
        with open(p, "r", encoding="utf-8") as f:
            html = f.read()
        if not embed:
            return HTMLResponse(html)
        em = db.get_embed(embed)
        if not em:
            raise HTTPException(404, "embed not found")
        tk = db.get_token(em.get("jti"))
        if not tk:
            raise HTTPException(404, "token not found")
        if tk.get("revoked"):
            raise HTTPException(401, "revoked")
        if tk.get("expires", 0) < int(time.time()):
            raise HTTPException(401, "expired")
        # enforce origin if embed has an origin bound
        bound_origin = em.get("origin")
        if bound_origin:
            req_origin = req.headers.get("origin")
            if not req_origin or req_origin != bound_origin:
                raise HTTPException(403, "forbidden: origin mismatch")
        payload = {
            "iss": "tts",
            "iat": int(time.time()),
            "exp": tk.get("expires"),
            "jti": tk.get("jti"),
            "roles": tk.get("roles"),
        }
        token = jwt.encode(payload, app.state.jwt_secret, algorithm="HS256")
        inj = f"<script>window.OVERLAY_TOKEN = {json.dumps(token)};</script>"
        return HTMLResponse(inj + html)

    @r.post("/overlay/token", dependencies=[need("admin")])
    async def overlay_mint_token(req: Request):
        j = await req.json()
        ttl = int(j.get("ttl", 3600))
        roles = j.get("roles") or ["tts", "pull"]
        note = j.get("note") or ""
        jti = uuid.uuid4().hex
        now = int(time.time())
        exp = now + ttl
        payload = {"iss": "tts", "iat": now, "exp": exp, "jti": jti, "roles": roles}
        token = jwt.encode(payload, req.app.state.jwt_secret, algorithm="HS256")
        db.insert_token(jti, roles, exp, "admin", now, note)
        return {"token": token, "expires": int(exp), "jti": jti}

    @r.post("/overlay/embed", dependencies=[need("admin")])
    async def overlay_create_embed(req: Request):
        j = await req.json()
        ttl = int(j.get("ttl", 3600))
        roles = j.get("roles") or ["tts", "pull"]
        note = j.get("note") or ""
        jti = uuid.uuid4().hex
        now = int(time.time())
        exp = now + ttl
        payload = {"iss": "tts", "iat": now, "exp": exp, "jti": jti, "roles": roles}
        token = jwt.encode(payload, req.app.state.jwt_secret, algorithm="HS256")
        db.insert_token(jti, roles, exp, "admin", now, note)
        # generate a longer unpredictable embed id
        embed_id = secrets.token_urlsafe(18)
        origin = (j.get("origin") or "") or None
        db.insert_embed(embed_id, jti, now, note, origin)
        return {
            "embed_id": embed_id,
            "url": f"/api/overlay?embed={embed_id}",
            "expires": int(exp),
        }

    @r.delete("/overlay/embed/{embed_id}", dependencies=[need("admin")])
    def overlay_delete_embed(embed_id: str):
        em = db.get_embed(embed_id)
        if not em:
            raise HTTPException(404, "embed not found")
        db.delete_embed(embed_id)
        return {"ok": True}

    @r.get("/overlay/tokens", dependencies=[need("admin")])
    def overlay_list_tokens():
        toks = db.list_tokens()
        out = []
        for t in toks:
            out.append(
                {
                    "jti": t["jti"][:6] + "...",
                    "roles": t["roles"],
                    "expires": t["expires"],
                    "revoked": bool(t["revoked"]),
                    "note": t["note"],
                }
            )
        return {"tokens": out}

    @r.get("/overlay/embeds", dependencies=[need("admin")])
    def overlay_list_embeds():
        embeds = db.list_embeds()
        out = []
        for e in embeds:
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

    @r.delete("/overlay/token/{jti}", dependencies=[need("admin")])
    def overlay_revoke_token(jti: str):
        if db.revoke_token(jti):
            return {"ok": True}
        if db.revoke_token_prefix(jti):
            return {"ok": True}
        raise HTTPException(404, "token not found")

    @r.get("/mod/list", dependencies=[need("mod")])
    def mod_list():
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        return {"terms": eng.mod_list()}

    @r.post("/mod/add", dependencies=[need("mod")])
    async def mod_add(req: Request):
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        j = await req.json()
        term = (j.get("term") or "").strip()
        if not term:
            raise HTTPException(400, "term required")
        return eng.mod_add(term)

    @r.post("/mod/remove", dependencies=[need("mod")])
    async def mod_remove(req: Request):
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        j = await req.json()
        term = (j.get("term") or "").strip()
        if not term:
            raise HTTPException(400, "term required")
        return eng.mod_remove(term)

    @r.post("/mod/reload", dependencies=[need("mod")])
    def mod_reload():
        if not eng.mod_enabled():
            raise HTTPException(400, "moderation disabled")
        return eng.mod_reload()

    @r.get("/mod/test", dependencies=[need("mod")])
    def modtest(text: str):
        tx = text
        if eng.mod_enabled():
            tx2, flags = eng.moder.filter(tx)
        else:
            tx2, flags = tx, {"urls": 0, "emojis": 0, "slurs": 0}
        return {"in": tx, "out": tx2, "flags": flags}

    app.include_router(r)
    if os.path.isdir("public"):
        app.mount("/", StaticFiles(directory="public", html=True), name="ui")
    return app
