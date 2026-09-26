import re

import pytest
from fastapi.testclient import TestClient

import api
import db
import mod
import sfx
import tts as eng

SOUNDS = {"airhorn": {"file": "airhorn.wav"}}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "init", lambda *a, **k: None)
    monkeypatch.setattr(eng, "warmup", lambda: None)
    monkeypatch.setattr(eng, "auth_enabled", lambda: True)
    monkeypatch.setattr(eng, "dev_admin", lambda: True)
    monkeypatch.setattr(eng, "service_key_ok", lambda k: k == "service-key")
    monkeypatch.setattr(eng, "voices", lambda: [{"id": "test-voice"}])
    monkeypatch.setattr(eng, "health", lambda: {"ok": True})
    monkeypatch.setattr(eng, "metrics", lambda: {"count": 0})
    monkeypatch.setattr(eng, "reload", lambda: 1)
    monkeypatch.setattr(eng, "warmup_voice", lambda v: True)
    monkeypatch.setattr(eng, "cfg", {"max_text_chars": 20})
    monkeypatch.setattr(sfx, "init_sfx_aliases", lambda cfg: None)
    monkeypatch.setattr(sfx, "get_sfx_index", lambda cfg: SOUNDS)

    aliases = {}
    profiles = {}
    sfx_aliases = {}

    monkeypatch.setattr(eng, "get_aliases", lambda: aliases)
    monkeypatch.setattr(eng, "set_alias", lambda n, v: aliases.__setitem__(n, v))
    monkeypatch.setattr(eng, "del_alias", lambda n: aliases.pop(n, None))
    monkeypatch.setattr(eng, "get_profiles", lambda: profiles)
    monkeypatch.setattr(eng, "set_profile", lambda n, v: profiles.__setitem__(n, v))
    monkeypatch.setattr(eng, "del_profile", lambda n: profiles.pop(n, None))
    monkeypatch.setattr(sfx, "get_sfx_aliases", lambda: sfx_aliases)
    monkeypatch.setattr(
        sfx, "set_sfx_alias", lambda n, t, cfg: sfx_aliases.__setitem__(n, t)
    )
    monkeypatch.setattr(sfx, "del_sfx_alias", lambda n: sfx_aliases.pop(n, None))

    monkeypatch.setattr(
        eng, "tts", lambda d: (b"single", "audio/mpeg", {"X-Mod-Slurs": "0"})
    )
    monkeypatch.setattr(
        eng,
        "_resolve_request",
        lambda d: (d["text"], None, type("M", (), {"mod_flags": {"slurs": 0}})()),
    )
    monkeypatch.setattr(sfx, "parse_sfx_tags", lambda t: [{"text": t}])
    monkeypatch.setattr(eng, "render_parts", lambda sub, p, rm, n, mx: (list(sub), n))
    monkeypatch.setattr(
        eng, "_concat_wavs", lambda s, fmt, bitrate: (b"batch", "audio/mpeg")
    )

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("# keep me\naliases: {}\nprofiles: {}\nsfx_aliases: {}\n")

    cfg = {
        "db_file": str(tmp_path / "test.db"),
        "sounds_dir": str(tmp_path / "missing"),
        "session": {"secret": "test-secret"},
        "secrets_file": str(tmp_path / "secrets.yaml"),
        "jwt_secret": "test-jwt-secret-that-is-long-enough-for-hs256",
    }

    with TestClient(api.make_app(cfg, cfg_path=str(cfg_path))) as c:
        c.cfg_path = cfg_path
        yield c


def test_frozen_contract_routes(client):
    assert client.get("/api/voices").json() == [{"id": "test-voice"}]
    assert client.get("/api/sounds").json()["index"] == SOUNDS
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/metrics").json() == {"count": 0}

    r = client.post("/api/warmup", json={"voice": "test-voice"})
    assert r.json() == {"warmed": True, "voice": "test-voice"}
    assert client.post("/api/warmup", json={}).status_code == 400


def test_tts_takes_text_or_parts(client):
    r = client.post("/api/tts", json={"text": "hello"})
    assert r.content == b"single"

    r = client.post("/api/tts", json={"parts": [{"text": "a"}, {"text": "b"}]})
    assert r.content == b"batch"
    assert r.headers["X-Mod-Slurs"] == "0"

    assert client.post("/api/tts", json={"parts": []}).status_code == 400


def test_catalog_writes_reach_the_config_file(client):
    client.put("/api/catalog/voice_alias/Bob", json={"voice": "test-voice"})
    client.put("/api/catalog/profile/narrator", json={"voice": "test-voice"})
    client.put("/api/catalog/sfx_alias/horn", json={"target_id": "airhorn"})

    cat = client.get("/api/catalog").json()
    assert cat["voice_aliases"] == {"bob": "test-voice"}
    assert cat["profiles"] == {"narrator": {"voice": "test-voice"}}
    assert cat["sfx_aliases"] == {"horn": "airhorn"}
    assert cat["sounds"] == SOUNDS

    written = client.cfg_path.read_text()
    assert "# keep me" in written
    assert "bob: test-voice" in written
    assert "narrator:" in written
    assert "horn: airhorn" in written

    assert client.put("/api/catalog/nope/x", json={}).status_code == 404
    assert client.put("/api/catalog/voice_alias/x", json={}).status_code == 400


def test_a_config_entry_cannot_be_deleted(client):
    # this one is hand written in the config, the panel never made it
    client.cfg_path.write_text(
        "# keep me\naliases:\n  handmade: test-voice\nprofiles: {}\nsfx_aliases: {}\n"
    )
    eng.set_alias("handmade", "test-voice")

    r = client.delete("/api/catalog/voice_alias/handmade")
    assert r.status_code == 403
    assert "config" in r.json()["detail"]
    assert "handmade" in client.cfg_path.read_text()
    assert client.get("/api/catalog").json()["removable"]["voice_alias"] == []


def test_a_panel_entry_can_be_deleted(client):
    client.put("/api/catalog/voice_alias/Bob", json={"voice": "test-voice"})
    assert client.get("/api/catalog").json()["removable"]["voice_alias"] == ["bob"]

    assert client.delete("/api/catalog/voice_alias/Bob").status_code == 200
    assert client.get("/api/catalog").json()["voice_aliases"] == {}
    assert "bob" not in client.cfg_path.read_text()
    assert "# keep me" in client.cfg_path.read_text()

    # gone from the panel list, so a second delete is refused
    assert client.delete("/api/catalog/voice_alias/Bob").status_code == 403


def test_queue_lifecycle(client):
    r = client.post("/api/queue", json={"text": "first"})
    first = r.json()["id"]
    second = client.post("/api/queue", json={"text": "second"}).json()["id"]

    items = client.get("/api/queue").json()["items"]
    assert [i["id"] for i in items] == [first, second]

    assert client.delete(f"/api/queue/{first}").json() == {"deleted": 1}
    assert client.get("/api/queue/next").json()["id"] == second
    assert client.get("/api/queue/next").status_code == 204

    assert client.post("/api/queue", json={}).status_code == 400


def test_queue_push_truncates_text(client):
    client.post("/api/queue", json={"text": "x" * 50})
    assert client.get("/api/queue").json()["items"][0]["text"] == "x" * 20


def test_deleting_an_embed_revokes_its_token(client):
    embed_id = client.post("/api/admin/embeds", json={"ttl": 3600}).json()["embed_id"]

    listed = client.get("/api/admin/embeds").json()["embeds"]
    assert [e["embed_id"] for e in listed] == [embed_id]
    assert listed[0]["revoked"] is False

    jti = db.list_embeds()[0]["jti"]
    assert client.delete(f"/api/admin/embeds/{embed_id}").json() == {"ok": True}
    assert db.get_token(jti)["revoked"]

    assert client.delete(f"/api/admin/embeds/{embed_id}").status_code == 404
    assert client.get(f"/api/overlay?embed={embed_id}").status_code == 404


def test_an_embed_token_synths_but_never_admins(client, monkeypatch):
    embed_id = client.post("/api/admin/embeds", json={"ttl": 3600}).json()["embed_id"]
    page = client.get(f"/api/overlay?embed={embed_id}")
    token = re.search(r'OVERLAY_TOKEN = "([^"]+)"', page.text).group(1)

    monkeypatch.setattr(eng, "dev_admin", lambda: False)
    hdr = {"X-API-Key": token}

    assert client.post("/api/tts", json={"text": "hi"}, headers=hdr).status_code == 200
    assert client.get("/api/queue/next", headers=hdr).status_code == 204
    assert client.get("/api/admin/embeds", headers=hdr).status_code == 401

    monkeypatch.setattr(eng, "dev_admin", lambda: True)
    client.delete(f"/api/admin/embeds/{embed_id}")
    monkeypatch.setattr(eng, "dev_admin", lambda: False)

    assert client.post("/api/tts", json={"text": "hi"}, headers=hdr).status_code == 401


def test_mod_state_and_update(client, monkeypatch):
    terms = ["nasty"]
    state = {"mode": "mask", "modes": ["mask", "drop"], "censoring": True}

    monkeypatch.setattr(mod, "mod_enabled", lambda: True)
    monkeypatch.setattr(mod, "mod_list", lambda: list(terms))
    monkeypatch.setattr(mod, "mod_mode", lambda: dict(state))
    monkeypatch.setattr(mod, "mod_add", lambda t: terms.append(t))
    monkeypatch.setattr(mod, "mod_remove", lambda t: terms.remove(t))
    monkeypatch.setattr(mod, "mod_set_mode", lambda m: state.__setitem__("mode", m))
    monkeypatch.setattr(
        mod, "mod_set_censoring", lambda on: state.__setitem__("censoring", on)
    )

    assert client.get("/api/admin/mod").json()["terms"] == ["nasty"]

    r = client.post(
        "/api/admin/mod",
        json={"mode": "drop", "censoring": False, "add": ["rude"], "remove": ["nasty"]},
    )
    body = r.json()
    assert body["terms"] == ["rude"]
    assert body["mode"] == "drop"
    assert body["censoring"] is False


def test_mod_routes_report_disabled(client, monkeypatch):
    monkeypatch.setattr(mod, "mod_enabled", lambda: False)
    assert client.get("/api/admin/mod").status_code == 400


def test_a_service_key_never_grants_admin(client, monkeypatch):
    monkeypatch.setattr(eng, "dev_admin", lambda: False)
    hdr = {"X-API-Key": "service-key"}

    assert client.get("/api/voices", headers=hdr).status_code == 200
    assert (
        client.post("/api/queue", json={"text": "hi"}, headers=hdr).status_code == 200
    )
    assert client.get("/api/queue/next", headers=hdr).status_code == 200

    assert client.get("/api/catalog", headers=hdr).status_code == 200
    assert client.get("/api/admin/embeds", headers=hdr).status_code == 401
    assert client.get("/api/admin/mod", headers=hdr).status_code == 401
    assert client.get("/api/queue", headers=hdr).status_code == 401
    assert (
        client.put(
            "/api/catalog/voice_alias/x", json={"voice": "v"}, headers=hdr
        ).status_code
        == 401
    )


def test_a_bad_key_is_rejected(client, monkeypatch):
    monkeypatch.setattr(eng, "dev_admin", lambda: False)
    assert client.get("/api/voices", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/api/voices").status_code == 401


def test_there_is_no_key_login_route(client):
    paths = {r.path for r in client.app.routes if hasattr(r, "methods")}
    assert "/api/auth/login" not in paths


def test_dev_admin_grants_the_panel(client):
    assert client.get("/api/auth/me").json()["role"] == "admin"
