-- greendragon: make "Ragnafluff The Ruinous" (mon 364, pad 1735) a real attackable boss.
-- The map is authored=1, so the server serves the COMPILED monBranch from the monsters columns.
-- mon 364.reaction_type was NULL (the row predated the data file's reactionType:1 and the monster
-- seed is INSERT-IF-ABSENT, so it never backfilled), and a compiled entry with no reactionType
-- defaults to `neutral` on the client -> a click-to-talk NPC, unfightable. Set it Hostile (1) to
-- match data/maps/greendragon.json. Pad already has apop_id=-1, so this restores the fightable
-- shape (reactionType 1, apopID -1), like redux_boss_attackable.sql did for Redux.
UPDATE monsters SET reaction_type = 1 WHERE mon_id = 364;

-- verify
SELECT m.mon_id, m.name, m.level, m.reaction_type, p.apop_id AS pad_apop,
       (COALESCE(p.apop_id, -1) <= 0 AND m.reaction_type = 1) AS attackable
FROM monsters m LEFT JOIN pad_npcs p ON p.mon_id = m.mon_id AND p.map = 'greendragon'
WHERE m.mon_id = 364;
