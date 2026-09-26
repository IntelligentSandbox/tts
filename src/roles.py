import jwt
from echo_common import logger
from fastapi import Depends, HTTPException, Request

import db
import tts as eng


def apply_role(session, role):
    session["role"] = role


def session_role(req):
    if eng.dev_admin():
        return "admin"

    role = req.session.get("role")
    return role if role in ("admin", "mod") else None


def who(req):
    """A name for the audit log (the session is the only way to admin)."""
    login = req.session.get("oauth_twitch_login")
    sid = req.session.get("oauth_twitch_id")

    if login or sid:
        return f"{login or '?'}/{sid or '?'}"

    return "dev-admin" if eng.dev_admin() else "unknown"


def audit(req, what):
    logger.info(f"[audit] {who(req)} {what}")


def _bearer(req):
    k = req.headers.get("x-api-key") or req.headers.get("authorization") or ""

    if k.lower().startswith("bearer "):
        k = k[7:]

    return k


def _embed_token_ok(k, secret):
    try:
        pl = jwt.decode(k, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired") from None
    except Exception:
        return False

    tk = db.get_token(pl.get("jti"))

    if tk and tk.get("revoked"):
        raise HTTPException(401, "revoked")

    return True


def need(level):
    """service is the key or an embed token (mod and admin are oauth only)."""

    async def dep(req: Request):
        if not eng.auth_enabled():
            return

        role = session_role(req)

        if role == "admin" or (role == "mod" and level != "admin"):
            return

        if level == "service":
            k = _bearer(req)

            if k and (
                eng.service_key_ok(k) or _embed_token_ok(k, req.app.state.jwt_secret)
            ):
                return

        raise HTTPException(401, "unauthorized")

    return Depends(dep)
