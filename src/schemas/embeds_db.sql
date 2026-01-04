CREATE TABLE
  IF NOT EXISTS embeds (
    embed_id TEXT PRIMARY KEY,
    jti TEXT,
    created_at INTEGER,
    note TEXT,
    origin TEXT
  )