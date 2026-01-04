-- List all embeds ordered by creation date
SELECT embed_id, jti, created_at, note, origin FROM embeds ORDER BY created_at DESC