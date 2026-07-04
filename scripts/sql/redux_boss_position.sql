-- The Redux boss pad (9001) sat at (-20.95,-20.95) — off the bottom of the artix2 map,
-- so nothing showed. Move it to (-10.95,-10.95): the exact spot where the old NPC was
-- clearly visible (its level/HP were readable), guaranteeing the boss is on-screen.
UPDATE map_pads SET x = -10.951, y = -10.951 WHERE map = 'redux' AND pad_id = 9001;

SELECT pad_id, x, y, frame FROM map_pads WHERE map = 'redux' ORDER BY pad_id;
