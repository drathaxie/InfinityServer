-- Collapse duplicate catalog entries: one native item + one synthetic 900k twin.
--
-- BACKGROUND. Our catalog holds two ID spaces. Below 900000 the IDs are real AE item
-- IDs. At 900000+ they are synthetic -- the import API returned names and assets but no
-- IDs, so the importer derived item_id = 900000 + <AE asset id>. Where it re-imported an
-- item that already existed natively, we ended up with the same item twice.
--
-- "Same name" is NOT sufficient evidence of a duplicate: 85 pairs are two distinct
-- synthetic items that merely share a name, and 2 native/synthetic pairs are genuinely
-- different items ("drakath armor" is DrakathArmor2-24Mar15 natively and
-- BanditDrakathTempArmor synthetically). Those are deliberately left alone.
--
-- The proof used here is the asset id: the native row's filename carries it, and the
-- synthetic id IS 900000 + that same id. When they agree, the two rows are the same
-- underlying item. Filenames come in two shapes -- "items/staves/40_staff01.unity3d"
-- and bare "2511_icm1" -- so both are matched.
--
-- Run via the rehearse or apply wrapper; this body has no BEGIN/COMMIT of its own.

-- ---------------------------------------------------------------- classification
CREATE TEMP TABLE dupname AS
  SELECT lower(name) AS nm FROM items GROUP BY lower(name) HAVING COUNT(*) = 2;
CREATE INDEX ON dupname(nm);
ANALYZE dupname;

CREATE TEMP TABLE pairs AS
SELECT d.nm,
       n.item_id AS native_id, n.filename AS native_file,
       n.item_type AS native_type, n.equip_spot AS native_equip,
       s.item_id AS synth_id,  s.filename AS synth_file,
       s.item_type AS synth_type,  s.equip_spot AS synth_equip,
       COALESCE(
         NULLIF(substring(n.filename from '/([0-9]+)_'), ''),
         NULLIF(substring(n.filename from '^([0-9]+)_'), '')
       )::bigint AS native_asset_id,
       s.item_id - 900000 AS synth_asset_id
FROM dupname d
JOIN items n ON lower(n.name) = d.nm AND n.item_id <  900000
JOIN items s ON lower(s.name) = d.nm AND s.item_id >= 900000;
CREATE INDEX ON pairs(synth_id);
CREATE INDEX ON pairs(native_id);
ANALYZE pairs;

-- A: the asset id proves they are the same item.
CREATE TEMP TABLE merge_proven AS
  SELECT native_id, synth_id, nm FROM pairs
   WHERE native_asset_id IS NOT NULL AND native_asset_id = synth_asset_id;

-- B: the native row has no filename at all, and type + equip slot agree. The synthetic
--    twin carries the asset the native is missing, so the native is enriched from it.
CREATE TEMP TABLE merge_stub AS
  SELECT native_id, synth_id, nm, synth_file FROM pairs
   WHERE native_asset_id IS NULL
     AND COALESCE(native_file, '') = ''
     AND native_type  IS NOT DISTINCT FROM synth_type
     AND native_equip IS NOT DISTINCT FROM synth_equip;

CREATE TEMP TABLE to_merge AS
  SELECT native_id, synth_id FROM merge_proven
  UNION ALL
  SELECT native_id, synth_id FROM merge_stub;
CREATE INDEX ON to_merge(synth_id);
ANALYZE to_merge;

\echo '-- classified:'
SELECT (SELECT COUNT(*) FROM pairs)        AS native_synth_pairs,
       (SELECT COUNT(*) FROM merge_proven) AS proven_same_item,
       (SELECT COUNT(*) FROM merge_stub)   AS stub_enriched,
       (SELECT COUNT(*) FROM to_merge)     AS total_to_merge,
       (SELECT COUNT(*) FROM pairs) - (SELECT COUNT(*) FROM to_merge) AS left_alone;

-- ---------------------------------------------------------------- enrich
-- Give the native row the filename it was missing, taken from its synthetic twin.
UPDATE items i
   SET filename = m.synth_file
  FROM merge_stub m
 WHERE i.item_id = m.native_id
   AND COALESCE(i.filename, '') = '';

-- ---------------------------------------------------------------- repoint
-- Move every reference off the synthetic twin and onto the native item. char_items is
-- one row per owned instance, so a character legitimately ends up with two rows of the
-- same item_id where they held both sides; that is a valid state, not a duplicate.
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

-- The Flash-server mapping should point at the surviving row.
UPDATE sword_item_map t SET infinity_item_id = m.native_id
  FROM to_merge m WHERE t.infinity_item_id = m.synth_id;

-- ---------------------------------------------------------------- guard
-- Abort rather than orphan anything: if a reference to a doomed synthetic survives,
-- this raises and the whole transaction rolls back.
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
    RAISE EXCEPTION 'aborting: % reference(s) still point at a synthetic twin due for deletion', n;
  END IF;
END $$;

-- ---------------------------------------------------------------- delete
DELETE FROM items i USING to_merge m WHERE i.item_id = m.synth_id;
