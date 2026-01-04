-- List all tokens ordered by creation date
SELECT jti, roles, expires, created_by, created_at, revoked, note FROM tokens ORDER BY created_at DESC