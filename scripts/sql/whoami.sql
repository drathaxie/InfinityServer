-- Sample query for ./scripts/db.ps1 -File scripts\sql\whoami.sql
-- Lists every character with their account, level, gold and staff access level.
SELECT c.id,
       c.name,
       c.level,
       c.gold,
       c.access_level,
       a.username
FROM characters c
LEFT JOIN accounts a ON a.id = c.account_id
ORDER BY c.access_level DESC, c.id;
