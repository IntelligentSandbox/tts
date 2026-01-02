import os
import glob
import json
import time
import uuid
import shlex
import shutil
import tempfile
import subprocess
import threading
import hmac
from collections import OrderedDict
from log import TTSLogger

import mod
import secrets_util as sec

cfg = {}
vc = {}
scanned = False
sem = None
aliases = {}
presets = {}
cache = None
moder = None
_auth = {"enabled": False, "keys": {}}
sfx_files = {}
sfx_aliases = {}
# TODO: Add singleton TTSLogger function or a global configuration.

DEFAULT_SOUNDS = os.path.join(os.path.dirname(__file__), "..", "sounds")
DEFAULT_VOICES = os.path.join(os.path.dirname(__file__), "..", "voices")


class LRU:
    def __init__(self, n, ttl):
        self.n = max(0, n)
        self.ttl = max(0, ttl)
        self.od = OrderedDict()
        self.lk = threading.Lock()

    def get(self, k):
        if self.n == 0 or self.ttl == 0:
            return None
        now = time.time()
        with self.lk:
            it = self.od.get(k)
            if not it:
                return None
            b, m, ts = it
            if now - ts > self.ttl:
                self.od.pop(k, None)
                return None
            self.od.move_to_end(k)
            return b, m

    def put(self, k, b, m):
        if self.n == 0 or self.ttl == 0:
            return
        with self.lk:
            self.od[k] = (b, m, time.time())
            self.od.move_to_end(k)
            while len(self.od) > self.n:
                self.od.popitem(last=False)

    def stats(self):
        with self.lk:
            return {"items": len(self.od), "capacity": self.n, "ttl_sec": self.ttl}


def _resolve_path(p: str, base_dir: str | None) -> str:
    """Resolve a path relative to base_dir if not absolute."""
    if not p or os.path.isabs(p):
        return p
    if base_dir:
        return os.path.normpath(os.path.join(base_dir, p))
    return p


def init(c, base_dir: str | None = None):
    global cfg, sem, cache, aliases, presets, moder, _auth
    cfg = c
    if base_dir:
        try:
            for k in ("voices_dir", "sounds_dir"):
                v = cfg.get(k)
                if v and not os.path.isabs(v):
                    cfg[k] = _resolve_path(v, base_dir)
        except Exception:
            pass
    sem = threading.Semaphore(int(cfg.get("max_concurrency", 2)))
    cache = LRU(int(cfg.get("cache_size", 64)), int(cfg.get("cache_ttl_s", 300)))
    aliases = dict(cfg.get("aliases", {}))
    presets = dict(cfg.get("presets", {}))
    mcfg = cfg.get("moderation") or {}
    if mcfg.get("blocklist_path"):
        mcfg["blocklist_path"] = _resolve_path(mcfg["blocklist_path"], base_dir)
    moder = mod.Moderator(mcfg) if mcfg.get("enabled", False) else None
    a = cfg.get("auth") or {}
    if a.get("enabled"):
        _auth = {"enabled": True, "keys": sec.ensure_keys(a, base_dir=base_dir)}
        print(f"[auth] enabled; roles={list(_auth['keys'].keys())}")
    else:
        _auth = {"enabled": False, "keys": {}}
        print("[auth] disabled")
    voices()


def _scan_sounds():
    global sfx_files
    sfx_files = {}
    d = cfg.get("sounds_dir", DEFAULT_SOUNDS)
    exts = (".mp3", ".wav", ".ogg", ".m4a")
    if not os.path.isdir(d):
        return sfx_files
    for root, _, files in os.walk(d):
        for fn in files:
            lo = fn.lower()
            if any(lo.endswith(x) for x in exts):
                base, ext = os.path.splitext(fn)
                # last one wins if duplicate ids
                sfx_files[base] = os.path.join(root, fn)
    return sfx_files


def get_sfx_index():
    if not sfx_files:
        _scan_sounds()
    # expose "file" relative to /sounds mount
    out = {}
    sd = os.path.abspath(cfg.get("sounds_dir", DEFAULT_SOUNDS))
    for sid, ap in sfx_files.items():
        rel = os.path.relpath(ap, sd).replace("\\", "/")
        out[sid] = {"file": rel}
    return out


def get_sfx_aliases():
    return dict(sfx_aliases)


def set_sfx_alias(name, target_id):
    name = (name or "").strip().lower()
    if not name or target_id not in get_sfx_index():
        raise ValueError("bad alias")
    sfx_aliases[name] = target_id


def del_sfx_alias(name):
    sfx_aliases.pop((name or "").strip().lower(), None)


def _resolve_sfx(name):
    idx = get_sfx_index()
    key = (name or "").lower()
    sid = sfx_aliases.get(key, key)
    info = idx.get(sid)
    if not info:
        return None, None
    url = "/sounds/" + info["file"]
    abspath = sfx_files[sid]
    return url, abspath


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


def mod_enabled():
    return moder is not None


def mod_list():
    if not moder:
        raise RuntimeError("moderation disabled")
    return moder.censor.list()


def mod_add(term):
    if not moder:
        raise RuntimeError("moderation disabled")
    return {"added": moder.censor.add(term)}


def mod_remove(term):
    if not moder:
        raise RuntimeError("moderation disabled")
    return {"removed": moder.censor.remove(term)}


def mod_reload():
    if not moder:
        raise RuntimeError("moderation disabled")
    moder.censor.reload()
    return {"reloaded": True}


def _scan():
    global scanned, vc
    v = {}
    p = cfg.get("voices_dir", DEFAULT_VOICES)
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


def _core(txt, vid, fmt, ls, ns, nw, ss, spk, norm, br):
    info = _vinfo(vid)
    if not _which(cfg.get("piper_bin", "piper")):
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
        c = _cmd(info, tf.name, of.name, ls, ns, nw, ss, spk)
        with sem:
            r = subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0 or not os.path.exists(of.name):
            raise RuntimeError("piper failed")
        src = _norm(of.name) if norm else of.name
        if src != of.name:
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
    mod_flags = {"urls": 0, "emojis": 0, "slurs": 0}
    if moder:
        tx2, flags = moder.filter(tx, mode="drop")
        tx = tx2
        mod_flags = flags
    if not tx:
        raise RuntimeError("empty")
    a1, rest = _alias_prefix(tx)
    p1, clean = _preset_prefix(rest)
    vf = (d.get("voice") or "").strip()
    if vf in aliases:
        vf = aliases[vf]
    req_voice = a1 or vf or None
    vid, used_fallback = _resolve_voice_id(req_voice)
    psel = (d.get("preset") or p1 or "").lower()
    pv = presets.get(psel, {})
    ls = d.get("length_scale", pv.get("length_scale"))
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
    key = (vid, clean, fmt, ls, ns, nw, ss, spk, norm, br, psel)
    hit = cache.get(key)
    rid = uuid.uuid4().hex[:8]
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
    cache.put(key, b, m)
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


def health():
    return {
        "ok": True,
        "piper": _which(cfg.get("piper_bin", "piper")) or None,
        "ffmpeg": _which(cfg.get("ffmpeg_bin", "ffmpeg")) or None,
        "voices": len(vc) or len(voices()),
        "max_concurrency": int(cfg.get("max_concurrency", 2)),
        "cache": cache.stats(),
    }


def metrics():
    return {
        "cache": cache.stats(),
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
    if not _which(cfg.get("piper_bin", "piper")):
        raise RuntimeError("piper not found")
    tf = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", delete=False
    )
    tf.write(text + "\n")
    tf.close()
    of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    of.close()
    c = _cmd(info, tf.name, of.name, ls, ns, nw, ss, spk)
    r = subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0 or not os.path.exists(of.name):
        raise RuntimeError("piper failed")
    return tf.name, of.name  # caller cleans up


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
    if not _which(cfg.get("piper_bin", "piper")):
        raise RuntimeError("piper not found")
    tf = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".txt", delete=False
    )
    tf.write(txt + "\n")
    tf.close()
    of = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    of.close()
    c = _cmd(info, tf.name, of.name, ls, ns, nw, ss, spk)
    try:
        with sem:
            r = subprocess.run(c, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0 or not os.path.exists(of.name):
            raise RuntimeError("piper failed")
        src = _norm(of.name) if norm else of.name
        return src, [tf.name, of.name] + ([] if src == of.name else [src])
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
    # mp3 encode
    br = bitrate or cfg.get("mp3_bitrate", "128k")
    mp3 = _mp3(merged_wav.name, br)
    try:
        os.remove(merged_wav.name)
    except:
        pass
    if mp3:
        return mp3, "audio/mpeg"
    # fallback: return wav if mp3 failed
    b = open(merged_wav.name, "rb").read() if os.path.exists(merged_wav.name) else b""
    return b, "audio/wav"
