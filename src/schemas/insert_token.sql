-- Insert token query
INSERT OR REPLACE INTO tokens (jti, roles, expires, created_by, created_at, revoked, note) VALUES (?, ?, ?, ?, ?, 0, ?)