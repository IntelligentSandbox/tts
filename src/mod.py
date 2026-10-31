import contextlib
import os
import random
import re
import unicodedata

import emoji
from echo_common import resolve_path

_url_re = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)

_BEEPS = ("censor-beep-1", "censor-beep-2", "censor-beep-3")

CENSOR_MODES = ("drop", "mask", "beep", "duck", "random")

MASK_WHOLE_WORD_CHARS = 2
SHORT_BEEP_CHARS = 4
MEDIUM_BEEP_CHARS = 7

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


def _remove_emojis(s):
    return emoji.replace_emoji(s, replace="")


def _mask_token(src):
    return (
        "*" * len(src)
        if len(src) <= MASK_WHOLE_WORD_CHARS
        else (src[0] + "*" * (len(src) - 2) + src[-1])
    )


def _censor_sound(word, mode):
    """Pick a censor sound id for a slur based on mode and length."""
    if mode == "duck":
        return "censor-beep-duck"

    if mode == "random":
        return random.choice((*_BEEPS, "censor-beep-duck"))

    # longer word gets a longer beep
    n = sum(ch.isalnum() for ch in word)

    if n <= SHORT_BEEP_CHARS:
        return _BEEPS[0]

    if n <= MEDIUM_BEEP_CHARS:
        return _BEEPS[1]

    return _BEEPS[2]


def _normalize(s):
    """Strip accents so "é" compares as "e"."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _obfus_rx(term):
    """Match leetspeak and spaced out spellings like "n.a$ty" too."""
    t = _normalize(term.lower())
    parts = [
        _leet.get(ch, re.escape(ch)) if ch.isalnum() else re.escape(ch) for ch in t
    ]
    glue = r"[^a-zA-Z0-9]{0,2}"
    # a term only counts as a whole word so "ass" leaves "class" alone
    body = glue.join(parts)
    return re.compile(rf"(?<![a-zA-Z0-9]){body}(?![a-zA-Z0-9])", re.IGNORECASE)


def _fold(s):
    """Fold accents off and keep where each char came from in the original."""
    out, src = [], []

    for i, ch in enumerate(s):
        for f in _normalize(ch):
            out.append(f)
            src.append(i)

    return "".join(out), src


def _sub_folded(rx, s, repl):
    """Match on the folded text but replace in the original so accents survive."""
    folded, src = _fold(s)
    out, last = [], 0

    for m in rx.finditer(folded):
        if m.start() == m.end():
            continue

        a = src[m.start()]
        b = src[m.end() - 1] + 1
        out += [s[last:a], repl(s[a:b])]
        last = b

    out.append(s[last:])
    return "".join(out)


class SlurCensor:
    def __init__(self, path):
        self.path = path
        self.rxs = []
        self.raw = []
        self.lines = []
        self.mtime = None
        if path:
            self.reload()

    def _read_lines(self):
        """Comments and blanks are kept so a save writes them back."""
        if not self.path or not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f]

    def reload(self):
        self.lines = self._read_lines()
        terms = [
            ln.strip()
            for ln in self.lines
            if ln.strip() and not ln.strip().startswith("#")
        ]
        self.raw = terms
        self.rxs = [_obfus_rx(t) for t in terms]
        try:
            self.mtime = os.path.getmtime(self.path) if self.path else None
        except OSError:
            self.mtime = None

    def ensure_fresh(self):
        """Pick up hand edits to the file without a reload call."""
        if not self.path:
            return
        try:
            mt = os.path.getmtime(self.path)
        except OSError:
            mt = None
        if mt and mt != self.mtime:
            self.reload()

    def list(self):
        self.ensure_fresh()
        return list(self.raw)

    def add(self, term):
        if term in self.raw:
            return
        self.raw.append(term)
        self.rxs.append(_obfus_rx(term))
        self.lines.append(term)
        self._save()

    def remove(self, term):
        if term not in self.raw:
            return
        idx = self.raw.index(term)
        self.raw.pop(idx)
        self.rxs.pop(idx)
        self.lines = [ln for ln in self.lines if ln.strip() != term]
        self._save()

    def _save(self):
        if not self.path:
            return
        with open(self.path, "w", encoding="utf-8") as f:
            for ln in self.lines:
                f.write(ln + "\n")
        with contextlib.suppress(OSError):
            self.mtime = os.path.getmtime(self.path)


class Moderator:
    def __init__(self, cfg):
        self.strip_urls = bool(cfg.get("strip_urls", True))
        self.strip_emojis = bool(cfg.get("strip_emojis", True))
        self.censor_slurs = bool(cfg.get("censor_slurs", True))
        self.censor_mode = (cfg.get("censor_mode") or "drop").lower()
        self.censor = SlurCensor(cfg.get("blocklist_path"))

    def filter(self, s):
        mode = self.censor_mode
        out = s or ""

        if self.strip_urls:
            out = _url_re.sub("[link]", out)

        if self.strip_emojis:
            out = _remove_emojis(out)

        if self.censor_slurs:
            self.censor.ensure_fresh()

            def repl(src):
                if mode == "mask":
                    return _mask_token(src)

                if mode in ("beep", "duck", "random"):
                    return "{" + _censor_sound(src, mode) + "}"

                return ""

            for rx in self.censor.rxs:
                out = _sub_folded(rx, out, repl)

        return out.strip()

    def state(self):
        return {
            "terms": self.censor.list(),
            "mode": self.censor_mode,
            "modes": list(CENSOR_MODES),
            "censoring": self.censor_slurs,
        }


def build_moderator(cfg, base_dir=None):
    mcfg = dict(cfg.get("moderation") or {})
    if mcfg.get("blocklist_path"):
        mcfg["blocklist_path"] = resolve_path(mcfg["blocklist_path"], base_dir)
    return Moderator(mcfg) if mcfg.get("enabled", False) else None
