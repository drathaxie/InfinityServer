-- Second de-duplication pass: nine pairs the first pass could not prove.
--
-- The first pass matched the AE asset id embedded in the native row's filename against
-- the synthetic id (900000 + asset id). These nine have a native filename with no
-- directory and no numeric prefix -- "icm1" rather than "items/potions/2511_icm1" -- so
-- the asset id was not extractable. Their stems match exactly, and because neither side
-- carries a directory there is no risk of a false stem collision across folders.
--
-- The pairs are listed explicitly rather than matched: a stem rule broad enough to catch
-- these would also catch 32 all-synthetic pairs where the stem matches only because the
-- directory was stripped (items/swords/100_foo vs items/daggers/200_foo), and merging
-- those would destroy genuinely distinct items.
--
-- Run via the round2 rehearse/apply wrapper; no BEGIN/COMMIT of its own.

CREATE TEMP TABLE to_merge (native_id BIGINT, synth_id BIGINT);
INSERT INTO to_merge (native_id, synth_id) VALUES
  ( 5572, 902511),   -- mana potion              icm1
  ( 5586, 902516),   -- elders' blood            icb3
  ( 5590, 902520),   -- scroll of life steal     isi1
  (12917, 922974),   -- scroll of enrage         iwd1
  (17438, 905229),   -- reputation boost! (1 hr) icbrep
  (17439, 907142),   -- gold boost! (1 hr)       icbgold
  (45561, 929432),   -- [class item] entropic corruption  cchronoEC
  (45604, 931626),   -- [class item] foresee corruption   cchronoFC
  (45605, 931627);   -- [class item] infinite corruption  cchronoIC
CREATE INDEX ON to_merge(synth_id);
ANALYZE to_merge;

-- Sanity: every pair must still exist and still share a name, or something moved under us.
DO $$
DECLARE bad bigint;
BEGIN
  SELECT COUNT(*) INTO bad
  FROM to_merge m
  LEFT JOIN items n ON n.item_id = m.native_id
  LEFT JOIN items s ON s.item_id = m.synth_id
  WHERE n.item_id IS NULL OR s.item_id IS NULL OR lower(n.name) <> lower(s.name);
  IF bad <> 0 THEN
    RAISE EXCEPTION 'aborting: % pair(s) missing or no longer name-matched', bad;
  END IF;
END $$;

-- NO filename enrichment here, unlike the first pass. That pass only filled filenames
-- that were EMPTY, which is always an improvement. Doing the same on these nine would be
-- wrong: their native filenames are already correct. The catalog convention for this item
-- class is a bare, unprefixed stem -- Health Potion is "ich1", Potent Honor Potion "icb1",
-- Scroll of Scorched Steel "ief1" -- so the native "icm1" is right and the synthetic's
-- "2511_icm1" is the import artefact. (Applying it left the only 9 bare-prefixed
-- filenames among 1903 native rows, which is how the error was caught; it was reverted.)
--
-- The native row keeps its real AE id and its own filename. Only the duplicate goes.

UPDATE char_items        t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE shop_items        t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE monster_drops     t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE global_drops      t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE statues           t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE item_sponsors     t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE ac_item_buybacks  t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE quest_rewards     t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE quest_turnins     t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE token_redemptions t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE token_redemptions t SET token_item_id = m.native_id FROM to_merge m WHERE t.token_item_id = m.synth_id;
UPDATE account_audit     t SET item_id       = m.native_id FROM to_merge m WHERE t.item_id       = m.synth_id;
UPDATE sword_item_map    t SET infinity_item_id = m.native_id FROM to_merge m WHERE t.infinity_item_id = m.synth_id;

DO $$
DECLARE n bigint;
BEGIN
  SELECT
      (SELECT COUNT(*) FROM char_items        t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM shop_items        t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM monster_drops     t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM global_drops      t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM statues           t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM item_sponsors     t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM ac_item_buybacks  t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM quest_rewards     t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM quest_turnins     t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM token_redemptions t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM token_redemptions t JOIN to_merge m ON m.synth_id = t.token_item_id)
    + (SELECT COUNT(*) FROM account_audit     t JOIN to_merge m ON m.synth_id = t.item_id)
    + (SELECT COUNT(*) FROM sword_item_map    t JOIN to_merge m ON m.synth_id = t.infinity_item_id)
  INTO n;
  IF n <> 0 THEN
    RAISE EXCEPTION 'aborting: % reference(s) still point at a synthetic due for deletion', n;
  END IF;
END $$;

DELETE FROM items i USING to_merge m WHERE i.item_id = m.synth_id;
