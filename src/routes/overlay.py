import json
import os
import time

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import db


def build():
    r = APIRouter(prefix="/api")

    @r.get("/overlay")
    def overlay(req: Request, embed=None):
        p = os.path.join(req.app.state.public_dir, "overlay.html")

        if not os.path.isfile(p):
            raise HTTPException(404, "overlay not found")

        with open(p, encoding="utf-8") as f:
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

        exp = tk.get("expires") or 0

        if exp and exp < int(time.time()):
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
            "jti": tk.get("jti"),
            "roles": tk.get("roles"),
        }

        if exp:
            payload["exp"] = exp

        token = jwt.encode(payload, req.app.state.jwt_secret, algorithm="HS256")
        inj = f"<script>window.OVERLAY_TOKEN = {json.dumps(token)};</script>"
        return HTMLResponse(inj + html)

    return r
