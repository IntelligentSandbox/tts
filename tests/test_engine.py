import shutil
import subprocess

import pytest

import audio
import sfx
import voices
from engine import Engine
from resolve import SynthParams


@pytest.fixture
def wav(tmp_path):
    ff = shutil.which("ffmpeg")

    if not ff:
        pytest.skip("needs ffmpeg")

    p = tmp_path / "tone.wav"
    subprocess.run(
        [ff, "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.2", str(p)],
        check=True,
    )
    return ff, str(p)


@pytest.fixture
def fake_voice(wav, monkeypatch):
    """A piper voice that copies a tone so the real chain runs without a model."""
    _, p = wav
    seen = []
    vox = {"id": "vox", "backend": "piper", "sample_rate": 22050}
    monkeypatch.setattr(voices.Voices, "_scanned", lambda self: {"vox": vox})
    monkeypatch.setattr(
        voices.Piper,
        "synth",
        lambda self, info, txt, params, out: seen.append(txt) or shutil.copy(p, out),
    )
    return seen


def test_a_failed_mp3_encode_falls_back_to_wav(wav, monkeypatch):
    ff, p = wav
    monkeypatch.setattr(audio.Ffmpeg, "mp3", lambda self, path, br: b"")

    b, m = audio.Ffmpeg(ff).concat([p], "mp3", "128k")
    assert m == "audio/wav"
    assert b.startswith(b"RIFF")


def test_normalize_runs_loudnorm(monkeypatch):
    calls = []
    failed = subprocess.CompletedProcess([], 1)
    monkeypatch.setattr(
        audio.subprocess, "run", lambda cmd, **k: calls.append(cmd) or failed
    )

    assert audio.Ffmpeg("ffmpeg").normalize("x.wav") == "x.wav"
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in calls[0]


def test_a_rescan_picks_up_a_new_sound(tmp_path):
    sounds = sfx.Sounds(str(tmp_path))
    (tmp_path / "boom.mp3").write_bytes(b"")
    assert list(sounds.index()) == ["boom"]

    (tmp_path / "pop.mp3").write_bytes(b"")
    sounds.rescan()
    assert sorted(sounds.index()) == ["boom", "pop"]


def test_health_reports_capacity_while_the_cache_is_empty(monkeypatch):
    monkeypatch.setattr(voices.Voices, "_scanned", lambda self: {})
    health = Engine({}).health()
    assert health["cache"] == {"items": 0, "capacity": 64, "ttl_sec": 300}


def test_a_voice_alias_matches_any_case(monkeypatch):
    vox = {"en_US-ryan-high": {"id": "en_US-ryan-high"}}
    monkeypatch.setattr(voices.Voices, "_scanned", lambda self: vox)
    res = Engine({"aliases": {"echo": "en_US-ryan-high"}}).resolver

    assert res.voice_id("Echo") == "en_US-ryan-high"
    assert res.voice_id("en_US-ryan-high") == "en_US-ryan-high"


def test_sfx_tags_split_into_parts():
    assert sfx.parse_sfx_tags("hi {Boom} there {pop}") == [
        {"text": "hi"},
        {"sfx": "Boom"},
        {"text": "there"},
        {"sfx": "pop"},
    ]
    assert sfx.parse_sfx_tags("no tags") == [{"text": "no tags"}]
    assert sfx.parse_sfx_tags("{a}{b}") == [{"sfx": "a"}, {"sfx": "b"}]


def test_a_batch_renders_through_the_real_chain(wav, fake_voice, tmp_path):
    _, p = wav
    shutil.copy(p, tmp_path / "boom.wav")
    eng = Engine({"default_format": "wav", "sounds_dir": str(tmp_path)})

    rm = []
    parts = [{"text": "a"}, {"sfx": "boom"}, {"text": "b"}]
    segs, n = eng.render_parts(parts, SynthParams("vox", fmt="wav"), rm)
    b, m = eng.ffmpeg.concat(segs, "wav", "128k")

    assert fake_voice == ["a", "b"]
    assert n == 1
    assert m == "audio/wav"
    assert b.startswith(b"RIFF")

    for f in rm:
        audio.rm(f)


def test_a_batch_moderates_each_part_and_skips_empty_ones(fake_voice):
    eng = Engine(
        {
            "default_format": "wav",
            "moderation": {"enabled": True, "censor_slurs": False},
        }
    )

    parts = [
        {"text": "see https://a.com"},
        {"text": "and https://b.com"},
        {"text": " "},
    ]
    b, m, _ = eng.batch({"parts": parts})

    assert fake_voice == ["see [link]", "and [link]"]
    assert m == "audio/wav"
    assert b.startswith(b"RIFF")


def test_a_repeat_request_is_a_cache_hit(fake_voice):
    eng = Engine({"default_format": "wav"})

    first, _, _ = eng.tts({"text": "hello", "voice": "vox"})
    second, _, _ = eng.tts({"text": "hello", "voice": "vox"})

    assert first == second
    assert fake_voice == ["hello"]


def test_sounds_past_the_cap_are_skipped(wav, tmp_path):
    _, p = wav
    shutil.copy(p, tmp_path / "boom.wav")
    eng = Engine({"sounds_dir": str(tmp_path), "max_sfx_per_request": 1})

    rm = []
    segs, n = eng.render_parts([{"sfx": "boom"}, {"sfx": "boom"}], None, rm)

    assert n == 1
    assert len(segs) == 1

    for f in rm:
        audio.rm(f)
