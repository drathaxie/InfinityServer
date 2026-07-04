-- redux map: make "Redux (Monster)" (mon 100000, pad 9001) a real attackable boss.
-- It already has reaction_type=1 + boss stats, but the pad pinned apop 6006 onto it,
-- which makes the client treat it as a click-to-talk NPC. Strip the apop (-1 = none) so
-- it matches the fightable-monster shape (reactionType 1, apopID -1), like Frogzard.
UPDATE pad_npcs SET apop_id = -1 WHERE map = 'redux' AND pad_id = 9001;

SELECT p.pad_id, p.mon_id, p.name, p.level, p.max_hp, p.apop_id, p.boss,
       m.reaction_type,
       (p.apop_id <= 0 AND m.reaction_type = 1) AS attackable
FROM pad_npcs p JOIN monsters m ON m.mon_id = p.mon_id
WHERE p.map = 'redux' ORDER BY p.pad_id;
