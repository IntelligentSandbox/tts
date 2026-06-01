import os
import random
import re
import unicodedata

from util import resolve_path

_url_re = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)

_BEEPS = ("censor-beep-1", "censor-beep-2", "censor-beep-3")

CENSOR_MODES = ("drop", "mask", "beep", "duck", "random")

_leet = {
    "a": "[a@4]",
    "b": "[b8]",
    "e": "[e3]",
    "i": "[i1!|]",
    "l": "[l1|]",
    "o": "[o0]",
    "s": "[s5$]",
    "t": "[t7]",
    "g": "[g9]",
    "z": "[z2]",
}

_emoji: set = set()

# TODO(393e): https://unicode.org/reports/tr51/tr51-12.html#Identification
with open(
    os.path.join(os.path.dirname(__file__), "assets", "emoji-data.txt"),
    encoding="utf-8",
) as f:
    for line in f:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue

        code = line.split(";", 1)[0].strip()

        if " " in code:
            continue

        if ".." in code:
            a, b = code.split("..")
            _emoji.update(range(int(a, 16), int(b, 16) + 1))
        else:
            _emoji.add(int(code, 16))


def _remove_emojis(s):
    """Remove Unicode emoji characters from a string."""
    return "".join(ch for ch in s if ord(ch) not in _emoji)


def _mask_token(src):
    """Mask all but the first and last character with asterisks."""
    return (
        "*" * len(src) if len(src) <= 2 else (src[0] + "*" * (len(src) - 2) + src[-1])
    )


def _censor_sound(word, mode):
    """Pick a censor sound id for a slur based on mode and length."""
    if mode == "duck":
        return "censor-beep-duck"

    if mode == "random":
        return random.choice(_BEEPS + ("censor-beep-duck",))

    # longer word gets a longer beep
    n = sum(ch.isalnum() for ch in word)

    if n <= 4:
        return _BEEPS[0]

    if n <= 7:
        return _BEEPS[1]

    return _BEEPS[2]


def _normalize(s):
    """Normalize a Unicode string using NFKD and remove combining characters."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _obfus_rx(term):
    """Compile a regex that matches obfuscated variants of a term."""
    t = _normalize(term.lower())
    parts = []
    for ch in t:
        parts.append(_leet.get(ch, re.escape(ch)) if ch.isalnum() else re.escape(ch))
    glue = r"[^a-zA-Z0-9]{0,2}"
    return re.compile(glue.join(parts), re.IGNORECASE)


class SlurCensor:
    def __init__(self, path):
        """
        Initialize a SlurCensor instance

        :param path: Path to block list
        """
        self.path = path
        self.rxs = []
        self.raw = []
        self.mtime = None
        if path:
            self._reload()

    def _read_terms(self):
        """Read terms from file, ignoring empty lines and comments"""
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f]
        return [t for t in lines if t and not t.startswith("#")]

    def _reload(self):
        """Reload terms and compile regexes from the file"""
        terms = self._read_terms()
        self.raw = terms
        self.rxs = [_obfus_rx(t) for t in terms]
        try:
            self.mtime = os.path.getmtime(self.path) if self.path else None
        except OSError:
            self.mtime = None

    def ensure_fresh(self):
        """Reload the terms if the file has been modified"""
        if not self.path:
            return
        try:
            mt = os.path.getmtime(self.path)
        except OSError:
            mt = None
        if mt and mt != self.mtime:
            self._reload()

    def reload(self):
        """Force reload of terms and regexes from teh file"""
        self._reload()

    def list(self):
        """Return the current list of terms."""
        self.ensure_fresh()
        return list(self.raw)

    def add(self, term):
        """Add a term to the blocklist and save to file."""
        term = (term or "").strip()
        if not term or term in self.raw:
            return False
        self.raw.append(term)
        self.rxs.append(_obfus_rx(term))
        self._save()
        return True

    def remove(self, term):
        """Remove a term from the blocklist and save to file."""
        term = (term or "").strip()
        if term not in self.raw:
            return False
        idx = self.raw.index(term)
        self.raw.pop(idx)
        self.rxs.pop(idx)
        self._save()
        return True

    def _save(self):
        """Persist current terms to the blocklist file."""
        if not self.path:
            return
        with open(self.path, "w", encoding="utf-8") as f:
            for t in self.raw:
                f.write(t + "\n")
        try:
            self.mtime = os.path.getmtime(self.path)
        except OSError:
            pass


class Moderator:
    def __init__(self, cfg=None):
        """
        Initialize a Moderator instance

        :param cfg: Optional configuration with keys:
                        'strip_urls', 'strip_emojis', 'censor_slurs', 'blocklist_path'
        """
        cfg = cfg or {}
        self.strip_urls = bool(cfg.get("strip_urls", True))
        self.strip_emojis = bool(cfg.get("strip_emojis", True))
        self.censor_slurs = bool(cfg.get("censor_slurs", True))
        self.censor_mode = (cfg.get("censor_mode") or "drop").lower()
        bl_path = cfg.get("blocklist_path")
        self.censor = SlurCensor(bl_path) if bl_path else SlurCensor(None)

    def filter(self, s, mode=None):
        """
        Filter a string based on the config rules

        :param s: Input string
        :param mode: mask, drop, beep, duck, or random (None uses config)
        :return: Tuple of filtered string and dictionary of flags e.g.,
                    {'urls': 0/1, 'emojis': 0/1', 'slurs': count}
        """
        mode = mode or self.censor_mode
        out = s or ""
        flags = {"urls": 0, "emojis": 0, "slurs": 0}

        if self.strip_urls:
            before = out
            out = _url_re.sub("[link]", out)
            if out != before:
                flags["urls"] = 1

        if self.strip_emojis:
            before = out
            out = _remove_emojis(out)
            if out != before:
                flags["emojis"] = 1

        if self.censor_slurs and self.censor:
            self.censor.ensure_fresh()
            n = 0

            def repl(m):
                nonlocal n
                n += 1
                src = m.group(0)

                if mode == "mask":
                    return _mask_token(src)

                if mode in ("beep", "duck", "random"):
                    return f"[SFX: {_censor_sound(src, mode)}]"

                return ""

            for rx in self.censor.rxs:
                out = rx.sub(repl, out)
            flags["slurs"] = n

        return out.strip(), flags


_moderator = None


def init_moderator(cfg, base_dir: str | None = None):
    """Initialize the global moderator instance."""
    global _moderator
    mcfg = dict(cfg.get("moderation") or {})
    if mcfg.get("blocklist_path"):
        mcfg["blocklist_path"] = resolve_path(mcfg["blocklist_path"], base_dir)
    _moderator = Moderator(mcfg) if mcfg.get("enabled", False) else None


def get_moderator():
    """Get the current moderator instance."""
    return _moderator


def mod_enabled():
    """Check if moderation is enabled."""
    return _moderator is not None


def mod_list():
    """List moderation terms."""
    if not _moderator:
        raise RuntimeError("moderation disabled")
    return _moderator.censor.list()


def mod_add(term):
    """Add a moderation term."""
    if not _moderator:
        raise RuntimeError("moderation disabled")
    return {"added": _moderator.censor.add(term)}


def mod_remove(term):
    """Remove a moderation term."""
    if not _moderator:
        raise RuntimeError("moderation disabled")
    return {"removed": _moderator.censor.remove(term)}


def mod_reload():
    """Reload moderation terms."""
    if not _moderator:
        raise RuntimeError("moderation disabled")
    _moderator.censor.reload()
    return {"reloaded": True}


def mod_mode():
    """Get the current censor mode."""
    if not _moderator:
        raise RuntimeError("moderation disabled")

    return {"mode": _moderator.censor_mode, "modes": list(CENSOR_MODES)}


def mod_set_mode(mode):
    """Set the censor mode at runtime."""
    if not _moderator:
        raise RuntimeError("moderation disabled")

    mode = (mode or "").strip().lower()

    if mode not in CENSOR_MODES:
        raise ValueError("bad mode")

    _moderator.censor_mode = mode

    return {"mode": mode}


def filter_text(text, mode=None):
    """Filter text using moderation rules."""
    if not _moderator:
        return text, {"urls": 0, "emojis": 0, "slurs": 0}

    return _moderator.filter(text, mode)
