# Authoring content (NPCs, dialog, cutscenes, skills, items)

InfinityServer self-hosts everything AE keeps staff-side. Two ways to author:

- **In-game tools** — the client's own staff editors (SkillForge, Dialogger, apop editor, Map
  editor). These talk to our server, which persists straight into Postgres. This is the path AE
  staff use; it's WYSIWYG and validates for you.
- **By hand in the DB** — for bulk/catalog data (items, monsters, shops, quests) it's faster to
  `INSERT`/`UPDATE` directly. See [DB_ACCESS.md](DB_ACCESS.md).

---

## Prerequisite: a dev account

The in-game editors are gated behind staff access level. Add the account's **username** (lowercase)
to [`data/dev_users.txt`](../data/dev_users.txt); on next login the server bumps it to
`access_level = 100` (the top tier — `DEV_ACCESS_LEVEL` in `server/game.py`). That single level
unlocks every authoring entry point below.

> Access is sent to the client at login, so **relog after a bump** (or after editing the DB level
> directly). The forge button in particular checks `hasAccess(100)` — at 50 it silently won't open.

To set it immediately without waiting for the allowlist:
`./scripts/db.ps1 "UPDATE characters SET access_level=100 WHERE lower(name)='redux'"` then relog.

---

## In-game tools (what each unlocks)

| Tool | How to open | Persists to | Server cmds |
|------|-------------|-------------|-------------|
| **SkillForge** | mini-menu → Forge button (needs access 100) | `classes`, `skills`, `class_skills` | `sfInit` (load) · `sfNew/sfNewLib/sfEdit/sfSave/sfClone/sfLink/sfDel` |
| **Dialogger** (dialog & cutscene editor) | `/dialog` or `/cutscene` in chat | `cutscenes` | DialoggerSave/Load (via `tweak`) |
| **Apop editor** (NPC popups) | apop editor UI / `/dapop` to delete | `apops` | `getApop` (serve) |
| **Map editor** | DevConsole | `maps`, `map_pads`, `pad_npcs`, `map_state` | map editor cmds |
| **charedit** | `/charedit` | `characters` | — |
| dev toggles | `/devon`, `/devoff`, `/devmode`, `/noclip`, `/scan` | — | — |

The forge round-trips through [`server/forge.py`](../server/forge.py): `build_init()` sends the node
palette + every class/skill (`sfInit`), and `MUTATIONS` (`sfNew`…`sfDel`) persist edits. Skills are
stored as their node-graph (`Data`) plus editor layout (`ForgeData`) — exactly AE's Class/Skill shape.

---

## Apops (NPC dialog popups) — the most common thing you'll author

An **apop** is the popup an NPC shows when clicked: panels, text bubbles, buttons (open a shop, accept
a quest, play a cutscene, etc.). One row per apop in the `apops` table:

```
apop_id  INTEGER PRIMARY KEY   -- referenced by NPC buttons / pad_npcs
name     TEXT
raw      TEXT (JSON)           -- the full Dialogger apop definition the client renders
```

`raw` is the JSON the client's apop renderer consumes — top-level `ID`, `name`, `startingPanels`,
and a `panels[]` array, each panel holding `elements[]` (Bubble text, Buttons, images…). Example
shape (truncated, apop 1 "Gravelyn"):

```json
{ "ID":1, "name":"Gravelyn", "startingPanels":[2],
  "panels":[ { "PID":2, "name":"Gravelyn",
    "elements":[ { "ID":101, "type":"Bubble", "color":"#000000", ... } ] } ] }
```

The server serves apops verbatim from `apops.raw` when the client sends `getApop`
(see the `getApop` handler in `server/server.py`). **Ghost-NPC gotcha:** the client dereferences an
apop *before* a null check, so every NPC button id referenced in a map must have a row — even if
it's a stub. Unknown ids are served as an empty stub by `load_apops` so the client doesn't crash.

### Authoring an apop
1. **In-game (recommended):** open the apop editor on a dev account, build panels/buttons visually,
   save — it writes `apops.raw`. Buttons can link a shop id, quest id, or an `OpenCutscene` id.
2. **By hand:** craft the JSON (copy an existing `raw` as a template, change `ID`/`name`/text), then
   `INSERT INTO apops (apop_id,name,raw) VALUES (999,'My NPC','{...}')`. Inspect an existing one:
   `./scripts/db.ps1 "SELECT raw FROM apops WHERE apop_id=1"`.

---

## Cutscenes

Dialogger cutscenes live in the `cutscenes` table (`id`, `raw` = the Dialogger_Data JSON). Author
them with the Dialogger editor (`/cutscene`); apop buttons of type `OpenCutscene` reference them by
`id`. They're served self-hosted the same way apops are — no staff/CDN auth needed.

---

## Catalog data (items, monsters, shops, quests) — do it in SQL

These are plain catalog rows; the in-game editors don't cover them. Pattern (see
[DB_ACCESS.md](DB_ACCESS.md) for the tooling):

```powershell
# new shop item
./scripts/db.ps1 "INSERT INTO shop_items (shop_id,item_id,sort) VALUES (5,4771,0)"

# describe a table before editing
./scripts/db.ps1 "\d items"
```

`items`, `monsters`, `maps`, `quests` (+ `quest_turnins`, `quest_rewards`), `shops`/`shop_items`,
`classes`/`skills`/`class_skills` are the catalog. Per-character state (`char_items`, `char_quests`,
…) references the catalog by id — never edit per-character rows to add *content*, add it to the
catalog and let characters reference it. The full model is in [SCHEMA.md](SCHEMA.md).
