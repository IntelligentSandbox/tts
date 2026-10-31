import pytest
from fastapi.testclient import TestClient

import api
import engine
import voices
from resolve import BadRequest

VOICES = [{"id": "test-voice", "backend": "piper"}]
SOUNDS = {"airhorn": {"file": "airhorn.wav"}}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(voices.Voices, "all", lambda self: VOICES)
    monkeypatch.setattr(voices.Voices, "get", lambda self, vid: VOICES[0])
    monkeypatch.setattr(voices.Voices, "warmup", lambda self: None)
    monkeypatch.setattr(voices.Voices, "reload", lambda self: None)
    monkeypatch.setattr(
        engine.Engine, "tts", lambda self, d: (b"single", "audio/mpeg", {})
    )
    monkeypatch.setattr(
        engine.Engine, "batch", lambda self, j: (b"batch", "audio/mpeg", {})
    )

    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    (sounds_dir / "airhorn.wav").write_bytes(b"honk")

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("# keep me\naliases: {}\nprofiles: {}\nsfx_aliases: {}\n")

    def make(**extra):
        cfg = {
            "db_file": str(tmp_path / "test.db"),
            "sounds_dir": str(sounds_dir),
            "max_text_chars": 20,
            **extra,
        }
        c = TestClient(api.make_app(cfg, cfg_path=str(cfg_path)))
        c.cfg_path = cfg_path
        return c

    return make


@pytest.fixture
def client(make_client):
    with make_client() as c:
        yield c


def test_frozen_contract_routes(client):
    assert client.get("/api/sounds").json()["index"] == SOUNDS
    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert health["voices"] == 1


def test_tts_takes_text_or_parts(client):
    r = client.post("/api/tts", json={"text": "hello"})
    assert r.content == b"single"

    r = client.post("/api/tts", json={"parts": [{"text": "a"}, {"text": "b"}]})
    assert r.content == b"batch"

    assert client.post("/api/tts", json={"parts": []}).status_code == 422


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

    assert client.put("/api/catalog/nope/x", json={}).status_code == 405
    assert client.put("/api/catalog/voice_alias/x", json={}).status_code == 422


def test_a_config_entry_cannot_be_deleted(make_client):
    # this one is hand written in the config, the panel never made it
    with make_client(aliases={"handmade": "test-voice"}) as client:
        client.cfg_path.write_text(
            "# keep me\naliases:\n  handmade: test-voice\n"
            "profiles: {}\nsfx_aliases: {}\n"
        )

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


def test_mod_state_and_update(make_client, tmp_path):
    bl = tmp_path / "bl.txt"
    bl.write_text("nasty\n", encoding="utf-8")
    moderation = {"enabled": True, "blocklist_path": str(bl), "censor_mode": "mask"}

    with make_client(moderation=moderation) as client:
        assert client.get("/api/mod").json()["terms"] == ["nasty"]

        r = client.post(
            "/api/mod",
            json={
                "mode": "drop",
                "censoring": False,
                "add": ["rude"],
                "remove": ["nasty"],
            },
        )
        body = r.json()
        assert body["terms"] == ["rude"]
        assert body["mode"] == "drop"
        assert body["censoring"] is False
        assert bl.read_text(encoding="utf-8") == "rude\n"


def test_mod_routes_report_disabled(client):
    assert client.get("/api/mod").status_code == 400


def test_an_sfx_alias_name_is_stored_lowercase(client):
    client.put("/api/catalog/sfx_alias/Horn", json={"target_id": "airhorn"})
    assert client.get("/api/catalog").json()["removable"]["sfx_alias"] == ["horn"]
    assert "horn: airhorn" in client.cfg_path.read_text()

    assert client.delete("/api/catalog/sfx_alias/horn").status_code == 200
    assert client.get("/api/catalog").json()["sfx_aliases"] == {}


# Echo's avatar and llm call these exactly like this so they cannot change
def test_echo_contract_tts_ignores_an_old_service_key(client, monkeypatch):
    seen = []
    monkeypatch.setattr(
        engine.Engine,
        "tts",
        lambda self, d: seen.append(d) or (b"single", "audio/wav", {}),
    )
    key = {"X-API-Key": "leftover-key"}
    body = {"text": "hi", "profile": "echo", "format": "wav"}

    r = client.post("/api/tts", json=body, headers=key)
    assert r.status_code == 200
    assert r.content == b"single"
    assert seen == [body]


def test_echo_contract_sounds_resolve_to_a_public_file(make_client):
    with make_client(sfx_aliases={"horn": "airhorn"}) as client:
        data = client.get("/api/sounds", headers={"X-API-Key": "service-key"}).json()
        assert data == {"index": SOUNDS, "aliases": {"horn": "airhorn"}}

        sound_file = data["index"][data["aliases"]["horn"]]["file"]
        assert client.get(f"/sounds/{sound_file}").content == b"honk"


def test_reload_returns_the_catalog(client):
    client.put("/api/catalog/sfx_alias/horn", json={"target_id": "airhorn"})
    r = client.post("/api/catalog/reload")
    assert r.json()["sfx_aliases"] == {"horn": "airhorn"}


def test_the_panel_is_a_static_page(client):
    assert "TTS Panel" in client.get("/").text


def test_an_empty_batch_is_a_bad_request(client, monkeypatch):
    def empty(self, j):
        raise BadRequest("empty parts")

    monkeypatch.setattr(engine.Engine, "batch", empty)
    r = client.post("/api/tts", json={"parts": [{"text": " "}]})
    assert r.status_code == 400
    assert r.json()["detail"] == "empty parts"
