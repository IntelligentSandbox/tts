-- Revoke tokens by prefix
UPDATE tokens SET revoked=1 WHERE jti LIKE ?