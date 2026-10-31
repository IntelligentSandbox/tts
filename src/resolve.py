import re
import unicodedata
from dataclasses import dataclass

_tag_re = re.compile(r"\[([^\[\]]+)\]")

NO_VOICES = "No voices found. Add some to voices/piper or voices/kokoro/voices"


class BadRequest(ValueError):
    """The caller sent something unusable so it is a 400 and not a crash."""


# frozen so the params themselves can sit in the cache key
@dataclass(frozen=True)
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
    fx: str | None = None


def sanitize(s, max_chars):
    # A null byte makes piper throw and nobody means to say one
    s = "".join(c for c in (s or "") if unicodedata.category(c) != "Cc" or c.isspace())
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = " ".join(s.split())
    return s[:max_chars]


def _name_prefix(s, names):
    """Split off a "name: rest" prefix when the name is one we know."""
    if ":" in s:
        h, t = s.split(":", 1)
        name = h.strip().lower()

        if name in names:
            return name, t.strip()

    return None, s


def _pick(key, *sources, default=None):
    """First source that has a real value wins. A missing key and an explicit
    null both fall through."""
    for s in sources:
        v = s.get(key)

        if v is not None:
            return v

    return default


class Resolver:
    """Turns a request into synth params through its aliases, presets and profiles."""

    def __init__(self, cfg, voices, fx, moderator=None):
        self.cfg = cfg
        self.voices = voices
        self.fx = fx
        self.moderator = moderator
        self.aliases = {
            str(k).strip().lower(): v for k, v in (cfg.get("aliases") or {}).items()
        }
        self.presets = dict(cfg.get("presets", {}))
        self.profiles = {
            str(k).strip().lower(): dict(v or {})
            for k, v in (cfg.get("profiles") or {}).items()
        }

    @property
    def max_text_chars(self):
        return int(self.cfg.get("max_text_chars", 500))

    def voice_id(self, v):
        v = (v or "").strip()
        # aliases are stored lowercase but voice ids keep their case
        v = self.aliases.get(v.lower(), v)

        if self.voices.get(v):
            return v

        if not self.voices.all():
            raise BadRequest(NO_VOICES)

        return self.voices.default_id()

    def _preset_tag(self, s):
        """Pull the first [preset] tag out of the text wherever it sits."""
        for m in _tag_re.finditer(s):
            tag = m.group(1).strip().lower()

            if tag in self.presets:
                return tag, " ".join((s[: m.start()] + " " + s[m.end() :]).split())

        return None, s

    def params_for(self, d):
        """Layer the request over its profile, then its preset, then the config.

        Both the single and the batch route go through here so a part behaves the
        same either way.
        """
        cfg = self.cfg
        profile = str(d.get("profile") or "").strip().lower()
        pro = self.profiles.get(profile, {})

        preset = str(d.get("preset") or pro.get("preset") or "").lower()
        pre = self.presets.get(preset, {})

        fmt = _pick("format", d, pro, default=cfg.get("default_format", "mp3"))
        normalize = _pick("normalize", d, pro, default=cfg.get("normalize", False))

        return SynthParams(
            voice_id=self.voice_id(_pick("voice", d, pro)),
            length_scale=_pick("length_scale", d, pro, pre),
            noise_scale=_pick("noise_scale", d, pro, pre),
            noise_w=_pick("noise_w", d, pro, pre),
            speaker_id=_pick("speaker_id", d, pro),
            fmt=str(fmt).lower(),
            normalize=bool(normalize),
            bitrate=_pick("bitrate", d, pro, default=cfg.get("mp3_bitrate", "128k")),
            fx=_pick("fx", d, pro),
        )

    def resolve(self, d):
        """Turn a raw request dict into the settings the synth chain needs."""
        text = sanitize(d.get("text") or "", self.max_text_chars)

        if self.moderator:
            text = self.moderator.filter(text)

        profile_prefix, rest = _name_prefix(text, self.profiles)
        alias_prefix, rest = _name_prefix(rest, self.aliases)
        preset_prefix, clean = self._preset_tag(rest)

        if not clean:
            raise BadRequest("empty")

        src = dict(d)

        for key, from_prefix in (
            ("profile", profile_prefix),
            ("preset", preset_prefix),
            ("voice", self.aliases.get(alias_prefix)),
        ):
            if from_prefix and not src.get(key):
                src[key] = from_prefix

        return clean, self.params_for(src)

    def set_alias(self, n, v):
        self.aliases[n] = v

    def del_alias(self, n):
        self.aliases.pop(n, None)

    def set_profile(self, n, v):
        """An inline chain registers under the profile name so a character can be
        retuned without a restart."""
        v = {k: val for k, val in v.items() if val is not None}
        chain = v.pop("chain", None)

        if chain is not None:
            v["fx"] = self.fx.set_chain(v.get("fx") or n, chain)

        self.profiles[n] = v

    def del_profile(self, n):
        self.profiles.pop(n, None)
        self.fx.del_chain(n)
