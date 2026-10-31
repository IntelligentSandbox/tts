import pytest

from resolve import BadRequest, Resolver, SynthParams
from voice_fx import VoiceFx


class FakeVoices:
    ids = ("vox", "other", "en_US-ryan-high")

    def get(self, vid):
        return {"id": vid} if vid in self.ids else None

    def default_id(self):
        return self.ids[0]

    def all(self):
        return [{"id": vid} for vid in self.ids]


class NoVoices(FakeVoices):
    ids = ()


@pytest.fixture
def res():
    cfg = {
        "default_format": "mp3",
        "mp3_bitrate": "128k",
        "aliases": {"ry": "en_US-ryan-high"},
        "presets": {"fast": {"length_scale": 0.85}},
        "profiles": {
            "narrator": {"voice": "vox", "length_scale": 1.1, "fx": "warm"},
            "thin": {"voice": "vox", "preset": "fast"},
        },
    }
    return Resolver(cfg, FakeVoices(), VoiceFx())


def test_request_beats_profile(res):
    params = res.params_for(
        {"profile": "narrator", "voice": "other", "length_scale": 0.5}
    )
    assert params.voice_id == "other"
    assert params.length_scale == 0.5
    assert params.fx == "warm"


def test_profile_fills_unset_fields(res):
    params = res.params_for({"profile": "narrator"})
    assert params.voice_id == "vox"
    assert params.length_scale == 1.1
    assert params.fx == "warm"


def test_profile_preset_beats_config_default(res):
    params = res.params_for({"profile": "thin"})
    assert params.length_scale == 0.85


def test_explicit_null_falls_through_to_profile(res):
    params = res.params_for({"profile": "narrator", "length_scale": None})
    assert params.length_scale == 1.1


def test_unknown_profile_is_ignored(res):
    params = res.params_for({"profile": "nope"})
    assert params.voice_id == "vox"
    assert params.fx is None


def test_profile_prefix_resolves_from_text(res):
    clean, params = res.resolve({"text": "narrator: hello there"})
    assert clean == "hello there"
    assert params.voice_id == "vox"
    assert params.fx == "warm"


def test_alias_prefix_still_works(res):
    clean, params = res.resolve({"text": "ry: hello"})
    assert clean == "hello"
    assert params.voice_id == "en_US-ryan-high"
    assert params.fx is None


def test_cache_key_separates_fx():
    a = SynthParams(voice_id="vox", fx="warm")
    b = SynthParams(voice_id="vox", fx="cold")
    cache = {(a, "hi"): 1}
    assert (b, "hi") not in cache


def test_named_chain_beats_per_voice():
    fx = VoiceFx(
        {
            "enabled": True,
            "chain": [{"type": "gain", "gain_db": 1}],
            "chains": {"warm": [{"type": "gain", "gain_db": 2}]},
            "per_voice": {"vox": {"chain": [{"type": "gain", "gain_db": 3}]}},
        }
    )

    assert fx.named_boards["warm"][0].gain_db == 2
    assert fx.named_boards["warm"] is not fx.voice_boards["vox"]

    fx.set_chain("warm", [{"type": "gain", "gain_db": 9}])
    assert fx.named_boards["warm"][0].gain_db == 9

    fx.del_chain("warm")
    assert "warm" not in fx.named_boards


def test_unknown_fx_falls_back_to_per_voice():
    fx = VoiceFx(
        {
            "enabled": True,
            "chain": [],
            "per_voice": {"vox": {"chain": [{"type": "gain", "gain_db": 3}]}},
        }
    )

    # a missing name must not silently drop the voice's own chain
    assert fx.process_wav("/nonexistent.wav", voice_id="vox", fx="nope") == (
        "/nonexistent.wav"
    )
    assert len(fx.voice_boards["vox"]) == 1


def test_a_leading_preset_tag_picks_the_preset(res):
    clean, params = res.resolve({"text": "[fast] hello"})
    assert clean == "hello"
    assert params.length_scale == 0.85


def test_a_mid_text_preset_tag_picks_the_preset(res):
    clean, params = res.resolve({"text": "hello [fast] there"})
    assert clean == "hello there"
    assert params.length_scale == 0.85


def test_an_unknown_tag_is_just_text(res):
    clean, params = res.resolve({"text": "hello [nope] there"})
    assert clean == "hello [nope] there"
    assert params.length_scale is None


def test_an_inline_chain_registers_under_the_profile_name(res):
    res.set_profile("robot", {"voice": "vox", "chain": [{"type": "gain"}]})
    assert res.profiles["robot"] == {"voice": "vox", "fx": "robot"}
    assert "robot" in res.fx.named_boards

    res.del_profile("robot")
    assert "robot" not in res.profiles
    assert "robot" not in res.fx.named_boards


def test_no_voices_says_so():
    res = Resolver({}, NoVoices(), VoiceFx())

    with pytest.raises(BadRequest, match="No voices found"):
        res.resolve({"text": "hi", "voice": "am_echo"})
