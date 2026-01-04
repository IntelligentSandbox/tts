import os
import sqlite3
import json

_conn = None


def _read_schema(filename):
    """Read SQL schema file from schemas directory."""
    schema_path = os.path.join(os.path.dirname(__file__), 'schemas', filename)
    with open(schema_path, 'r') as f:
        return f.read()


def init_db(path):
    global _conn
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    
    _conn = sqlite3.connect(path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row

    c = _conn.cursor()
    c.execute(_read_schema('tokens_db.sql'))
    c.execute(_read_schema('embeds_db.sql'))

    _conn.commit()


def insert_token(jti, roles, expires, created_by, created_at, note=""):
    if _conn is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    c = _conn.cursor()
    c.execute(_read_schema('insert_token.sql'), (jti, json.dumps(roles), int(expires), created_by, int(created_at), note))
    _conn.commit()


def get_token(jti):
    c = _conn.cursor()
    r = c.execute(_read_schema('get_token.sql'), (jti,)).fetchone()
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
    c = _conn.cursor()
    rows = c.execute(_read_schema('list_tokens.sql')).fetchall()
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
    c = _conn.cursor()
    r = c.execute(_read_schema('revoke_token.sql'), (jti,))
    _conn.commit()
    return r.rowcount > 0


def revoke_token_prefix(prefix):
    c = _conn.cursor()
    like = prefix + "%"
    r = c.execute(_read_schema('revoke_token_prefix.sql'), (like,))
    _conn.commit()
    return r.rowcount > 0


def insert_embed(embed_id, jti, created_at, note="", origin=None):
    c = _conn.cursor()
    c.execute(_read_schema('insert_embed.sql'), (embed_id, jti, int(created_at), note, origin))
    _conn.commit()


def get_embed(embed_id):
    c = _conn.cursor()
    r = c.execute(_read_schema('get_embed.sql'), (embed_id,)).fetchone()
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
    c = _conn.cursor()
    r = c.execute(_read_schema('delete_embed.sql'), (embed_id,))
    _conn.commit()
    return r.rowcount > 0


def list_embeds():
    c = _conn.cursor()
    rows = c.execute(_read_schema('list_embeds.sql')).fetchall()
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
