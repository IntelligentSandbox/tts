import os

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def resolve_path(p, base_dir=None):
    """Resolve path relative to base_dir if not absolute."""
    if not p or os.path.isabs(p):
        return p or ""

    if base_dir:
        return os.path.normpath(os.path.join(base_dir, p))

    return p
