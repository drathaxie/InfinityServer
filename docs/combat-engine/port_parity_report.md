# Port parity report — Conviction/Hunger as data vs combat.py

Same casts, same seeds, same `combat._hit` rolls on both paths;
every Attack packet, post-cast pool, kill list and damage total
compared. Normalization applied: `PlayerAnimation` without a
`Speed` key equals `Speed: 1.0` (the client's NodePlayerAnimation
default) — the engine omits unauthored optional keys, matching
the captured AE wire shape.

## Paladin (Reduxidain 69420) — Conviction

| # | skill | rp after | nodes | result |
|---|-------|----------|-------|--------|
| 0 | 90373 | 3 | 8 | MATCH |
| 1 | 90369 | 5 | 8 | MATCH |
| 2 | 90369 | 7 | 8 | MATCH |
| 3 | 90370 | 7 | 9 | MATCH |
| 4 | 90371 | 7 | 8 | MATCH |
| 5 | 90370 | 7 | 9 | MATCH |
| 6 | 90372 | 0 | 9 | MATCH |
| 7 | 90370 | 0 | 9 | MATCH |
| 8 | 90372 | 0 | 9 | MATCH |
| 9 | 90373 | 3 | 8 | MATCH |

## Voidwalker (2064) — Hunger

| # | skill | rp after | nodes | result |
|---|-------|----------|-------|--------|
| 0 | 90380 | 3 | 6 | MATCH |
| 1 | 90381 | 5 | 8 | MATCH |
| 2 | 90382 | 10 | 8 | MATCH |
| 3 | 90383 | 10 | 7 | MATCH |
| 4 | 90384 | 0 | 9 | MATCH |
| 5 | 90381 | 2 | 8 | MATCH |

**16/16 casts identical**
