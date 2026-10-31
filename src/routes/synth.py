import anyio
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from resolve import BadRequest
from routes.models import SynthReq


def build(engine):
    r = APIRouter(prefix="/api")

    @r.post(
        "/tts",
        response_class=Response,
        responses={200: {"content": {"audio/mpeg": {}, "audio/wav": {}}}},
    )
    async def synth(body: SynthReq):
        j = body.model_dump(exclude_none=True)

        # a parts body concatenates (a plain text body takes the cached path)
        render = engine.batch if body.parts is not None else engine.tts

        # offload blocking synth so concurrent sentences pipeline
        try:
            b, m, h = await anyio.to_thread.run_sync(render, j)
        except BadRequest as e:
            raise HTTPException(400, str(e)) from None
        return Response(content=b, media_type=m, headers=h)

    @r.get("/sounds")
    def list_sounds():
        return {"index": engine.sounds.index(), "aliases": dict(engine.sounds.aliases)}

    @r.get("/health")
    def health():
        return engine.health()

    return r
