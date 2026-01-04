-- Revoke token by JTI
UPDATE tokens SET revoked=1 WHERE jti=?