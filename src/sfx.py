import os
import re

DEFAULT_SOUNDS = os.path.join(os.path.dirname(__file__), "..", "sounds")
SFX_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a")

_sfx_re = re.compile(r"\{([a-z0-9_-]+)\}", re.IGNORECASE)


class Sounds:
    """The sound files on disk plus the aliases that point at them."""

    def __init__(self, sounds_dir=None, aliases=None):
        self.dir = sounds_dir or DEFAULT_SOUNDS
        self.aliases = {str(k).strip().lower(): v for k, v in (aliases or {}).items()}
        self._files = None

    def rescan(self):
        """Pick up sound files added or removed since the last scan."""
        self._files = {}

        for root, _, files in os.walk(self.dir):
            for fn in files:
                if fn.lower().endswith(SFX_EXTENSIONS):
                    self._files[os.path.splitext(fn)[0]] = os.path.join(root, fn)

    def files(self):
        if self._files is None:
            self.rescan()

        return self._files

    def index(self):
        sd = os.path.abspath(self.dir)
        return {
            sid: {"file": os.path.relpath(ap, sd).replace("\\", "/")}
            for sid, ap in self.files().items()
        }

    def set_alias(self, name, target_id):
        name = (name or "").strip().lower()

        if not name or target_id not in self.files():
            raise ValueError("bad alias")

        self.aliases[name] = target_id

    def del_alias(self, name):
        self.aliases.pop((name or "").strip().lower(), None)

    def resolve(self, name):
        """Resolve a sound name or alias to its file path."""
        key = (name or "").lower()
        return self.files().get(self.aliases.get(key, key))


def parse_sfx_tags(text):
    """Parse {name} tags into parts list."""
    parts = []
    last_end = 0

    for m in _sfx_re.finditer(text):
        before = text[last_end : m.start()].strip()

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
