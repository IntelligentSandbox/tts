import uuid

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import sfx
import tts as eng
from roles import need

MAX_SOUNDS = 10

# what a batch part may set for itself, falling back to the top level body
PART_KEYS = (
    "voice",
    "profile",
    "preset",
    "fx",
    "length_scale",
    "noise_scale",
    "noise_w",
    "speaker_id",
    "normalize",
)


def _render_batch(j):
    parts = j.get("parts") or []
    fmt = (j.get("format") or "mp3").lower()

    # top level settings are the fallback for a part that names none
    defaults = {k: j.get(k) for k in PART_KEYS}

    segs, rm, sfx_count = [], [], 0
    slurs = 0

    try:
        for p in parts:
            if "sfx" in p:
                sub, params = [{"sfx": p.get("sfx")}], None
            else:
                d = dict(defaults)
                d.update({k: p[k] for k in PART_KEYS if p.get(k) is not None})
                d["text"] = p.get("text") or ""

                try:
                    txt, params, meta = eng._resolve_request(d)
                except RuntimeError:
                    continue

                slurs += meta.mod_flags["slurs"]
                sub = sfx.parse_sfx_tags(txt)

            got, sfx_count = eng.render_parts(sub, params, rm, sfx_count, MAX_SOUNDS)
            segs += got

        if not segs:
            raise HTTPException(400, "empty parts")

        b, m = eng._concat_wavs(segs, fmt=fmt, bitrate=j.get("bitrate"))
        rid = uuid.uuid4().hex[:8]
        h = {
            "Content-Disposition": (
                f'inline; filename="batch-{rid}.'
                f'{"mp3" if m == "audio/mpeg" else "wav"}"'
            ),
            "Cache-Control": "no-store",
            "X-Mod-Slurs": str(slurs),
        }
        return Response(content=b, media_type=m, headers=h)
    finally:
        for pth in rm:
            eng._rm(pth)


def build(cfg):
    r = APIRouter(prefix="/api")

    @r.post("/tts", dependencies=[need("service")])
    async def synth(req: Request):
        j = await req.json()

        # a parts body concatenates (a plain text body takes the cached path)
        if j.get("parts") is not None:
            return await anyio.to_thread.run_sync(_render_batch, j)

        # offload blocking synth so concurrent sentences pipeline
        b, m, h = await anyio.to_thread.run_sync(eng.tts, j)
        return Response(content=b, media_type=m, headers=h)

    @r.get("/voices", dependencies=[need("service")])
    def voices():
        return eng.voices()

    @r.post("/warmup", dependencies=[need("service")])
    async def warmup_voice(req: Request):
        j = await req.json()
        voice_id = (j.get("voice") or "").strip()

        if not voice_id:
            raise HTTPException(400, "voice required")

        ok = eng.warmup_voice(voice_id)
        return {"warmed": ok, "voice": voice_id}

    @r.get("/sounds", dependencies=[need("service")])
    def list_sounds():
        return {"index": sfx.get_sfx_index(cfg), "aliases": sfx.get_sfx_aliases()}

    @r.get("/health")
    def health():
        return eng.health()

    @r.get("/metrics")
    def metrics():
        return eng.metrics()

    return r
