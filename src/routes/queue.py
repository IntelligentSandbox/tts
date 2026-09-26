import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import tts as eng
from roles import need


def build(q):
    r = APIRouter(prefix="/api/queue")

    @r.post("", dependencies=[need("service")])
    async def push(req: Request):
        try:
            j = await req.json()
        except Exception:
            raise HTTPException(400, "invalid json") from None

        t = (j.get("text") or "").strip()

        if not t:
            raise HTTPException(400, "text required")

        mx = eng.cfg.get("max_text_chars", 500)

        if len(t) > mx:
            t = t[:mx]

        j["text"] = t

        # assign id for deletion
        j["id"] = j.get("id") or uuid.uuid4().hex[:8]
        q.append(j)
        return {"ok": True, "id": j["id"], "queued": len(q)}

    @r.get("", dependencies=[need("mod")])
    def listing():
        items = []

        for it in q:
            if not it.get("id"):
                it["id"] = uuid.uuid4().hex[:8]

            items.append(dict(it))

        return {"items": items, "queued": len(items)}

    @r.get("/next", dependencies=[need("service")])
    def next_item():
        if not q:
            return Response(status_code=204)

        it = q.popleft()

        if not it.get("id"):
            it["id"] = uuid.uuid4().hex[:8]

        return it

    @r.delete("/{qid}", dependencies=[need("mod")])
    def delete_item(qid):
        if not qid:
            raise HTTPException(400, "bad id")

        n = 0
        tmp = []

        while q:
            it = q.popleft()

            if str(it.get("id")) == str(qid):
                n += 1
            else:
                tmp.append(it)

        for it in tmp:
            q.append(it)

        return {"deleted": n}

    return r
