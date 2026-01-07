import os
import re
import glob

from log import logger

DEFAULT_SOUNDS = os.path.join(os.path.dirname(__file__), "..", "sounds")
SFX_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a")

_sfx_re = re.compile(r"\[SFX:\s*([^\]]+)\]", re.IGNORECASE)

sfx_files = {}
sfx_aliases = {}


def _scan_sounds(cfg):
    """Scan for sound files."""
    global sfx_files
    sfx_files = {}
    d = cfg.get("sounds_dir", DEFAULT_SOUNDS)

    if not os.path.isdir(d):
        return sfx_files

    for root, _, files in os.walk(d):
        for fn in files:
            lo = fn.lower()

            if any(lo.endswith(ext) for ext in SFX_EXTENSIONS):
                base, _ = os.path.splitext(fn)
                sfx_files[base] = os.path.join(root, fn)

    return sfx_files


def get_sfx_index(cfg):
    """Get index of available SFX files."""
    if not sfx_files:
        _scan_sounds(cfg)

    out = {}
    sd = os.path.abspath(cfg.get("sounds_dir", DEFAULT_SOUNDS))

    for sid, ap in sfx_files.items():
        rel = os.path.relpath(ap, sd).replace("\\", "/")
        out[sid] = {"file": rel}

    return out


def get_sfx_aliases():
    """Get current SFX aliases."""
    return dict(sfx_aliases)


def set_sfx_alias(name, target_id, cfg):
    """Set an SFX alias."""
    name = (name or "").strip().lower()

    if not name or target_id not in get_sfx_index(cfg):
        raise ValueError("bad alias")

    sfx_aliases[name] = target_id


def del_sfx_alias(name):
    """Delete an SFX alias."""
    sfx_aliases.pop((name or "").strip().lower(), None)


def _resolve_sfx(name, cfg):
    """Resolve SFX name to URL and path."""
    idx = get_sfx_index(cfg)
    key = (name or "").lower()
    sid = sfx_aliases.get(key, key)
    info = idx.get(sid)

    if not info:
        return None, None

    url = "/sounds/" + info["file"]
    abspath = sfx_files[sid]

    return url, abspath


def parse_sfx_tags(text):
    """Parse [SFX: name] tags into parts list."""
    parts = []
    last_end = 0

    for m in _sfx_re.finditer(text):
        before = text[last_end:m.start()].strip()

        if before:
            parts.append({"text": before})

        sfx_name = m.group(1).strip()

        if sfx_name:
            parts.append({"sfx": sfx_name})

        last_end = m.end()

    after = text[last_end:].strip()

    if after:
        parts.append({"text": after})

    return parts


def has_sfx_tags(text):
    """Check if text contains SFX tags."""
    return bool(_sfx_re.search(text))
