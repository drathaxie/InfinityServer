# Spellstones

Transformation item **200023** retains existing ownership and chooses uniformly from
`data/spellstone_transformations.json`. The starting list contains 19 distinct catalogued
Frogzard appearances, including seasonal, Chinzard and cat variants. Duplicate encounters
using the same prefab are represented once. Zardmen and Zardhunter are separate creatures.

Add an entry such as `{"monster_id": 314, "name": "Frogzard"}` to the `monsters` array.
The ID resolves the live catalog's prefab, bundle and scale; name is a human-readable label.
An optional `"scale": 0.8` overrides the catalog scale. The file is read on each cast, so
an atomic replacement takes effect without a restart. Keep 1–200 entries with distinct
bundle/prefab pairs. Invalid configuration rejects the cast without spending its cooldown.
The deployed file is `/opt/infinity/data/spellstone_transformations.json`; editing the local
copy alone does not update production.

The effect lasts 30 seconds and everyone receives the same selected native transformation
packet. Repeats are possible. The server removes the transformation on aura expiry.

Cheer item **200025** makes living, out-of-combat heroes in the same map instance and cell
say **Battle on!** using native zone speech bubbles. It has a shared room cooldown to prevent
multiple owners from spamming it. The deployed version sends speech bubbles and the
requested native `/cheer` emote together:
the installed client's level0 emote table maps `/cheer` to `Em_Cheer`; each eligible participant
receives a native `emotea` packet alongside the bubble. Six focused tests pass, including
same-room scope and excluding fighting players. No combat effects are added.

Both stones require ownership and sixth-slot equipment, have a 30-second cooldown, and
cannot be cast while defeated or in combat. They do not spend class resource or acquire
a selected monster as a combat target. The staff-only Dev Check All shop is the test source;
no founder entitlements or player inventory grants are added by this feature.

Forge remains deferred: it requires a temporary interactive shop with defined inventory,
eligibility, lifetime and cleanup rules.

The owner confirmed Android working on a Samsung S23 on September 10, 2026. This does not
constitute a visual acceptance test of these newly added effects. Catalog metadata and
packet tests cannot prove that every variant renders in every installed client build.

Validation (September 11): full local suite **74 passed, 1 skipped**; focused static checks
passed. Read-only production catalog verification resolved all 19 selected appearances.

Deployment status: **live**, September 11, 2026 at 06:30 UTC, following explicit owner approval.
Seven installed file hashes match. PostgreSQL checks passed both before deployment and from
the installed path: **36 passed, 1 skipped** each, using disposable schemas. Both services are
active/running with zero automatic restarts; API returned 200, anonymous editor access was
rejected with 401, game TCP accepted connections, and no traceback or AI tick error appeared
in the post-release journal. Database/file backup:
`/opt/infinity/backups/spellstones_20260911_063029` (database dump 14,967,644 bytes).

At the owner's request, Redux's existing transformation stone was preserved and the missing
cheer stone was granted through the canonical inventory helper with an audit record. Verified
inventory: item 200023 quantity 1, item 200025 quantity 1, both unbanked. Reconnect to refresh
the client inventory, then equip and test each stone in the sixth slot. Visual acceptance of
all 19 appearances and the group cheer remains an in-client test.

Both stones were added to **Underworld Founder, shop 2691**, on September 11 at 06:38 UTC.
They match its existing free price (cost 0, coins 1) and unlimited stock. Shop count increased
from 50 to 52; all other rows and shop metadata were preserved. The live and local seed JSON
include both additions. Backup: `/opt/infinity/backups/founder_spellstones_20260911_063813`.

The two-file emote follow-up was deployed with explicit owner approval on September 11 at
06:42 UTC. Both installed hashes match; all six focused PostgreSQL tests passed before and
after installation in disposable schemas. Both services remain active with zero automatic
restarts, no traceback or AI tick errors, and healthy API/game listeners.
Backup: `/opt/infinity/backups/cheer_emote_20260911_064219`.
In-client visual timing remains for the owner to verify using Redux's stone.
