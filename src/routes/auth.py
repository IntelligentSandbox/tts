from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

import secrets_util as sec
from roles import apply_role, session_role

OAUTH_TIMEOUT_SECONDS = 10.0


def _provider_cfg(req, provider):
    return sec.get_oauth_provider(
        provider,
        req.app.state.cfg.get("secrets_file") if req.app.state.cfg else None,
        base_dir=req.app.state.config_dir,
    )


def build():
    r = APIRouter(prefix="/api/auth")

    @r.post("/logout")
    async def logout(req: Request):
        req.session.clear()
        return {"ok": True}

    @r.get("/me")
    def me(req: Request, provider="twitch"):
        prov = provider or "twitch"
        sid = req.session.get(f"oauth_{prov}_id")
        slogin = req.session.get(f"oauth_{prov}_login")
        out = {"role": session_role(req), "provider": prov}

        if not sid and not slogin:
            return {**out, "ok": False}

        return {**out, "ok": True, "id": sid, "login": slogin}

    @r.get("/oauth/{provider}")
    def oauth_start(req: Request, provider):
        if provider != "twitch":
            raise HTTPException(400, "unsupported provider")

        cfg = _provider_cfg(req, provider)
        client_id = cfg.get("client_id")
        redirect = cfg.get("redirect_uri") or str(req.url_for("overlay"))

        if not client_id or not redirect:
            raise HTTPException(400, "oauth not configured")

        params = {
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "user:read:email",
        }
        url = "https://id.twitch.tv/oauth2/authorize?" + urlencode(params)

        return Response(status_code=302, headers={"Location": url})

    @r.get("/oauth/{provider}/callback")
    async def oauth_callback(req: Request, provider, code=None):
        if provider != "twitch":
            raise HTTPException(400, "unsupported provider")

        cfg = _provider_cfg(req, provider)
        client_id = cfg.get("client_id")
        client_secret = cfg.get("client_secret")
        redirect = cfg.get("redirect_uri")

        if not client_id or not client_secret or not redirect:
            raise HTTPException(400, "oauth not configured")

        if not code:
            raise HTTPException(400, "missing code")

        try:
            async with httpx.AsyncClient(timeout=OAUTH_TIMEOUT_SECONDS) as client:
                resp = await client.post(
                    "https://id.twitch.tv/oauth2/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": redirect,
                    },
                )
                resp.raise_for_status()
                tok = resp.json()
        except Exception as e:
            raise HTTPException(400, f"token exchange failed: {e}") from e

        access = tok.get("access_token")

        if not access:
            raise HTTPException(400, "no access token")

        try:
            async with httpx.AsyncClient(timeout=OAUTH_TIMEOUT_SECONDS) as client:
                hr = await client.get(
                    "https://api.twitch.tv/helix/users",
                    headers={
                        "Authorization": f"Bearer {access}",
                        "Client-Id": client_id,
                    },
                )
                hr.raise_for_status()
                u = hr.json()
        except Exception as e:
            raise HTTPException(400, f"user lookup failed: {e}") from e

        data = u.get("data") or []

        if not data:
            raise HTTPException(400, "no user data")

        user = data[0]
        twitch_id = user.get("id")
        login_name = user.get("login")

        # the panel reads the identity back through /api/auth/me
        req.session[f"oauth_{provider}_id"] = str(twitch_id)
        req.session[f"oauth_{provider}_login"] = (login_name or "").lower()

        mapped = sec.list_oauth_mappings(
            provider,
            req.app.state.cfg.get("secrets_file") if req.app.state.cfg else None,
            base_dir=req.app.state.config_dir,
        )
        role = mapped.get(str(twitch_id)) or mapped.get((login_name or "").lower())

        if role not in ("admin", "mod"):
            text = (
                f"<html><body>Login OK (user={login_name})."
                " Account not mapped to a role."
                f" Ask an admin to map your Twitch id {twitch_id}"
                " to a role.</body></html>"
            )
            return HTMLResponse(text)

        apply_role(req.session, role)
        return Response(status_code=302, headers={"Location": "/"})

    return r
