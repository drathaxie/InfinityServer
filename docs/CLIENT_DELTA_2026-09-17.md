# Client delta audit — 2026-09-17 build

Compared the installed `Assembly-CSharp.dll` dated 2026-09-17 against the
2026-07-06 decompile (`C:\tmp\aqwi_decomp_2026-07-06`), which was the baseline
behind `docs/CLIENT_DELTA_2026-07-06.md`.

- Installed assembly SHA-256: `886D4D2FBD02500DE46C76BE1F1997748A2454C7C1569AB97B403549B59C8FCE`
- New decompile: `C:\tmp\aqwi_decomp_2026-09-17`, copied over `docs/decomp`
- Decompiled files: 1,310 new vs. 1,160 baseline
- Delta: 157 added, 7 removed, 297 changed
- Wire delta: 9 new requests (1 removed), 11 new responses, 8 changed responses
- `schema/schema.json` regenerated from this build

The whole Unity runtime shipped with this build (every `Managed/*.dll`,
`resources.assets`, `globalgamemanagers`), not just game code.

## Wire summary

New c2s requests and their parameter order:

| cmd | params |
| --- | --- |
| `applyTrait` | `ShopID, ShopItemID, CharItemID, CharPatternID` |
| `removeTrait` | `TraitItemID, CharItemID, CharPatternID` |
| `dustPattern` | `CharPatternID, LootID` (already implemented) |
| `getClassSkills` | `ItemID` |
| `getMonDrops` | `monID` |
| `getPlayerTitles` | (none — already implemented) |
| `savePlayerTitle` | `title` (already implemented) |
| `getItemSponsors` | `itemID` (already implemented) |
| `getRandomStatues` | `gen, specCount, spec…, excludeCid…` |

`removePattern` is gone as a request; `dustPattern` replaces it. The s2c
`removePattern` response still exists and is still the eviction path, so our
handler stays as is.

New s2c responses:

| cmd | fields |
| --- | --- |
| `classSkills` | `ItemID`, `skillList` (`Dictionary<int, SkillData>`) |
| `dataWarnings` | `warnings: List<string>` |
| `dust` | `bSuccess, Gained, Total, CharPatternID, message` (implemented) |
| `getItemSponsors` | `Sponsors: List<string>`, **`ItemID: int`** |
| `getMonDrops` | `monID`, `ItemDrops: List<MonDropItem>` |
| `getPlayerTitles` | `Titles: List<string>` (implemented) |
| `getRandomStatues` | `Gen, Cids: List<int>, Names: List<string>` |
| `monLeash` | `monMapID` |
| `monWalk` | `monMapID, x, y, speed` |
| `savePlayerTitle` | `CharID, Title` (implemented) |
| `statueVersion` | `cid, version` (implemented) |

Changed response shapes:

- `initPlayer` / `addItems` / `buyItem`: `houseItem` renamed to `HouseItem`,
  and a separate `classItems` bucket was added. Both already served.
- `QuestAccept` gained an optional `quest` (`QuestData`).
- `UpdatePattern` gained `silent`.
- `igai` gained `El` and `HoldMove`.
- `pickupCollect` gained `Collector`; `pickupClear` gained `PickupId?`.
- `loadShop`'s shop payload gained `lockedItems` (a list of `ShopItemID`s that
  sets `ShopItem.Locked`).
- `login` is unchanged on the wire, but the client now feeds `serverTime` into
  a new `ServerTime` clock and warns when it is absent. We already send it.

Data-model additions the server feeds:

- `Pattern` gained `Dust`, `IsDefault`, `Traits: List<TraitData>`, `MaxSlots`
  (default 3) and `SlotsUsed`. `TraitData` is `{ID, Name, Cost, Desc, Lvl}`.
- `Item` gained `MetaString` and `HouseInventory`, plus `isTrait`, `SkillID`
  (parsed from `Meta`) and `StatueCid` (parsed from `Meta`'s `cid` key).
- `iType` gained `Trait = 45`.
- `PlayerInfo` gained `IsGuest`, `Dust` and `Title`. We already send `Dust` and
  `Title`; `IsGuest` is new.

## Priority findings

### P0 — `getItemSponsors` omits `ItemID`, which disables the shipped feature

`ResponseItemSponsors` now carries `ItemID`, and `ItemPreviewNew` uses it for
everything:

```csharp
sponsorRequestsInFlight.Remove(resp.ItemID);
SessionState.ItemSponsors[resp.ItemID] = resp.Sponsors;
if (item != null && resp.ItemID == item.ID) ApplySponsors(resp.Sponsors);
```

`server/handlers/players.py` replies `{"Cmd": "getItemSponsors", "Sponsors": …}`
with no `ItemID`, so it deserializes as 0. Three consequences, all of them
silent: the sponsors are cached under item 0, the real item id is never cleared
from `sponsorRequestsInFlight` (so the panel never re-requests it for the rest
of the session), and `resp.ItemID == item.ID` fails so the Sponsors button never
appears.

Fix: echo the requested item id in the response. One line, and it restores the
whole seeded sponsorship feature against this build.

### P1 — four new requests have no handler and will hang their UI

- `getClassSkills` — `UIClassPreview` sends it whenever a class's skills are not
  already cached and waits for `classSkills` to populate the preview panel.
  We already have the skill graphs; this is a lookup-and-serialize.
- `getMonDrops` — sent from a chat/dev action against the current monster
  target. `MonDropItem` is `{itemID, Quantity, QtyMax, Rate, ItemData, Hide}`,
  which maps almost directly onto our `monster_drops` rows.
- `getRandomStatues` — `RandomStatueClient` batches up to 32 pedestals per
  request (`gen`, a spec per pedestal — `"me"`, a statue id, or `"0"` — then the
  cids already assigned, to exclude) and expects `Gen/Cids/Names` back in
  pedestal order. This is the generic form of the FounderTower statue work.
- `applyTrait` / `removeTrait` — a new gem-trait system driven from
  `PatternPreview`, buying a trait out of a shop into a gem's slot budget
  (`SlotsUsed`/`MaxSlots`) and removing it again. Nothing server-side exists.

### P2 — server-authored monster movement is now a client contract

`monWalk` walks a monster to `(x, y)` at `speed` via its walk animation with
`serverAuthored: true`, and `monLeash` puts it into leash state and sends it
back to its spawn. `NodeMonsterMove` (new) adds wall raycasts and spawn-pad
avoidance. We emit neither packet, so monsters stay where they spawn.

### P2 — `dataWarnings` is a free authoring safety net we are not using

`ResponseDataWarnings` queues a list of strings and shows them in a message box
once the next cell change finishes. That is exactly the channel for the content
problems our importers currently only print to the server log. Cheap to adopt.

## Lower priority / dormant

- `QuestAccept` may carry the full `QuestData`. The client now builds and
  registers the quest from it when it is not already cached, instead of
  throwing. Sending `QuestID` alone still works for quests the client loaded.
- `lockedItems` on `loadShop` gives server-side per-item lock state. Nothing in
  our shop data sets it, so the field is dormant.
- `pickupCollect.Collector` / `pickupClear.PickupId` refine a pickup system we
  do not emit at all.
- `RegisterNowRequest`/`RegisterNowResponse` and `UIGuestUpgrade` post to
  `https://account.aq.com/webapi/Register/Now` — AE's own account service, not
  our web API. Only relevant if we ever serve guest accounts
  (`PlayerInfo.IsGuest`).
- `Shop` now uses `TryAdd` and iterates its own list, so duplicate item ids in
  one shop payload no longer throw on the client.

## Client-only in this build

Mobile and platform work dominates: `MobileChatController`, `TouchWalkController`,
`MobileInteractButton`, `JoystickHider`, `AndroidDeviceTemperature`,
`DeviceThermalWatch`, `AndroidScreenshotShare`, `CanvasScaleCompensator`.
Also new: a keybinding system (`KeyBindings`, `UIKeyBindingsList`), screenshot
mode with poses and stickers, a time-trial system (`TimeTrialManager`,
`UITimeTrialHUD`, `AS_StartTimeTrial`, `If_TimeTrial`), a large action-script
node set (`AS_*`, `If_*`, `ActionScriptIf`, `ENDIF`), quick replies, toasts,
fake light probes and post-processing. None of these create a server contract
today, though the time-trial and action-script nodes are authoring surfaces we
may want to feed later.

## Verification

- `dotnet build mod/InfinityLoader/InfinityLoader.csproj -c Release`: passed,
  0 warnings, 0 errors.
- All 27 Harmony patch targets in `mod/InfinityLoader` still exist in this build
  **with identical signatures**, including the 16 whose declaring types changed
  (`AEC`, `Player`, `DynamicStatue`, `Apop`, `NPCLoader`, `Entity`, …). No mod
  work is required for this update.
- `python -m pytest server -p no:cacheprovider -q` with `TEMP`/`TMP` redirected
  to `C:\tmp`: 67 passed, 3 errors. The three errors are pre-existing collection
  failures in `test_cutover.py`/`test_infinity_hero.py` (helpers taking a `cfg`
  argument are collected as tests and report a missing fixture); they are
  unrelated to this build.

## Implemented follow-up (2026-09-18)

Every command above is now answered.

- `getItemSponsors` echoes `ItemID`. A live-AE capture confirms the exact shape:
  `{"Cmd":"getItemSponsors","Sponsors":["Xaxn:true"],"ItemID":100180}`.
- `getClassSkills` resolves the class-armor item to its class and replies with
  the same `skillList` shape `sEAct` and `initPlayer.classSkills` already use.
  Unmatched class armor answers with an empty bar rather than staying silent, so
  the preview panel settles instead of spinning.
- `getMonDrops` serves `loot.drop_table()`, which is deliberately the same two
  sources `roll_drops()` rolls (the monster's authored rows plus the global
  table), so the panel shows what can really drop.
- `getRandomStatues` resolves a batch of pedestals positionally in `statues
  .assign_random()`: `"me"` is the viewer, a numeric spec is that character, and
  anything else is a server pick that avoids the cids already on screen. A
  pedestal pinned to a specific hero still shows it even when another pedestal
  already does — the client dedupes only its own random picks.
- `applyTrait` / `removeTrait` spend and free a gem's slot budget
  (`patterns.apply_trait` / `remove_trait`). The server re-checks the duplicate
  and slot-budget rules the client checks, because that check is all that stands
  between a crafted packet and an over-stuffed gem. A gear gem answers with
  `UpdatePattern`; a loose bag gem answers with `addItems.patternItems`, because
  `Inventory.UpdatePattern` resolves `ItemID` against gear and bank only and
  would silently drop a bag gem. No trait content is authored, and `shop_items`
  has no token-price columns, so a priced trait still needs that schema.
- `monWalk` / `monLeash` are driven from the AI loop by `monstermove`: an
  engaged monster walks toward its target on an interval, stopping at melee
  range and refusing to be kited past its leash, and anything displaced but no
  longer engaged is walked home. Leashing is done by reconciling against the
  live engagement set, so every way aggro can end is covered without each path
  remembering to leash. `NoMove` placements never walk. Idle pad wandering is
  NOT implemented — it needs the authored pad extents.
- `dataWarnings` reports, to staff only, placements pointing at a monster or
  apop that does not exist, and a map we had to substitute.

Still open, and unchanged by this work: `QuestAccept` still sends only
`QuestID`, `lockedItems` is still unset, and pickups are still not emitted.

## Recommended order

1. ~~Echo `ItemID` in `getItemSponsors`~~ — done.
2. ~~Implement `getClassSkills` and `getMonDrops`~~ — done.
3. ~~Implement `getRandomStatues`~~ — done.
4. ~~Adopt `dataWarnings`~~ — done.
5. Author trait content, which needs token-price columns on `shop_items` first.
6. ~~Decide whether monsters should walk~~ — they chase and leash; wandering is
   still open.
