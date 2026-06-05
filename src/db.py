import json
import os
import sqlite3

TOKENS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    jti TEXT PRIMARY KEY,
    roles TEXT,
    expires INTEGER,
    created_by TEXT,
    created_at INTEGER,
    revoked INTEGER DEFAULT 0,
    note TEXT
)
"""

EMBEDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeds (
    embed_id TEXT PRIMARY KEY,
    jti TEXT,
    created_at INTEGER,
    note TEXT,
    origin TEXT
)
"""

_conn = None


def init_db(path):
    """Initialize database."""
    global _conn

    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)

    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row

    c = _conn.cursor()
    c.execute(TOKENS_SCHEMA)
    c.execute(EMBEDS_SCHEMA)
    _conn.commit()


def _token_row(r):
    return {
        "jti": r["jti"],
        "roles": json.loads(r["roles"]),
        "expires": r["expires"],
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "revoked": bool(r["revoked"]),
        "note": r["note"],
    }


def _embed_row(r):
    return {
        "embed_id": r["embed_id"],
        "jti": r["jti"],
        "created_at": r["created_at"],
        "note": r["note"],
        "origin": r["origin"],
    }


def insert_token(jti, roles, expires, created_by, created_at, note=""):
    """Insert a token."""
    if _conn is None:
        raise RuntimeError("db not initialized")

    c = _conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO tokens"
        " (jti, roles, expires, created_by, created_at, revoked, note)"
        " VALUES (?, ?, ?, ?, ?, 0, ?)",
        (jti, json.dumps(roles), int(expires), created_by, int(created_at), note),
    )
    _conn.commit()


def get_token(jti):
    """Get a token by jti."""
    c = _conn.cursor()
    r = c.execute("SELECT * FROM tokens WHERE jti=?", (jti,)).fetchone()

    if not r:
        return None

    return _token_row(r)


def list_tokens():
    """List all tokens."""
    c = _conn.cursor()
    rows = c.execute(
        "SELECT jti, roles, expires, created_by, created_at, revoked, note"
        " FROM tokens ORDER BY created_at DESC"
    ).fetchall()
    out = []

    for r in rows:
        out.append(_token_row(r))

    return out


def revoke_token(jti):
    """Revoke a token."""
    c = _conn.cursor()
    r = c.execute("UPDATE tokens SET revoked=1 WHERE jti=?", (jti,))
    _conn.commit()
    return r.rowcount > 0


def revoke_token_prefix(prefix):
    """Revoke tokens by prefix."""
    c = _conn.cursor()
    r = c.execute("UPDATE tokens SET revoked=1 WHERE jti LIKE ?", (prefix + "%",))
    _conn.commit()
    return r.rowcount > 0


def insert_embed(embed_id, jti, created_at, note="", origin=None):
    """Insert an embed."""
    c = _conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO embeds (embed_id, jti, created_at, note, origin)"
        " VALUES (?, ?, ?, ?, ?)",
        (embed_id, jti, int(created_at), note, origin),
    )
    _conn.commit()


def get_embed(embed_id):
    """Get an embed by id."""
    c = _conn.cursor()
    r = c.execute("SELECT * FROM embeds WHERE embed_id=?", (embed_id,)).fetchone()

    if not r:
        return None

    return _embed_row(r)


def delete_embed(embed_id):
    """Delete an embed."""
    c = _conn.cursor()
    r = c.execute("DELETE FROM embeds WHERE embed_id=?", (embed_id,))
    _conn.commit()
    return r.rowcount > 0


def list_embeds():
    """List all embeds."""
    c = _conn.cursor()
    rows = c.execute(
        "SELECT embed_id, jti, created_at, note, origin"
        " FROM embeds ORDER BY created_at DESC"
    ).fetchall()
    out = []

    for r in rows:
        out.append(_embed_row(r))

    return out
