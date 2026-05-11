import os
import glob
import json
import re
import time
import uuid
import shlex
import shutil
import tempfile
import subprocess
import threading
import hmac
import wave
from collections import OrderedDict
from log import configure, logger
from cachetools import TTLCache

import secrets_util as sec
import sfx
import mod
import voice_fx
from util import resolve_path

cfg = {}
vc = {}
scanned = False
sem = None
aliases = {}
presets = {}
cache = None
_auth = {"enabled": False, "keys": {}}
_speed_re = re.compile(r"\[(fast|slow)\]", re.IGNORECASE)


DEFAULT_VOICES = os.path.join(os.path.dirname(__file__), "..", "voices")
DEFAULT_SOUNDS = os.path.join(os.path.dirname(__file__), "..", "sounds")

# Built-in kokoro voices (downloaded on demand from HF by the kokoro package).
# Prefix encodes (a)merican/(b)ritish + (f)emale/(m)ale.
# TODO(6adf): support loading kokoro voice .pt files from <voices_dir>/kokoro/ so users
# can pre-cache the full set (avoid first-call HF download) or drop custom voices.
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

_kokoro_pipelines = {}
_kokoro_lock = threading.Lock()


def _backend():
    return (cfg.get("backend", "piper") or "piper").strip().lower()


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
    global cfg, sem, cache, aliases, presets, _auth
    cfg = c
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
    b = _backend()

    if b == "kokoro":
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
    else:
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
            except:
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
                "language": meta.get(
                    "language", meta.get("espeak", {}).get("voice", "")
                ),
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


def _which(b):
    return shutil.which(b)


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


def _cmd(info, txt, out, ls, ns, nw, ss, spk):
    c = [
        cfg.get("piper_bin", "piper"),
        "--model",
        info["model_path"],
        "--config",
        info["config_path"],
        "--input_file",
        txt,
        "--output_file",
        out,
        "-q",
    ]

    if spk is not None:
        c += ["--speaker", str(spk)]
    if ls is not None:
        c += ["--length_scale", str(ls)]
    if ns is not None:
        c += ["--noise_scale", str(ns)]
    if nw is not None:
        c += ["--noise_w", str(nw)]
    if ss is not None:
        c += ["--sentence_silence", str(ss)]

    return c


def _norm(w):
    if not bool(cfg.get("normalize", False)):
        return w

    f = _which(cfg.get("ffmpeg_bin", "ffmpeg"))

    if not f:
        return w

    n = w + ".norm.wav"
    r = subprocess.run(
        [
            f,
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
    f = _which(cfg.get("ffmpeg_bin", "ffmpeg"))

    if not f:
        return b""

    m = w + ".mp3"
    r = subprocess.run(
        [
            f,
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

    try:
        os.remove(m)
    except:
        pass

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


def _core(txt, vid, fmt, ls, ns, nw, ss, spk, norm, br):
    info = _vinfo(vid)
    is_kokoro = (info or {}).get("backend") == "kokoro"

    if not is_kokoro and not _which(cfg.get("piper_bin", "piper")):
        raise RuntimeError("piper not found")

    tf = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", delete=False
    )
    tf.write(txt + "\n")
    tf.close()

    of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    of.close()

    rm = [tf.name, of.name]

    try:
        if is_kokoro:
            with sem:
                _kokoro_synth(txt, vid, ls, of.name)
        else:
            c = _cmd(info, tf.name, of.name, ls, ns, nw, ss, spk)

            with sem:
                r = subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if r.returncode != 0 or not os.path.exists(of.name):
                raise RuntimeError("piper failed")

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
            try:
                os.remove(p)
            except:
                pass

    if not b or len(b) <= 44:
        raise RuntimeError("empty audio")

    return b, m, info


def tts(d):
    t0 = time.time()

    tx = _san(d.get("text") or "")
    if not tx:
        raise RuntimeError("empty")

    tx, mod_flags = mod.filter_text(tx, mode="drop")
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

                _, ap = sfx._resolve_sfx(p["sfx"], cfg)
                if not ap:
                    continue

                wav48 = _to_48k_mono_wav(ap)
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

                wav48 = _to_48k_mono_wav(wav)
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
            try:
                os.remove(pth)
            except:
                pass


def health():
    return {
        "ok": True,
        "backend": _backend(),
        "piper": _which(cfg.get("piper_bin", "piper")) or None,
        "ffmpeg": _which(cfg.get("ffmpeg_bin", "ffmpeg")) or None,
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


def _synth_wav_to_path(text, vid, ls, ns, nw, ss, spk):
    info = _vinfo(vid) if vid in vc else _vinfo(_default_voice_id())
    is_kokoro = (info or {}).get("backend") == "kokoro"

    if not is_kokoro and not _which(cfg.get("piper_bin", "piper")):
        raise RuntimeError("piper not found")

    tf = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", delete=False
    )
    tf.write(text + "\n")
    tf.close()

    of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    of.close()

    if is_kokoro:
        _kokoro_synth(text, vid, ls, of.name)
    else:
        c = _cmd(info, tf.name, of.name, ls, ns, nw, ss, spk)
        r = subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0 or not os.path.exists(of.name):
            raise RuntimeError("piper failed")

    return tf.name, of.name


def _resample_to_uniform(wav_in, sr):
    f = _which(cfg.get("ffmpeg_bin", "ffmpeg"))

    if not f:
        return wav_in

    out = wav_in + f".{sr}.u.wav"
    r = subprocess.run(
        [
            f,
            "-y",
            "-loglevel",
            "error",
            "-i",
            wav_in,
            "-ar",
            str(sr),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            out,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    return out if r.returncode == 0 and os.path.exists(out) else wav_in


def _render_tts_wav(txt, vid, ls, ns, nw, ss, spk, norm):
    info = _vinfo(vid) or vc[_default_voice_id()]
    is_kokoro = (info or {}).get("backend") == "kokoro"

    if not is_kokoro and not _which(cfg.get("piper_bin", "piper")):
        raise RuntimeError("piper not found")

    tf = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", delete=False
    )
    tf.write(txt + "\n")
    tf.close()

    of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    of.close()

    try:
        if is_kokoro:
            with sem:
                _kokoro_synth(txt, vid, ls, of.name)
        else:
            c = _cmd(info, tf.name, of.name, ls, ns, nw, ss, spk)
            with sem:
                r = subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if r.returncode != 0 or not os.path.exists(of.name):
                raise RuntimeError("piper failed")

        fx = voice_fx.process_wav(of.name, voice_id=vid)
        src = _norm(fx) if norm else fx

        extra = []
        if fx != of.name:
            extra.append(fx)
        if src != fx:
            extra.append(src)

        return src, [tf.name, of.name] + extra

    except:
        for p in [tf.name, of.name]:
            try:
                os.remove(p)
            except:
                pass
        raise


def _to_48k_mono_wav(inp):
    f = _which(cfg.get("ffmpeg_bin", "ffmpeg"))

    if not f:
        return inp

    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()

    r = subprocess.run(
        [
            f,
            "-y",
            "-loglevel",
            "error",
            "-i",
            inp,
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            out.name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    return out.name if r.returncode == 0 and os.path.exists(out.name) else inp


def _concat_wavs(paths, fmt="mp3", bitrate=None):
    f = _which(cfg.get("ffmpeg_bin", "ffmpeg"))

    if not f:
        raise RuntimeError("ffmpeg not found")

    lst = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    for p in paths:
        lst.write(f"file '{p}'\n")
    lst.close()

    merged_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    merged_wav.close()

    r = subprocess.run(
        [
            f,
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
    except:
        pass

    if mp3:
        return mp3, "audio/mpeg"

    b = open(merged_wav.name, "rb").read() if os.path.exists(merged_wav.name) else b""

    return b, "audio/wav"
