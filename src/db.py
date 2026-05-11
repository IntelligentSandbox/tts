import os
import sqlite3
import json

SCHEMAS_DIR = os.path.join(os.path.dirname(__file__), "schemas")

_conn = None


def _schema(name):
    """Read SQL schema file."""
    with open(os.path.join(SCHEMAS_DIR, name), "r") as f:
        return f.read()


def init_db(path):
    """Initialize database."""
    global _conn

    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)

    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row

    c = _conn.cursor()
    c.execute(_schema("tokens_db.sql"))
    c.execute(_schema("embeds_db.sql"))
    _conn.commit()


def insert_token(jti, roles, expires, created_by, created_at, note=""):
    """Insert a token."""
    if _conn is None:
        raise RuntimeError("db not initialized")

    c = _conn.cursor()
    c.execute(
        _schema("insert_token.sql"),
        (jti, json.dumps(roles), int(expires), created_by, int(created_at), note),
    )
    _conn.commit()


def get_token(jti):
    """Get a token by jti."""
    c = _conn.cursor()
    r = c.execute(_schema("get_token.sql"), (jti,)).fetchone()

    if not r:
        return None

    return {
        "jti": r["jti"],
        "roles": json.loads(r["roles"]),
        "expires": r["expires"],
        "created_by": r["created_by"],
        "created_at": r["created_at"],
        "revoked": bool(r["revoked"]),
        "note": r["note"],
    }


def list_tokens():
    """List all tokens."""
    c = _conn.cursor()
    rows = c.execute(_schema("list_tokens.sql")).fetchall()
    out = []

    for r in rows:
        out.append(
            {
                "jti": r["jti"],
                "roles": json.loads(r["roles"]),
                "expires": r["expires"],
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "revoked": bool(r["revoked"]),
                "note": r["note"],
            }
        )

    return out


def revoke_token(jti):
    """Revoke a token."""
    c = _conn.cursor()
    r = c.execute(_schema("revoke_token.sql"), (jti,))
    _conn.commit()
    return r.rowcount > 0


def revoke_token_prefix(prefix):
    """Revoke tokens by prefix."""
    c = _conn.cursor()
    r = c.execute(_schema("revoke_token_prefix.sql"), (prefix + "%",))
    _conn.commit()
    return r.rowcount > 0


def insert_embed(embed_id, jti, created_at, note="", origin=None):
    """Insert an embed."""
    c = _conn.cursor()
    c.execute(
        _schema("insert_embed.sql"), (embed_id, jti, int(created_at), note, origin)
    )
    _conn.commit()


def get_embed(embed_id):
    """Get an embed by id."""
    c = _conn.cursor()
    r = c.execute(_schema("get_embed.sql"), (embed_id,)).fetchone()

    if not r:
        return None

    return {
        "embed_id": r["embed_id"],
        "jti": r["jti"],
        "created_at": r["created_at"],
        "note": r["note"],
        "origin": r["origin"],
    }


def delete_embed(embed_id):
    """Delete an embed."""
    c = _conn.cursor()
    r = c.execute(_schema("delete_embed.sql"), (embed_id,))
    _conn.commit()
    return r.rowcount > 0


def list_embeds():
    """List all embeds."""
    c = _conn.cursor()
    rows = c.execute(_schema("list_embeds.sql")).fetchall()
    out = []

    for r in rows:
        out.append(
            {
                "embed_id": r["embed_id"],
                "jti": r["jti"],
                "created_at": r["created_at"],
                "note": r["note"],
                "origin": r["origin"],
            }
        )

    return out
