from echo_common import logger
from fastapi import APIRouter, Depends, HTTPException

from routes.models import ModReq


def build(moderator):
    async def _mod_on():
        if not moderator:
            raise HTTPException(400, "moderation disabled")

    r = APIRouter(prefix="/api/mod", dependencies=[Depends(_mod_on)])

    @r.get("")
    def mod_state():
        return moderator.state()

    @r.post("")
    def mod_update(body: ModReq):
        if body.reload:
            moderator.censor.reload()

        if body.censoring is not None:
            moderator.censor_slurs = body.censoring

        if body.mode:
            moderator.censor_mode = body.mode

        for term in filter(None, body.add):
            moderator.censor.add(term)
            logger.info("[mod] added a term")

        for term in filter(None, body.remove):
            moderator.censor.remove(term)
            logger.info("[mod] removed a term")

        state = moderator.state()

        if body.mode or body.censoring is not None:
            logger.info(f"[mod] mode {state['mode']} censoring {state['censoring']}")

        return state

    return r
