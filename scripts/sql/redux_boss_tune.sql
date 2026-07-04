-- Tune "Redux (Monster)" (mon 100000, redux pad 9001) to a balanced mini-boss:
-- level 10 -> monster damage 50-90/hit (5-9 per level), 5000 HP. Beatable solo at low level.
-- Set on both the pad (authored placement) and the monster record so template/served HP agree.
UPDATE pad_npcs SET level = 10, max_hp = 5000 WHERE map = 'redux' AND pad_id = 9001;
UPDATE monsters SET level = 10, hp = 5000, hp_max = 5000 WHERE mon_id = 100000;

SELECT p.pad_id, p.mon_id, p.level AS pad_level, p.max_hp AS pad_hp, m.level AS mon_level,
       m.hp_max AS mon_hp, m.scale
FROM pad_npcs p JOIN monsters m ON m.mon_id = p.mon_id
WHERE p.map = 'redux';
