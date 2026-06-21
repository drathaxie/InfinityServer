# AQW Infinity — Private Server User Manual

A self-hosted emulator of **AQW Infinity** (Artix's Unity remake of AdventureQuest
Worlds), reverse-engineered from the client + a live packet capture. The modded game
client talks to our server; the DB is the authoritative, live store.

> Research / game-preservation project. Run it against your own client.

---

## 1. Getting in

1. Install **AdventureQuest Worlds Unity Playtest** from Steam.
2. Grab **`InfinityServer-Client.zip`**, fully close the game, and copy its contents
   into the game folder (`…\steamapps\common\AdventureQuest Worlds Unity Playtest\`),
   keeping the structure. See the zip's `INSTALL.txt` for details.
3. Launch, pick any server.

**Accounts are open-registration.** Type a username + password on the login screen —
your first login creates the account; the same password is required afterward. There's
no email/recovery, so don't reuse a real password.

**The server:** API `https://130-162-189-229.sslip.io/` (HTTPS, via Caddy), game socket
`130.162.189.229:5588` (raw TCP). The client learns the game address from the API at
login, so you only configure the API marker.

**To return to live AE:** delete `UserData/infinity_api.txt` (or set `enabled=false` in
`doorstop_config.ini`). The mod's packet logger is always on either way, writing
`UserData/Beyond/packets.jsonl` — that's how we capture new content.

---

## 2. What works today

Everything below is served **live from the DB, per character** (no replayed capture):

| System | Status | Notes |
|---|---|---|
| Login / accounts | ✅ | open registration, hashed passwords, session-token gated |
| Character create / customize | ✅ | identity, colors, gender, stats from your own record |
| Maps / movement / cells | ✅ | 14 maps; walk, change cells, see other players |
| Combat | ⚠️ works | classes/skills/gems functional; damage-math *fidelity* still being tuned |
| Classes | ✅ | Warrior, Mage, Rogue, Healer, Dragonslayer; equip to swap skills |
| Skill Forge | ✅ | author skills (DB-driven, round-trips); branching is a known gap |
| Shops | ✅ (partial data) | 10 shops live; some still need capturing — see §3 |
| Buy / sell | ✅ | gold + AC (coins); class items are non-sellable |
| Inventory | ✅ | 1272-item catalog; equip/unequip, gems (patterns) |
| Bank | ⚠️ read-only | opens and shows banked items; deposit/withdraw not wired yet |
| Quests | ⚠️ partial | clean per-character state, NPCs offer quests; **progress tracking not built** |
| Cutscenes (Dialogger) | ✅ (partial data) | play with assets streamed from AE's CDN; more need capturing |
| NPCs / dialog (apops) | ✅ | dialog/menu trees, live-editable |

---

## 3. Shops

Shops are served from the normalized catalog (`shops` / `shop_items` / `items`). Unknown
shops open as an **honest empty window** (they never masquerade as another shop).

**Live with real content:** Gravelyn's Infinity (2468), and the Founder shops — Backer
(2688), Founder (2689), Epic Founder (2690), Underworld Founder (2691), Day-1 Backer
(2687), World Record Breaker (2704), Eternal Thanks (2705).

**Dev "Check All" shop (2722):** stocked with the **entire catalog (1272 items), free**.
Use it to grab any item for testing (Gravelyn → "DEV Check Everything shop"). It may take
a second to open (large), and re-stocks as the catalog grows.

**Still empty (need capture):** Class Shop, DragonSlayer Crafting, Bludrut Blade/Gear,
Infinity Houses, Joke Shop, the higher Founder tiers, designer/stranger shops (17 total).

---

## 4. Quests

A fresh character starts **clean** — nothing accepted or tracked (no inherited progress).
NPCs offer quests and the quest catalog (143 quests) serves live. **What's not built yet:**
accepting a quest, objective counting (e.g. kill 5 Wyverns → `qobjective`), and turn-ins.
So quests display but don't yet *advance*. This is the next major feature.

---

## 5. Cutscenes

Two systems:
- **Dialogger cutscenes** (`getDialog`) — talking-head/scripted scenes. We serve cutscenes
  **1, 28, 70** from the DB; assets (backgrounds, prefabs, music) stream from AE's CDN. The
  web API resolves the right CDN version per asset bundle automatically (it probes the live
  CDN — AE's version metadata is unreliable). Music tracks proxy from AE too.
- **`getCutscene` (CellData) scenes** — triggered by apop "OpenCutscene" buttons. **Not
  handled yet**; needs a captured payload to model.

More cutscenes arrive as we capture them (open them in live AE with the logger on, then
import — see §7).

---

## 6. Dev / authoring tools

Granted to accounts with **access level 50**. Ask the admin to add your username to the
server's dev list (`data/dev_users.txt`), then re-log. Unlocks:

- **Dialogger** — the in-game cutscene editor (save/load cutscenes to the DB live).
- **Apop / NPC editor** — create/edit NPC dialog trees (the "+" button, editor panels).
- **Charedit** and `/devon`, `/cutscene <id>`, and other staff commands.

Edits write straight to the live DB (AE-style), so they persist and are visible immediately.

---

## 7. Admin / operations

The server runs on an OCI VM (`130.162.189.229`) as systemd services `infinity-game`
(TCP 5588) and `infinity-api` (HTTP 8182, behind Caddy HTTPS), backed by Postgres.

**Deploy a code change:**
```
scp server/*.py ubuntu@130.162.189.229:/opt/infinity/server/
ssh ubuntu@130.162.189.229 'sudo systemctl restart infinity-game'   # or infinity-api
```

**Import captured content into the live DB** (capture against live AE first, with the mod's
logger writing `packets.jsonl`):
```
python capture/import_shops.py      <packets.jsonl>   # shops + their items
python capture/import_bank_items.py <packets.jsonl>   # item definitions from a bank
python capture/import_cutscenes.py  <packets.jsonl>   # getDialog cutscenes
```
Run these on the VM with `.pg.env` sourced so they target Postgres. All are non-destructive
(`ON CONFLICT DO NOTHING`).

**Persist the live catalog to source files** (so a rebuild reproduces it):
```
python server/export_catalog.py     # -> data/items.json + data/shops.json
```

**Re-stock the dev shop** after importing new items:
```
python server/fill_dev_shop.py
```

**Grant dev access:** add a username (one per line) to `data/dev_users.txt`; they re-log.

**Rebuild a DB from source:** `python -c "import seed; seed.run()"` — idempotent; seeds the
catalog from `data/` (items, shops, quests, apops, maps, classes, cutscenes, defaultclasses).

---

## 8. Known limitations / roadmap

- **Quest progress tracking** — accept / objective-count / turn-in (next big feature).
- **Bank mutation** — deposit / withdraw / swap (read-only today).
- **`getCutscene` (CellData) scenes** — handler + data not built.
- **Social / houses** — friends, party, guild, housing not implemented.
- **Combat fidelity** — functional but damage math / per-skill effects still being tuned to
  match captured AE behavior; Forge branching is unbuilt.
- **Content gaps** — 17 shops and most cutscenes still need capturing from live AE.
- **initPlayer** — a couple of cosmetic fields (hair bundle, ExpToLevel) still derive from
  the capture template; identity/privacy fields are fully scrubbed.

Content gaps close by capturing against live AE and importing (§7) — no code needed.
