# September review repairs

This tracks implementation and deployment of [the September 9 review](CODE_REVIEW_2026-09-09.md).
Production's affected runtime files matched the reviewed source before changes. Read-only
checks found no duplicate redemption groups, negative currency balances, or negative buyback stock.

## Repair coverage

| Review | Implemented change | Verification |
|---|---|---|
| R01 | Gate Unity content writes with current staff role; attach existing session bearer in loader | Actual anonymous/player/staff HTTP requests, including mixed-case path and demotion |
| R02 | Shared development-shop gate; canonical purchase price; membership/stock checks and conditional debit | Purchase/listing tests and competing requests for final stock |
| R03 | Serialize code quotas and enforce unique normalized account/code claims | Eight same-account claims and six accounts competing for two uses |
| R04 | Reward item grants participate in the enclosing redemption transaction | Invalid later reward rolls back currency, inventory, claim and history |
| R05 | Conditional stock claim/debit; full rollback if grant fails | Concurrent buybacks plus injected grant failure |
| R06 | Persisted ID-based roles; reserved legacy usernames cannot register/rename or promote accounts | Registration, rename, password authentication and login tests |
| R07 | Frame/time/connection/command limits; lazy DB allocation; typed envelopes; bounded HTTP threads | Real loopback TCP peers with oversized, malformed and idle traffic |
| R08 | Private input requests; caster/area/slot/node/expiry checks before consuming context | Wrong-owner/area/node/slot requests and separate owner/bystander writers |
| R09 | Validate registered living targets and current area/frame; clear streams/throttles during cancellation | Unknown targets, other cells/maps, cancellation and disconnect |
| R10 | Recorded death and ten-second eligibility gate; dead players cannot rest-heal | Living, newly dead, eligible and repeated revive tests |
| R11 | Browser revocation generation and logout revocation; current role checks; game token expiry and polling | Password/demotion/logout, active socket revocation and normal login |
| R12 | Complete manifest, read-only plan, empty-destination requirement, transactional copy and content verification | Real SQLite-to-PostgreSQL copy; occupied-target refusal; all identity sequences |
| R13 | Area-scoped monster aura cleanup | Identical monster IDs across two instances |
| R14 | One authenticated identity per socket; reconnect clears prior combat state | Repeated login followed by disconnect leaves no old membership |
| R15 | Membership flags derive from expiration; support grants extend it; preserve legacy grants once | Grant, expiration and existing membership fixtures |
| R16 | Script tests collected once; native pytest modules collected normally; reports written outside tracked docs | Normal pytest discovery passes; PostgreSQL CI job added |
| R17 | Background update check; helper waits for exact game process then verifies/replaces with previous build retained | Clean build and a real 35-second fixture process; target unchanged after 32 seconds |

Additional shared fixes: atomic inventory ID allocation, PostgreSQL rollback on context failure,
request transaction boundaries, unique process-owned PostgreSQL test schemas, redacted unknown
request logs, and double-precision timestamps in newly created PostgreSQL schemas.

Historical duplicate redemption history is preserved. A separate unique claim table prevents
further duplicate grants, instead of deleting ledger evidence to create a unique history index.

## Validation and release evidence

Evidence is retained under `.tmp-tests/code-review-2026-09-09/`: regression logs, baseline and
production hashes, `release-manifest.json`, reproducible updater fixture, staged PostgreSQL runner,
and the backup/deployment script. No credentials are written to these local evidence artifacts.

The client package preserves every existing entry except the rebuilt `InfinityLoader.dll`.
The DLL and its published SHA-256 are updated together. Existing clients must restart to load it.
The session schema change requires a fresh sign-in for old browser/game tokens.

Local SQLite regression coverage and isolated PostgreSQL tests do not establish Android or Unity
gameplay/visual acceptance. The updater fixture proves process waiting and replacement against
disposable files, rather than a running Unity assembly. Those client boundaries remain explicit.

Deployment status: all 17 reviewed repairs are installed and verified in production. The separate
browser-editor feature release is also installed. Both services are active/running with
`NRestarts=0`; the post-feature-release journal contained no Python tracebacks or unhandled
request exceptions at verification time. This is the reviewed issue set, not a guarantee that
every possible defect has been eliminated.

| Check | Result |
|---|---|
| Full local SQLite suite | 67 passed, 1 PostgreSQL-only test skipped |
| Installed PostgreSQL regression, migration and editor tests | 30 passed, 1 SQLite-only test skipped; random disposable schemas |
| Installed release files | 30 SHA-256 matches across the combined repair/editor manifests |
| Live HTTP | Base classes and published DLL/hash return 200; anonymous native authoring returns 401 |
| Public HTTPS editor routes | Both pages redirect to staff login; local API list/save routes reject anonymous requests with 401 |
| Loader build | Release build: zero warnings/errors; published SHA-256 `7aaf1772a55f9e69cdaf800f52feeb1cbbfc9d6bccd58dacb87691629edaeef5` |
| Browser QA | Both editors created, saved and reopened disposable content; invalid rewards displayed an error and preserved edits; desktop layout inspected |
| Backup restoration | Actual production dump restored successfully into a new UUID database; all 44 tables inspected; temporary database removed afterward |

Protected production backups:

- Repair release: `/opt/infinity/backups/review_repairs_20260909_032818`.
- Editor release and rehearsed dump: `/opt/infinity/backups/browser_editors_20260909_135321`.

The editor release restarted only the API. Its seven files were checked against the installed
baseline before replacement; no production content was created or edited by browser QA.
Restoration ran against a separate temporary database, never the live database.
Hosted CI and Unity/Android gameplay/visual behavior remain outside this verification.

## Backlog direction

The cutscene and redeem-code browser editors are now complete and reachable through the shared
editor menu:

- [Cutscene editor](https://130-162-189-229.sslip.io/cutscene/Edit.aspx): search, load, create, edit,
  NPC/bundle pickers and save; unknown command strings and envelope fields round-trip unchanged.
- [Redeem-code editor](https://130-162-189-229.sslip.io/redeem/Edit.aspx): search, create, edit,
  item lookup, usage limits, activation and multi-reward bundles.

Both use current staff-role checks and CSRF tokens for writes. Version conflicts prevent a stale
browser tab from overwriting a later save. Saves record the authenticated staff identity. Cutscene
IDs are allocated under a database lock shared with the native editor and update the game's
dialog cache revision. Redeem validation also applies to the existing support workflow and
preserves mixed-case legacy code identities.

Next acceptance work, in priority order:

1. Sky-blade client visuals and a focused in-game smoke pass of repaired combat/session behavior.
2. Exact class-mechanics matrix: Prepared Strike, authoritative Mage class ID, remaining
   Healer/Rogue formulas and interactions, with source/capture evidence before balance changes.
3. Android is owner-confirmed working on Samsung S23 (September 10). New-feature and updater
   acceptance remains specific to each change; macOS packaging is pending.
4. Per-monster respawn tuning, structured operational metrics, remaining identifier allocation,
   and incremental combat-state extraction behind the regression suite.
5. Redacted secret/history and public-artifact review; no full-history scan has been claimed.

Custom guild tags remain retired; native titles are preserved. The original review is a dated
pre-repair snapshot; this report is the current disposition of its findings and first backlog batch.

## September 10: Battleon Briefing Vol. 8 intake

[AE's September 8 briefing](https://aq2d.com/quick-posts/battleon-briefing-vol-8/)
describes an upcoming client build after substantial UI/asset cleanup. At publication,
Monstrous Transformation was in testing; the group cheer and Forge SpellStones were in
development for Legendary backers. SpellStones are described as unavailable in combat.
Pirate Class is upcoming and Ninja is teased. Shop sorting, Sanctuary/statue improvements,
Aranx birthday content and Swordhaven invasion work are also mentioned. The post is an
announcement, not evidence that these features are released or imported here.

Repository comparison and next steps:

| Work | Verified local foundation | Acceptance needed |
|---|---|---|
| New-client compatibility | Existing loader patches, editor authentication, updater and protocol tests | Compare the released build's assemblies, renamed assets, UI hooks and packet shapes before deploying compatibility changes |
| Titles | `handlers/players.py` handles native list/save packets; `titles.py` supplies the local title catalog | Check new picker behavior and official entitlement/catalog differences; preserve native titles and retired guild-tag behavior |
| Founder SpellStones | Random 19-appearance transformation pool and native room cheer deployed September 11; both available in Redux's inventory | New-effect client acceptance pending; Forge shop lifetime/access rules remain deferred |
| Pirate / Ninja | Generic class and Skill Forge machinery; no dedicated Pirate/Ninja implementation identified in this source pass | Require released class IDs, authored graphs, tooltips and captures; do not invent mechanics from the announcement |
| Shop sorting and statues | Existing shop packets and local statue capture/upload pipeline | Compare released client behavior and output visually before changing server contracts or applying a new statue conversion |
| New event/story content | Catalog import and cutscene tooling | Import only verified item/shop/quest/map/cutscene deltas when available |

This intake adds feature candidates and a compatibility gate to the backlog. It does not
change production behavior, grant founder rewards, or establish new client gameplay parity.

## September 11: Spellstone follow-up

Configurable random transformation and room cheer are implemented. The owner selected all
19 distinct catalogued Frogzard appearances for the starting list. See SPELLSTONES.md for
configuration, limitations and current deployment status. Forge remains deferred.

## Gem catalog and balance follow-up (local)

The catalog expands from five tokens to 52 family/slot combinations. V2 replaces independent
stat rolls with level/slot/rarity budgets, makes Power equal spent stat points, guarantees
favored-stat rarity progression, and limits the former 31-to-230 weapon Base jump. New loot
and purchases use the model; free Power Up scales with owner level, preserves applied gems,
and produces no dust when its starter gem is displaced. See GEM_CATALOG.md and GEM_BALANCE.md.
Production rollout and the read-only plan for normalizing existing owned gems require the
pending live comparison/approval. Neither catalog nor balance changes are deployed yet.
