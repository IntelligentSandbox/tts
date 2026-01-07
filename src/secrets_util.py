import os
import stat
import secrets

import yaml

from log import logger

ROLES = ["admin", "mod", "tts", "push", "pull", "overlay"]
DEFAULT_SECRETS = os.path.join(os.path.dirname(__file__), "private", "secrets.yaml")

_POSSIBLE_PATHS = [
    os.path.join(os.path.dirname(__file__), "private", "config.yaml"),
    os.path.join(os.path.dirname(__file__), "config.yaml"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.yaml"),
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "private", "config.yaml"),
    os.path.join(os.getcwd(), "config.yaml"),
]

_CFG_DIR = os.path.dirname(DEFAULT_SECRETS)
for _p in _POSSIBLE_PATHS:
    if os.path.exists(_p):
        _CFG_DIR = os.path.dirname(_p)
        break

TOKEN_LEN = 32
SECRET_LEN = 48
FILE_MODE = stat.S_IRUSR | stat.S_IWUSR


def _chmod600(p):
    try:
        os.chmod(p, FILE_MODE)
    except Exception:
        pass


def _resolve(p, base_dir=None):
    if not p:
        return DEFAULT_SECRETS

    if os.path.isabs(p):
        return p

    if base_dir:
        base = os.path.normpath(os.path.join(_CFG_DIR, base_dir)) if not os.path.isabs(base_dir) else base_dir
        return os.path.normpath(os.path.join(base, p))

    return os.path.normpath(os.path.join(_CFG_DIR, p))


def _read(p, base_dir=None):
    rp = _resolve(p, base_dir)

    if os.path.exists(rp):
        with open(rp, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    return {}


def _write(p, data, base_dir=None):
    rp = _resolve(p, base_dir)
    os.makedirs(os.path.dirname(rp) or ".", exist_ok=True)

    with open(rp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True)

    _chmod600(rp)


def ensure_session_secret(path=None, base_dir=None):
    """Ensure session secret exists."""
    rp = _resolve(path, base_dir)
    data = _read(path, base_dir)

    if "session_secret" not in data:
        data["session_secret"] = secrets.token_urlsafe(SECRET_LEN)
        _write(path, data, base_dir)
        logger.info(f"[session] wrote {rp}")

    return data["session_secret"]


def ensure_keys(auth_cfg, base_dir=None):
    """Ensure auth keys exist."""
    path = (auth_cfg or {}).get("file") or DEFAULT_SECRETS
    rp = _resolve(path, base_dir)
    data = _read(path, base_dir)
    ks = dict(data.get("keys", {}))
    created = []

    for r in ROLES:
        if r == "mod":
            continue

        if not ks.get(r):
            ks[r] = secrets.token_urlsafe(TOKEN_LEN)
            created.append(r)

    if created or "keys" not in data:
        data["keys"] = ks
        _write(path, data, base_dir)
        logger.info(f"[auth] wrote {rp}")

        for r in created:
            logger.info(f"[auth] save this {r} key: {ks[r]}")

    return ks


def ensure_jwt_secret(path=None, base_dir=None):
    """Ensure JWT secret exists."""
    rp = _resolve(path, base_dir)
    data = _read(path, base_dir)

    if "jwt_secret" not in data:
        data["jwt_secret"] = secrets.token_urlsafe(SECRET_LEN)
        _write(path, data, base_dir)
        logger.info(f"[jwt] wrote {rp}")

    return data["jwt_secret"]


def get_oauth_provider(provider, path=None, base_dir=None):
    """Get OAuth provider config."""
    data = _read(path, base_dir)
    return (data.get("oauth") or {}).get(provider, {})


def save_oauth_mapping(provider, remote_id, role, path=None, base_dir=None):
    """Save OAuth mapping."""
    data = _read(path, base_dir)
    oauth = data.setdefault("oauth", {})
    maps = oauth.setdefault("mappings", {})
    prov = maps.setdefault(provider, {})

    r = str(remote_id)
    if not r.isdigit():
        r = r.lower()

    prov[r] = role
    _write(path, data, base_dir)


def list_oauth_mappings(provider=None, path=None, base_dir=None):
    """List OAuth mappings."""
    data = _read(path, base_dir)
    maps = (data.get("oauth") or {}).get("mappings") or {}

    if provider:
        return maps.get(provider) or {}

    return maps


def delete_oauth_mapping(provider, remote_id, path=None, base_dir=None):
    """Delete OAuth mapping."""
    data = _read(path, base_dir)
    oauth = data.get("oauth") or {}
    maps = oauth.get("mappings") or {}
    prov = maps.get(provider) or {}

    r = str(remote_id)
    rl = r.lower()

    for key in (r, rl):
        if key in prov:
            del prov[key]
            oauth["mappings"] = maps
            data["oauth"] = oauth
            _write(path, data, base_dir)
            return True

    return False
