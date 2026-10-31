import pytest

import tts
import voice_fx


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(tts, "cfg", {"default_format": "mp3", "mp3_bitrate": "128k"})
    monkeypatch.setattr(tts, "aliases", {"ry": "en_US-ryan-high"})
    monkeypatch.setattr(tts, "presets", {"fast": {"length_scale": 0.85}})
    monkeypatch.setattr(
        tts,
        "profiles",
        {
            "narrator": {"voice": "vox", "length_scale": 1.1, "fx": "warm"},
            "thin": {"voice": "vox", "preset": "fast"},
        },
    )
    monkeypatch.setattr(tts, "vc", {"vox": {"id": "vox"}, "other": {"id": "other"}})
    monkeypatch.setattr(tts, "scanned", True)
    return tts


def test_request_beats_profile(engine):
    params, profile, _, _, _ = engine.params_for(
        {"profile": "narrator", "voice": "other", "length_scale": 0.5}
    )
    assert profile == "narrator"
    assert params.voice_id == "other"
    assert params.length_scale == 0.5


def test_profile_fills_unset_fields(engine):
    params, _, _, _, _ = engine.params_for({"profile": "narrator"})
    assert params.voice_id == "vox"
    assert params.length_scale == 1.1
    assert params.fx == "warm"


def test_profile_preset_beats_config_default(engine):
    params, _, preset, _, _ = engine.params_for({"profile": "thin"})
    assert preset == "fast"
    assert params.length_scale == 0.85


def test_explicit_null_falls_through_to_profile(engine):
    params, _, _, _, _ = engine.params_for(
        {"profile": "narrator", "length_scale": None}
    )
    assert params.length_scale == 1.1


def test_unknown_profile_is_ignored(engine):
    params, profile, _, _, fallback = engine.params_for({"profile": "nope"})
    assert profile == "nope"
    assert fallback is False
    assert params.fx is None


def test_profile_prefix_resolves_from_text(engine):
    clean, params, meta = engine._resolve_request({"text": "narrator: hello there"})
    assert clean == "hello there"
    assert meta.profile == "narrator"
    assert params.voice_id == "vox"


def test_alias_prefix_still_works(engine):
    clean, _, meta = engine._resolve_request({"text": "ry: hello"})
    assert clean == "hello"
    assert meta.profile == ""
    assert meta.requested_voice == "en_US-ryan-high"


def test_cache_key_separates_fx(engine):
    a = engine.SynthParams(voice_id="vox", fx="warm")
    b = engine.SynthParams(voice_id="vox", fx="cold")
    assert a.cache_key("hi", "") != b.cache_key("hi", "")


def test_named_chain_beats_per_voice():
    voice_fx.init(
        {
            "voice_fx": {
                "enabled": True,
                "chain": [{"type": "gain", "gain_db": 1}],
                "chains": {"warm": [{"type": "gain", "gain_db": 2}]},
                "per_voice": {"vox": {"chain": [{"type": "gain", "gain_db": 3}]}},
            }
        }
    )

    assert voice_fx.chains()["warm"][0]["gain_db"] == 2
    assert voice_fx._named_boards["warm"] is not voice_fx._voice_boards["vox"]

    voice_fx.set_chain("warm", [{"type": "gain", "gain_db": 9}])
    assert voice_fx.chains()["warm"][0]["gain_db"] == 9

    voice_fx.del_chain("warm")
    assert "warm" not in voice_fx.chains()


def test_unknown_fx_falls_back_to_per_voice():
    voice_fx.init(
        {
            "voice_fx": {
                "enabled": True,
                "chain": [],
                "per_voice": {"vox": {"chain": [{"type": "gain", "gain_db": 3}]}},
            }
        }
    )

    # a missing name must not silently drop the voice's own chain
    assert voice_fx.process_wav("/nonexistent.wav", voice_id="vox", fx="nope") == (
        "/nonexistent.wav"
    )
    assert len(voice_fx._voice_boards["vox"]) == 1
