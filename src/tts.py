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

from cachetools import TTLCache
from echo_common import resolve_path

import mod
import secrets_util as sec
import sfx
import voice_fx
from echo_common import logger

cfg: dict = {}
vc: dict = {}
scanned = False
sem = None
aliases: dict = {}
presets: dict = {}
cache = None
_auth: dict = {"enabled": False, "keys": {}}
_ffmpeg: str | None = None
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

_kokoro_pipelines: dict = {}
_kokoro_lock = threading.Lock()

_piper_voices: dict = {}
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


def init(c, base_dir: str | None = None):
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
        of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        of.close()

        try:
            _piper_synth(piper[0], "warming up", of.name, None, None, None, None)
            logger.info(f"[warmup] piper voice ready: {piper[0]['id']}")
        finally:
            _rm(of.name)
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
        of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        of.close()

        try:
            _synth(info, "warming up", vid, None, None, None, None, of.name)
            logger.info(f"[warmup] voice ready: {info['id']} ({backend})")
            return True
        finally:
            _rm(of.name)
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
            meta = json.load(open(j, "r", encoding="utf-8"))
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


def _rm(p):
    try:
        os.remove(p)
    except Exception:
        pass


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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if r.returncode != 0 or not os.path.exists(m):
        return b""

    b = open(m, "rb").read()
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


def _synth(info, txt, vid, ls, ns, nw, spk, out_path):
    if (info or {}).get("backend") == "kokoro":
        with sem:
            _kokoro_synth(txt, vid, ls, out_path)
    else:
        with sem:
            _piper_synth(info, txt, out_path, ls, ns, nw, spk)


def _core(txt, vid, fmt, ls, ns, nw, ss, spk, norm, br):
    info = _vinfo(vid)

    of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    of.close()

    rm = [of.name]

    try:
        _synth(info, txt, vid, ls, ns, nw, spk, of.name)

        fx = voice_fx.process_wav(of.name, voice_id=vid)

        if fx != of.name:
            rm.append(fx)

        src = _norm(fx) if norm else fx

        if src != fx:
            rm.append(src)

        if fmt == "mp3":
            b = _mp3(src, br)
            m = "audio/mpeg" if b else "audio/wav"

            if not b:
                b = open(src, "rb").read()

        elif fmt == "wav":
            b = open(src, "rb").read()
            m = "audio/wav"

        else:
            raise RuntimeError("bad format")

    finally:
        for p in rm:
            _rm(p)

    if not b or len(b) <= 44:
        raise RuntimeError("empty audio")

    return b, m, info


def tts(d):
    t0 = time.time()

    tx = _san(d.get("text") or "")

    if not tx:
        raise RuntimeError("empty")

    tx, mod_flags = mod.filter_text(tx)

    if not tx:
        raise RuntimeError("empty")

    a1, rest = _alias_prefix(tx)
    p1, clean = _preset_prefix(rest)

    clean, speed_mult = _parse_speed_modifier(clean)

    if not clean:
        raise RuntimeError("empty")

    vf = (d.get("voice") or "").strip()

    if vf in aliases:
        vf = aliases[vf]

    req_voice = a1 or vf or None
    vid, used_fallback = _resolve_voice_id(req_voice)

    psel = (d.get("preset") or p1 or "").lower()
    pv = presets.get(psel, {})

    base_ls = d.get("length_scale", pv.get("length_scale"))
    ls = (base_ls or 1.0) * speed_mult if speed_mult != 1.0 else base_ls

    ns = d.get("noise_scale", pv.get("noise_scale"))
    nw = d.get("noise_w", pv.get("noise_w"))
    ss = d.get("sentence_silence", pv.get("sentence_silence"))
    spk = d.get("speaker_id")

    fmt = (d.get("format") or cfg.get("default_format", "mp3")).lower()
    norm = bool(
        d.get("normalize")
        if d.get("normalize") is not None
        else cfg.get("normalize", False)
    )
    br = d.get("bitrate") or cfg.get("mp3_bitrate", "128k")
    rid = uuid.uuid4().hex[:8]

    if sfx.has_sfx_tags(clean):
        return _tts_with_sfx(
            clean,
            vid,
            fmt,
            ls,
            ns,
            nw,
            ss,
            spk,
            norm,
            br,
            rid,
            req_voice,
            used_fallback,
            mod_flags,
            psel,
            t0,
        )

    key = (vid, clean, fmt, ls, ns, nw, ss, spk, norm, br, psel)
    hit = cache.get(key)

    if hit:
        b, m = hit
        h = {
            "X-Req-Id": rid,
            "X-Voice": vid,
            "X-Format": m,
            "X-Cache": "hit",
            "X-Text-Chars": str(len(clean)),
            "X-Duration-MS": "0",
            "X-Preset": psel or "",
            "Cache-Control": "no-store",
            "X-Mod-Urls": str(mod_flags["urls"]),
            "X-Mod-Emojis": str(mod_flags["emojis"]),
            "X-Mod-Slurs": str(mod_flags["slurs"]),
        }

        ext = "mp3" if m == "audio/mpeg" else "wav"
        h["Content-Disposition"] = f'inline; filename="{vid}-{rid}.{ext}"'
        h["X-Voice-Requested"] = req_voice or ""
        h["X-Voice-Fallback"] = "1" if used_fallback else "0"

        return b, m, h

    b, m, info = _core(clean, vid, fmt, ls, ns, nw, ss, spk, norm, br)
    cache[key] = (b, m)

    dur = int((time.time() - t0) * 1000)

    h = {
        "X-Req-Id": rid,
        "X-Voice": vid,
        "X-Format": m,
        "X-Cache": "miss",
        "X-Sample-Rate": str(info["sample_rate"]),
        "X-Bytes": str(len(b)),
        "X-Text-Chars": str(len(clean)),
        "X-Duration-MS": str(dur),
        "X-Preset": psel or "",
        "Cache-Control": "no-store",
        "X-Mod-Urls": str(mod_flags["urls"]),
        "X-Mod-Emojis": str(mod_flags["emojis"]),
        "X-Mod-Slurs": str(mod_flags["slurs"]),
    }

    ext = "mp3" if m == "audio/mpeg" else "wav"
    h["Content-Disposition"] = f'inline; filename="{vid}-{rid}.{ext}"'
    h["X-Voice-Requested"] = req_voice or ""
    h["X-Voice-Fallback"] = "1" if used_fallback else "0"

    return b, m, h


def _tts_with_sfx(
    clean,
    vid,
    fmt,
    ls,
    ns,
    nw,
    ss,
    spk,
    norm,
    br,
    rid,
    req_voice,
    used_fallback,
    mod_flags,
    psel,
    t0,
):
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

                wav, tmp = _render_tts_wav(txt, vid, ls, ns, nw, ss, spk, norm)
                rm += tmp

                wav48 = _to_mono_wav(wav, trim=True)
                segs.append(wav48)

                if wav48 != wav:
                    rm.append(wav48)

        if not segs:
            raise RuntimeError("empty audio")

        b, m = _concat_wavs(segs, fmt=fmt, bitrate=br)

        dur = int((time.time() - t0) * 1000)

        h = {
            "X-Req-Id": rid,
            "X-Voice": vid,
            "X-Format": m,
            "X-Cache": "miss",
            "X-Text-Chars": str(len(clean)),
            "X-Duration-MS": str(dur),
            "X-Preset": psel or "",
            "X-SFX-Count": str(sfx_count),
            "Cache-Control": "no-store",
            "X-Mod-Urls": str(mod_flags["urls"]),
            "X-Mod-Emojis": str(mod_flags["emojis"]),
            "X-Mod-Slurs": str(mod_flags["slurs"]),
        }

        ext = "mp3" if m == "audio/mpeg" else "wav"
        h["Content-Disposition"] = f'inline; filename="{vid}-{rid}.{ext}"'
        h["X-Voice-Requested"] = req_voice or ""
        h["X-Voice-Fallback"] = "1" if used_fallback else "0"

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


def _render_tts_wav(txt, vid, ls, ns, nw, ss, spk, norm):
    info = _vinfo(vid) or vc[_default_voice_id()]

    of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    of.close()

    try:
        _synth(info, txt, vid, ls, ns, nw, spk, of.name)
        fx = voice_fx.process_wav(of.name, voice_id=vid)
        src = _norm(fx) if norm else fx

        extra = []

        if fx != of.name:
            extra.append(fx)

        if src != fx:
            extra.append(src)

        return src, [of.name] + extra

    except:
        _rm(of.name)
        raise


def _to_mono_wav(inp, sample_rate=48000, trim=False, gain_db=0):
    if not _ffmpeg:
        return inp

    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()

    cmd = [_ffmpeg, "-y", "-loglevel", "error", "-i", inp]

    af = []

    if trim:
        af.append(_SILENCE_TRIM)

    if gain_db:
        af.append(f"volume={gain_db}dB")

    if af:
        cmd += ["-af", ",".join(af)]

    cmd += ["-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", out.name]

    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return out.name if r.returncode == 0 and os.path.exists(out.name) else inp


def _concat_wavs(paths, fmt="mp3", bitrate=None):
    if not _ffmpeg:
        raise RuntimeError("ffmpeg not found")

    lst = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)

    for p in paths:
        lst.write(f"file '{p}'\n")

    lst.close()

    merged_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    merged_wav.close()

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
            lst.name,
            "-c",
            "copy",
            merged_wav.name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    os.remove(lst.name)

    if r.returncode != 0 or not os.path.exists(merged_wav.name):
        raise RuntimeError("concat failed")

    if fmt == "wav":
        b = open(merged_wav.name, "rb").read()
        os.remove(merged_wav.name)
        return b, "audio/wav"

    br = bitrate or cfg.get("mp3_bitrate", "128k")
    mp3 = _mp3(merged_wav.name, br)

    try:
        os.remove(merged_wav.name)
    except Exception:
        pass

    if mp3:
        return mp3, "audio/mpeg"

    b = open(merged_wav.name, "rb").read() if os.path.exists(merged_wav.name) else b""

    return b, "audio/wav"
