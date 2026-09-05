import contextlib
import glob
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

from cachetools import TTLCache
from echo_common import logger, resolve_path

import mod
import secrets_util as sec
import sfx
import voice_fx

cfg = {}
vc = {}
scanned = False
sem = None
aliases = {}
presets = {}
cache = None
_auth = {"enabled": False, "keys": {}}
_ffmpeg = None
_speed_re = re.compile(r"\[(fast|slow)\]", re.IGNORECASE)

# trim leading and trailing silence so segments sit flush
_SILENCE_TRIM = (
    "silenceremove=start_periods=1:start_threshold=-50dB,"
    "areverse,"
    "silenceremove=start_periods=1:start_threshold=-50dB,"
    "areverse"
)


DEFAULT_VOICES = os.path.join(os.path.dirname(__file__), "..", "voices")
DEFAULT_SOUNDS = os.path.join(os.path.dirname(__file__), "..", "sounds")

KOKORO_VOICES = [
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
]
KOKORO_SR = 24000
WAV_HEADER_BYTES = 44

_kokoro_pipelines = {}
_kokoro_lock = threading.Lock()

_piper_voices = {}
_piper_lock = threading.Lock()


def _piper_resident():
    # keep the onnx model loaded in-process instead of spawning piper per call
    return bool(cfg.get("piper_resident", True))


def _piper_voice(info):
    vid = info["id"]

    with _piper_lock:
        v = _piper_voices.get(vid)

        if v is None:
            from piper import PiperVoice

            v = PiperVoice.load(info["model_path"], info["config_path"])
            _piper_voices[vid] = v

        return v


def _piper_synth(info, txt, out_path, ls, ns, nw, spk):
    from piper import SynthesisConfig

    voice = _piper_voice(info)
    sc = SynthesisConfig(
        speaker_id=spk,
        length_scale=ls,
        noise_scale=ns,
        noise_w_scale=nw,
    )

    with wave.open(out_path, "wb") as wf:
        voice.synthesize_wav(txt, wf, sc)


def _kokoro_pipeline(voice_id):
    lang = "b" if voice_id.startswith("b") else "a"

    with _kokoro_lock:
        p = _kokoro_pipelines.get(lang)

        if p is None:
            from kokoro import KPipeline

            p = KPipeline(lang_code=lang)
            _kokoro_pipelines[lang] = p

        return p


def init(c, base_dir=None):
    global cfg, sem, cache, aliases, presets, _auth, _ffmpeg
    cfg = c
    _ffmpeg = shutil.which(cfg.get("ffmpeg_bin", "ffmpeg"))

    if base_dir:
        try:
            for k in ("voices_dir", "sounds_dir"):
                v = cfg.get(k)

                if v and not os.path.isabs(v):
                    cfg[k] = resolve_path(v, base_dir)
        except Exception:
            pass

    sem = threading.Semaphore(int(cfg.get("max_concurrency", 2)))
    cache = TTLCache(
        maxsize=int(cfg.get("cache_size", 64)), ttl=int(cfg.get("cache_ttl_s", 300))
    )
    aliases = dict(cfg.get("aliases", {}))
    presets = dict(cfg.get("presets", {}))
    mod.init_moderator(cfg, base_dir=base_dir)
    voice_fx.init(cfg)

    a = cfg.get("auth") or {}

    if a.get("enabled"):
        _auth = {"enabled": True, "keys": sec.ensure_keys(a, base_dir=base_dir)}
        logger.info(f"[auth] enabled; roles={list(_auth['keys'].keys())}")
    else:
        _auth = {"enabled": False, "keys": {}}
        logger.info("[auth] disabled")

    voices()


def warmup():
    """Preload the first piper voice so the first request is hot."""
    if not _piper_resident():
        return

    piper = sorted(
        [v for v in vc.values() if v.get("backend") == "piper"],
        key=lambda x: x["id"],
    )

    if not piper:
        return

    try:
        of_path = _temp_path(".wav")

        try:
            _piper_synth(piper[0], "warming up", of_path, None, None, None, None)
            logger.info(f"[warmup] piper voice ready: {piper[0]['id']}")
        finally:
            _rm(of_path)
    except Exception as e:
        logger.warning(f"[warmup] failed: {e}")


def warmup_voice(voice_id):
    """Preload a specific voice by id or alias so its first request is hot."""
    vid, _ = _resolve_voice_id(voice_id)
    info = _vinfo(vid)

    if not info:
        return False

    backend = info.get("backend")

    if backend == "piper" and not _piper_resident():
        return False

    try:
        of_path = _temp_path(".wav")

        try:
            _synth(info, "warming up", SynthParams(voice_id=vid), of_path)
            logger.info(f"[warmup] voice ready: {info['id']} ({backend})")
            return True
        finally:
            _rm(of_path)
    except Exception as e:
        logger.warning(f"[warmup] failed for {voice_id}: {e}")
        return False


def auth_enabled():
    return bool(_auth.get("enabled"))


def _role_key(role):
    return (_auth.get("keys") or {}).get(role)


def auth_ok(role, key):
    if not auth_enabled():
        return True

    if not key:
        return False

    exp = _role_key(role)

    if exp:
        return hmac.compare_digest(str(key), str(exp))

    for v in (_auth.get("keys") or {}).values():
        if hmac.compare_digest(str(key), str(v)):
            return True

    return False


def _scan():
    global scanned, vc
    v = {}
    base = cfg.get("voices_dir", DEFAULT_VOICES)

    for name in KOKORO_VOICES:
        v[name] = {
            "id": name,
            "backend": "kokoro",
            "model_path": None,
            "config_path": None,
            "sample_rate": KOKORO_SR,
            "speakers": 1,
            "language": "en-gb" if name.startswith("b") else "en-us",
        }

    p = (
        os.path.join(base, "piper")
        if os.path.isdir(os.path.join(base, "piper"))
        else base
    )

    for j in glob.glob(os.path.join(p, "**", "*.onnx.json"), recursive=True):
        m = j[:-5]
        if not os.path.exists(m):
            continue

        i = os.path.splitext(os.path.basename(m))[0]
        try:
            meta = json.loads(Path(j).read_text(encoding="utf-8"))
        except Exception:
            meta = {}

        v[i] = {
            "id": i,
            "backend": "piper",
            "model_path": m,
            "config_path": j,
            "sample_rate": meta.get(
                "sample_rate", meta.get("audio", {}).get("sample_rate", 22050)
            ),
            "speakers": len(meta.get("speakers", [0])),
            "language": meta.get("language", meta.get("espeak", {}).get("voice", "")),
        }

    vc = v
    scanned = True

    return [vc[k] for k in sorted(vc.keys())]


def _default_voice_id():
    return next(iter(sorted(voices(), key=lambda x: x["id"])))["id"]


def _resolve_voice_id(v):
    v = (v or "").strip()

    if v in aliases:
        v = aliases[v]

    if v in vc:
        return v, False

    return _default_voice_id(), bool(v)


def voices():
    return _scan() if not scanned else [vc[k] for k in sorted(vc.keys())]


def reload():
    global vc, scanned
    vc = {}
    scanned = False
    return len(voices())


def _vinfo(i):
    if i not in vc:
        voices()

    return vc.get(i)


def _san(s):
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(s.split())
    n = int(cfg.get("max_text_chars", 500))
    return s[:n]


def _alias_prefix(s):
    if ":" in s:
        h, t = s.split(":", 1)
        a = h.strip().lower()
        if a in aliases:
            return aliases[a], t.strip()

    return None, s


def _preset_prefix(s):
    if s.startswith("[") and "]" in s:
        tag = s[1 : s.index("]")].strip().lower()
        rest = s[s.index("]") + 1 :].strip()
        if tag in presets:
            return tag, rest

    return None, s


def _parse_speed_modifier(s):
    """
    Parse and remove [fast] or [slow] tag from text.
    Returns (clean_text, speed_multiplier).
    [fast] = 0.5 (half length_scale = faster)
    [slow] = 2.0 (double length_scale = slower)
    """
    m = _speed_re.search(s)

    if not m:
        return s, 1.0

    tag = m.group(1).lower()
    clean = (s[: m.start()] + s[m.end() :]).strip()

    multiplier = 0.5 if tag == "fast" else 2.0

    return clean, multiplier


@dataclass
class SynthParams:
    """The resolved voice and encoding settings for one request."""

    voice_id: str
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    speaker_id: int | None = None
    fmt: str = "mp3"
    normalize: bool = False
    bitrate: str = "128k"

    def cache_key(self, text, preset):
        return (
            self.voice_id,
            text,
            self.fmt,
            self.length_scale,
            self.noise_scale,
            self.noise_w,
            self.speaker_id,
            self.normalize,
            self.bitrate,
            preset,
        )


@dataclass
class RequestMeta:
    """What the response headers need beyond the synth settings."""

    request_id: str
    text: str
    preset: str
    mod_flags: dict
    requested_voice: str | None
    used_fallback: bool
    started_at: float

    def elapsed_ms(self):
        return int((time.time() - self.started_at) * 1000)


def _resp_headers(params, meta, mime, cached, duration_ms, extra=None):
    ext = "mp3" if mime == "audio/mpeg" else "wav"

    h = {
        "X-Req-Id": meta.request_id,
        "X-Voice": params.voice_id,
        "X-Format": mime,
        "X-Cache": "hit" if cached else "miss",
        "X-Text-Chars": str(len(meta.text)),
        "X-Duration-MS": str(duration_ms),
        "X-Preset": meta.preset or "",
        "Cache-Control": "no-store",
        "X-Mod-Urls": str(meta.mod_flags["urls"]),
        "X-Mod-Emojis": str(meta.mod_flags["emojis"]),
        "X-Mod-Slurs": str(meta.mod_flags["slurs"]),
        "Content-Disposition": (
            f'inline; filename="{params.voice_id}-{meta.request_id}.{ext}"'
        ),
        "X-Voice-Requested": meta.requested_voice or "",
        "X-Voice-Fallback": "1" if meta.used_fallback else "0",
    }

    if extra:
        h.update(extra)

    return h


def _temp_path(suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _rm(p):
    with contextlib.suppress(Exception):
        os.remove(p)


def _norm(w):
    if not bool(cfg.get("normalize", False)):
        return w

    if not _ffmpeg:
        return w

    n = w + ".norm.wav"
    r = subprocess.run(
        [
            _ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            w,
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            n,
        ],
        capture_output=True,
    )

    return n if r.returncode == 0 and os.path.exists(n) else w


def _mp3(w, br):
    if not _ffmpeg:
        return b""

    m = w + ".mp3"
    r = subprocess.run(
        [
            _ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            w,
            "-codec:a",
            "libmp3lame",
            "-b:a",
            br,
            m,
        ],
        capture_output=True,
    )

    if r.returncode != 0 or not os.path.exists(m):
        return b""

    b = Path(m).read_bytes()
    _rm(m)
    return b


def _kokoro_synth(txt, vid, ls, out_path):
    import numpy as np
    import soundfile as sf

    pipe = _kokoro_pipeline(vid)
    speed = 1.0 / float(ls) if ls else 1.0
    chunks = []

    for _, _, audio in pipe(txt, voice=vid, speed=speed):
        if audio is None:
            continue

        a = (
            audio.detach().cpu().numpy()
            if hasattr(audio, "detach")
            else np.asarray(audio)
        )
        chunks.append(a)

    if not chunks:
        raise RuntimeError("kokoro produced no audio")

    full = np.concatenate(chunks).astype(np.float32)
    sf.write(out_path, full, KOKORO_SR, subtype="PCM_16")


def _synth(info, text, params, out_path):
    if (info or {}).get("backend") == "kokoro":
        with sem:
            _kokoro_synth(text, params.voice_id, params.length_scale, out_path)
    else:
        with sem:
            _piper_synth(
                info,
                text,
                out_path,
                params.length_scale,
                params.noise_scale,
                params.noise_w,
                params.speaker_id,
            )


def _core(text, params):
    info = _vinfo(params.voice_id)

    of_path = _temp_path(".wav")

    rm = [of_path]

    try:
        _synth(info, text, params, of_path)

        fx = voice_fx.process_wav(of_path, voice_id=params.voice_id)

        if fx != of_path:
            rm.append(fx)

        src = _norm(fx) if params.normalize else fx

        if src != fx:
            rm.append(src)

        if params.fmt == "mp3":
            b = _mp3(src, params.bitrate)
            m = "audio/mpeg" if b else "audio/wav"

            if not b:
                b = Path(src).read_bytes()

        elif params.fmt == "wav":
            b = Path(src).read_bytes()
            m = "audio/wav"

        else:
            raise RuntimeError("bad format")

    finally:
        for p in rm:
            _rm(p)

    if not b or len(b) <= WAV_HEADER_BYTES:
        raise RuntimeError("empty audio")

    return b, m, info


def _resolve_request(d):
    """Turn a raw request dict into the settings the synth chain needs."""
    started_at = time.time()

    text = _san(d.get("text") or "")
    text, mod_flags = mod.filter_text(text)

    alias_voice, rest = _alias_prefix(text)
    preset_prefix, clean = _preset_prefix(rest)
    clean, speed_mult = _parse_speed_modifier(clean)

    if not clean:
        raise RuntimeError("empty")

    voice = (d.get("voice") or "").strip()
    voice = aliases.get(voice, voice)

    requested_voice = alias_voice or voice or None
    voice_id, used_fallback = _resolve_voice_id(requested_voice)

    preset = (d.get("preset") or preset_prefix or "").lower()
    preset_values = presets.get(preset, {})

    base_scale = d.get("length_scale", preset_values.get("length_scale"))
    length_scale = (base_scale or 1.0) * speed_mult if speed_mult != 1.0 else base_scale

    normalize = d.get("normalize")

    params = SynthParams(
        voice_id=voice_id,
        length_scale=length_scale,
        noise_scale=d.get("noise_scale", preset_values.get("noise_scale")),
        noise_w=d.get("noise_w", preset_values.get("noise_w")),
        speaker_id=d.get("speaker_id"),
        fmt=(d.get("format") or cfg.get("default_format", "mp3")).lower(),
        normalize=bool(
            normalize if normalize is not None else cfg.get("normalize", False)
        ),
        bitrate=d.get("bitrate") or cfg.get("mp3_bitrate", "128k"),
    )

    meta = RequestMeta(
        request_id=uuid.uuid4().hex[:8],
        text=clean,
        preset=preset,
        mod_flags=mod_flags,
        requested_voice=requested_voice,
        used_fallback=used_fallback,
        started_at=started_at,
    )

    return clean, params, meta


def tts(d):
    clean, params, meta = _resolve_request(d)

    if sfx.has_sfx_tags(clean):
        return _tts_with_sfx(clean, params, meta)

    key = params.cache_key(clean, meta.preset)
    hit = cache.get(key)

    if hit:
        b, m = hit
        return b, m, _resp_headers(params, meta, m, True, 0)

    b, m, info = _core(clean, params)
    cache[key] = (b, m)

    h = _resp_headers(
        params,
        meta,
        m,
        False,
        meta.elapsed_ms(),
        {"X-Sample-Rate": str(info["sample_rate"]), "X-Bytes": str(len(b))},
    )

    return b, m, h


def _tts_with_sfx(clean, params, meta):
    parts = sfx.parse_sfx_tags(clean)
    segs = []
    rm = []

    max_sfx = int(cfg.get("max_sfx_per_request", 10))
    sfx_count = 0

    try:
        for p in parts:
            if "sfx" in p:
                if sfx_count >= max_sfx:
                    continue

                _, ap = sfx.resolve_sfx(p["sfx"], cfg)

                if not ap:
                    continue

                # quiet the censor beeps relative to speech
                gain = 0

                if p["sfx"].startswith("censor-beep"):
                    gain = cfg.get("moderation", {}).get("censor_gain_db", 0)

                wav48 = _to_mono_wav(ap, gain_db=gain)
                segs.append(wav48)

                if wav48 != ap:
                    rm.append(wav48)

                sfx_count += 1

            else:
                txt = (p.get("text") or "").strip()

                if not txt:
                    continue

                wav, tmp = _render_tts_wav(txt, params)
                rm += tmp

                wav48 = _to_mono_wav(wav, trim=True)
                segs.append(wav48)

                if wav48 != wav:
                    rm.append(wav48)

        if not segs:
            raise RuntimeError("empty audio")

        b, m = _concat_wavs(segs, fmt=params.fmt, bitrate=params.bitrate)

        h = _resp_headers(
            params,
            meta,
            m,
            False,
            meta.elapsed_ms(),
            {"X-SFX-Count": str(sfx_count)},
        )

        return b, m, h

    finally:
        for pth in rm:
            _rm(pth)


def health():
    return {
        "ok": True,
        "backends": sorted({v.get("backend") for v in vc.values() if v.get("backend")}),
        "piper": (
            "resident"
            if _piper_resident() and _piper_voices
            else shutil.which(cfg.get("piper_bin", "piper")) or None
        ),
        "ffmpeg": shutil.which(cfg.get("ffmpeg_bin", "ffmpeg")) or None,
        "voices": len(vc) or len(voices()),
        "max_concurrency": int(cfg.get("max_concurrency", 2)),
        "cache": (
            {
                "items": len(cache),
                "capacity": cache.maxsize,
                "ttl_sec": cache.ttl,
            }
            if cache
            else {"items": 0, "capacity": 0, "ttl_sec": 0}
        ),
    }


def metrics():
    return {
        "cache": (
            {
                "items": len(cache),
                "capacity": cache.maxsize,
                "ttl_sec": cache.ttl,
            }
            if cache
            else {"items": 0, "capacity": 0, "ttl_sec": 0}
        ),
        "max_concurrency": int(cfg.get("max_concurrency", 2)),
        "voices": len(vc),
    }


def get_aliases():
    return aliases


def set_alias(n, v):
    aliases[n] = v


def del_alias(n):
    aliases.pop(n, None)


def _render_tts_wav(text, params):
    info = _vinfo(params.voice_id) or vc[_default_voice_id()]

    of_path = _temp_path(".wav")

    try:
        _synth(info, text, params, of_path)
        fx = voice_fx.process_wav(of_path, voice_id=params.voice_id)
        src = _norm(fx) if params.normalize else fx

        extra = []

        if fx != of_path:
            extra.append(fx)

        if src != fx:
            extra.append(src)

        return src, [of_path, *extra]

    except:
        _rm(of_path)
        raise


def _to_mono_wav(inp, sample_rate=48000, trim=False, gain_db=0):
    if not _ffmpeg:
        return inp

    out_path = _temp_path(".wav")

    cmd = [_ffmpeg, "-y", "-loglevel", "error", "-i", inp]

    af = []

    if trim:
        af.append(_SILENCE_TRIM)

    if gain_db:
        af.append(f"volume={gain_db}dB")

    if af:
        cmd += ["-af", ",".join(af)]

    cmd += ["-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", out_path]

    r = subprocess.run(cmd, capture_output=True)

    return out_path if r.returncode == 0 and os.path.exists(out_path) else inp


def _concat_wavs(paths, fmt="mp3", bitrate=None):
    if not _ffmpeg:
        raise RuntimeError("ffmpeg not found")

    lst_path = _temp_path(".txt")

    with open(lst_path, "w") as f:
        for p in paths:
            f.write(f"file '{p}'\n")

    merged_wav_path = _temp_path(".wav")

    r = subprocess.run(
        [
            _ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            lst_path,
            "-c",
            "copy",
            merged_wav_path,
        ],
        capture_output=True,
    )

    os.remove(lst_path)

    if r.returncode != 0 or not os.path.exists(merged_wav_path):
        raise RuntimeError("concat failed")

    if fmt == "wav":
        b = Path(merged_wav_path).read_bytes()
        os.remove(merged_wav_path)
        return b, "audio/wav"

    br = bitrate or cfg.get("mp3_bitrate", "128k")
    mp3 = _mp3(merged_wav_path, br)

    with contextlib.suppress(Exception):
        os.remove(merged_wav_path)

    if mp3:
        return mp3, "audio/mpeg"

    b = Path(merged_wav_path).read_bytes() if os.path.exists(merged_wav_path) else b""

    return b, "audio/wav"
