-- Remove the leftover inert Redux NPC (pad 9000) from the redux map, leaving only the
-- fightable boss (pad 9001). Delete the child pad_npcs rows first, then the pad.
BEGIN;
DELETE FROM pad_npcs WHERE map = 'redux' AND pad_id = 9000;
DELETE FROM map_pads WHERE map = 'redux' AND pad_id = 9000;
COMMIT;

-- verify: only the boss pad remains
SELECT p.pad_id, p.mon_id, p.name, p.level, p.max_hp, p.apop_id, p.boss, m.reaction_type
FROM pad_npcs p JOIN monsters m ON m.mon_id = p.mon_id
WHERE p.map = 'redux' ORDER BY p.pad_id;
