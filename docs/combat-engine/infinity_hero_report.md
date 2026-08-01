# Infinity Hero (class 2022) — golden-master report

The class is authored as pure data (`seed.INFINITY_HERO_RULES`).
Both captured AE sessions are replayed press by press and every
Attack node compared against what AE actually sent.

## infinity_hero_casts.json

- 233 captured packets -> 86 presses (auto x40, skill x46)
- **85/86 graded presses reproduced exactly** (1100 nodes compared)
- 1 known AE variance (Concealed Blade applied to nobody)

## golden_attack_fixtures.json (2nd session)

- 214 captured packets -> 74 presses (auto x29, skill x44, ult x1)
- **70/70 graded presses reproduced exactly** (951 nodes compared)
- 4 activations AE interrupted mid-cast (no resolution packet to compare)

## Harness sensitivity

Seven deliberate breakages of the rule config (wrong Aspect, missing Heroic gain, wrong effect aura, wrong arm threshold, wrong rebind icon, changed sound, resized hitbox) are each confirmed to make the replay fail — the match above is not a lax comparison.
