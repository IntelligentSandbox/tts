-- Create a "tokens" table
CREATE TABLE
  IF NOT EXISTS tokens (
    jti TEXT PRIMARY KEY,
    roles TEXT,
    expires INTEGER,
    created_by TEXT,
    created_at INTEGER,
    revoked INTEGER DEFAULT 0,
    note TEXT
  )