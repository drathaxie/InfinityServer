# Client delta audit — 2026-07-06 build

Compared the installed `Assembly-CSharp.dll` dated 2026-07-06 against the
repository's 2026-06-13 `docs/decomp` baseline.

- Installed assembly SHA-256: `E35F39165C9643D10FAD1DECBB57E02C52C18AAFBC01E2DC25BFE3AE48E6731F`
- New decompile: `C:\tmp\aqwi_decomp_2026-07-06`
- Decompiled files: 1,342 new vs. 1,309 baseline
- Delta: 39 added, 6 removed, 121 changed
- Wire delta: 2 new requests and 3 new responses

## Priority findings

### P0 — `rewardPlayer` monster identity is stale

The new client changed `ResponseRewardPlayer.monID` from `string` to `int` and
added `monMapID`. It uses `monMapID` to find the dead monster and place gold,
loot, and XP popups at the corpse (and to defer the popups until its death
animation reaches the right state).

Our `loot.reward_packet()` still:

- emits `monID` as a string;
- puts the map-instance id in `monID`, not the catalog monster id; and
- omits `monMapID` entirely.

Impact: the packet still awards currency/items, but the new corpse-aware reward
path cannot resolve the monster, so drops and popups fall back to the player.

Fix direction: pass both values from `_handle_kills`: catalog id from
`combat.monster_identity(area, target)` and instance id from the `m:<id>` target
string. Emit both as integers. Add assertions to `test_loot.py` for their type
and values.

### P1 — statue generation is acknowledged but not implemented

The client moved statue generation from the old HTTP `SaveStatue` payload to a
TCP `generateStatue` request/response with a server-controlled cooldown. Our
handler prevents the UI from hanging, but always returns `Success: false`.

Impact: the new button behaves cleanly but the feature is unavailable.

Fix direction: decide whether Infinity should generate a persistent house item,
an asset/layout record, or an intentionally local substitute. Then enforce
eligibility and cooldown server-side and return the generated `ItemID`.

### P1 — the new cache invalidation response is never emitted

`ResponseCacheReloaded` clears and reloads `apop`, `dialog`, or quest caches.
The client also removed its local `/reloadapops` implementation. The server and
web editor save paths never send `cacheReloaded`.

Impact: edits can remain stale in already-connected clients until another
reload path (usually a map change or reconnect) happens.

Fix direction: bridge successful apop/dialog/quest editor saves to the game
server and broadcast `{"Cmd":"cacheReloaded","rType":"apop|dialog|quest"}`
to affected staff sessions (or all sessions if targeted delivery is not worth
the complexity).

### P1 — `changeColor` trusts an arbitrary hair id

The new request always includes a seventh `HairID`, and the response now
dereferences `hairBundle.Name` without a null check. The current server persists
any integer hair id. If it is not in the hair catalog, the response omits the
bundle and the client can throw while applying the update.

Fix direction: validate the requested hair id against the catalog and the
character's gender before persisting or broadcasting it. Always include a
non-null `hairBundle` in a successful response.

## Content-authoring and latent compatibility gaps

These do not affect the current authored content, but the new client can consume
them and our mining/rendering pipeline cannot yet produce them faithfully.

- `AnimationCancel` is a new combat node. `extract_skill_graphs.py` drops it and
  `combat._render_node()` skips it as unknown.
- `SpellAnimation` gained `METEOR` plus `X`, `Y`, `Ease`, and `ProjSpeed`.
  The extractor preserves only `Animation`/`Speed`, and the server renderer has
  no `SpellAnimation` branch at all.
- `Particle` gained `Lifetime`; both extraction and rendering currently drop it.
- `sAct.skillList[*].autoHoldAtRange` is new. The skills schema/database and
  `forge.build_seact()` do not store or emit it, so it defaults to `false`.
- `loadShop` can now carry a second filter parameter. The handler ignores it.
  All currently authored `ItemShop` buttons found in the repo use filter `0`, so
  this is dormant today.
- House inventory/placement records gained `Meta`. The server preserves unknown
  placement JSON, but `_house_item_wire()` does not emit catalog `Meta`. No
  current item data contains that field, so this is also dormant.
- `schema/schema.json` is still generated from the June decompile and therefore
  lacks the new commands/fields and still describes `rewardPlayer.monID` as a
  string.

## Already compatible

- `savePortrait` / `portraitChange`, login `PortraitPreference`, world
  `portraitPref`, and `ownedPortraitFrames` are implemented.
- The seven-parameter `changeColor` request and its hair bundle response are
  implemented for valid catalog hair ids.
- The revised `loadHairShop` shape is compatible with the current response.
- `generateStatue` receives a well-formed failure response instead of hanging.
- InfinityLoader builds against the installed client with zero warnings/errors.

## Client-only or not actionable yet

- Most of the build is gamepad navigation/input work; it creates no new server
  contract.
- The achievements framework currently updates Steam/local state only. Its
  server notification method explicitly logs that it is not implemented, and
  no achievement request exists yet.
- Nameplate rendering, controller selection, reward visuals, VFX scaling, and
  other UI changes are client-internal unless corresponding authored content is
  introduced.

## Verification

- `dotnet build mod/InfinityLoader/InfinityLoader.csproj -c Release`: passed,
  0 warnings, 0 errors.
- `python -m pytest server -p no:cacheprovider -q` with `TEMP`/`TMP` redirected
  to `C:\tmp`: 25 passed.

## Recommended order

1. Repair and test the `rewardPlayer` identity fields.
2. Validate `changeColor` hair ids and add new-client contract tests.
3. Add editor-to-game `cacheReloaded` notifications.
4. Decide whether to implement real statue generation.
5. Extend schema, skill storage/mining, and node rendering before importing
   content that uses the dormant fields/nodes.

## Implemented follow-up (2026-07-13)

All actionable server-contract findings above are now implemented:

- rewardPlayer emits integer catalog monID plus integer monMapID.
- generateStatue creates or refreshes one Infinity Hero Statue floor item,
  preserves its per-instance Meta, snapshots the player's appearance, enforces
  founder eligibility and a five-minute cooldown, and exposes local PNG art at
  statue/<cid>.png. InfinityLoader redirects the prefab's hardcoded CDN load to
  that endpoint while the Infinity API marker is active.
- editor saves publish DB cache revisions and the game process emits
  cacheReloaded for apops, dialogs, and quests.
- the full harvested hair catalog is seeded; invalid hair ids are repaired and a
  successful changeColor always includes the new client's hairBundle field.
- shop filtering, house-item Meta, AnimationCancel, extended SpellAnimation,
  particle Lifetime, and autoHoldAtRange are supported.
- schema/schema.json was regenerated from the July 6 client.
- the dev-shop economy test now selects an item the starter character does not
  already own.

