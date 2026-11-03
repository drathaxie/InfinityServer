# InfinityServer native UI skins

Status: published with the Border Collector achievement gate to the live loader
update and client download endpoints on 2026-10-07. HTTPS readback verified the DLL and client ZIP hashes. Unity
runtime verification is still pending. The preview is an artwork composition over
the user's actual screenshot, not a running-client capture.

Release evidence: `outputs/ui-themes-20261007/border-release/public-verification.json`.
Live promo repair evidence:
`outputs/ui-themes-20261007/border-report-check/active-db-repair.json`.
The initial promo insertion targeted PostgreSQL, while the running API and game
services use MySQL. This caused `border` to return invalid through the real modal.
On 2026-10-07 the missing code/reward pair was added to the services' active MySQL
database, after a scoped backup at
`/opt/infinity/backups/border-active-db-fix-20261007T203423Z/`.
The installed handler recognized the normalized code after repair. Existing
promo rows stayed unchanged; no player records were modified or services restarted.
Future database deployment must derive its connection from the running services,
not assume that `.pg.env` identifies production.
The existing client pack was preserved with only `InfinityLoader.dll` replaced.
The previous live DLL, checksum, and ZIP are backed up at
`/opt/infinity/backups/ui-themes-20261007T174518Z/`. Both services stayed active;
no server restart was required. The released DLL SHA256 is
`02840e95cc21711c8cfae8bfaa4661781b0103ad240526ea8759d15baab2adaa`.

## Achievement unlocks

Default is always available. DoomKnight, Chaos, and BLOD require **Border
Collector**, stored in the server-owned character achievement field `cosmetics26`,
bit index 0 (reward mask 1). The new achievement appears in Character →
Achievements. Its definition and access check are shared in `NativeUIThemeAccess.cs`.
The native dropdown shows unavailable themes as `(Locked)` and a requirement
label explains how to earn them. Both saved selection restoration and selection
events check the current character's achievement. A missing identity or locked
character uses Default. Selection is stored under
`InfinityLoader.UITheme.v2.<CharID>`; the former device-wide v1 value is ignored.

The case-insensitive promo code **border** grants Border Collector through the
existing server achievement reward system. It is active, has no global cap, and
may be redeemed once per account. The achievement is awarded to the character
identified by the existing Redeem Code flow (the client's first account
character). Redeem from the character selection screen, then use **Reload
Character** to refresh the login achievement payload. Existing achievement flags
are preserved. Other characters do not inherit the reward or a saved skin.

No server Python change or service restart was required. Deployment inserted
only the new code and reward rows and merged only its definition into the live
`data/redeem_codes.json`, preserving existing definitions. Both the previous
client release and the promo definition/database snapshot are retained in the
backup directory. Updated clients are required for the lock checks.

## Appearance and controls

The General Options scroll list gets a native **UI theme** dropdown: Default,
DoomKnight, Chaos, and BLOD. It copies the installed client's Dropdown appearance
and appends a row to its existing VerticalLayoutGroup. It clears the copied
settings callbacks and uses a separate local preference per character,
`InfinityLoader.UITheme.v2.<CharID>`.
The confirmed native Reset Settings callback restores Default.

Each theme includes three embedded PNGs: rounded menu backing, circular skill
rim with a transparent center, and a nine-slice window background. DoomKnight
uses oxblood armor, bone skull reliefs and small ember eyes; Chaos uses violet
tendrils with olive/chartreuse undersides and acid-green slit eyes; BLOD uses
warm golden wings with blue-grey steel blades and small burgundy accents.
Revision 2 follows the user's three supplied palette/motif references while
retaining the existing sprite roles. The Chaos window eyes stay in fixed corner
patches, outside the stretch bands. Assets were made with built-in imagegen.
Revision 1 is preserved in `outputs/ui-themes-20261007/art-revisions/v1/`.
Artwork and exact prompts are in
`mod/InfinityLoader/Assets/UIThemes/`.

`NativeUIThemeRules.cs` binds exact sprite names and audited UI paths. It skins
menu backing images, skill frames, main window backgrounds and the desktop chat
input. Window corners/headers and bottom-bar backings retain native artwork with
a palette tint. It excludes portrait layers and the existing portrait picker,
menu/skill icons, hover outlines, skill-use/available effects, cooldown masks,
XP/rank fills, item rarity, close-button states, dropdown templates and inner
inventory tab backgrounds.

No additional portrait overlay, HUD panels, layout offsets, hit targets or
keybinds are introduced. Themes require the existing private-server API marker.
Doorstop boot installs hooks only; Unity objects are first created from
`HUDCanvas.Start`. Newly enabled images are registered individually. The manager
checks only registered decorations each frame and disposes its own generated
sprites/textures when the HUD is destroyed.

## Sprite handling

The runtime packs generated art into the original sprite canvas and preserves
its pivot, pixels-per-unit and slice borders. Menu artwork is fitted by its
alpha>=128 visible bounds to the native backing's visible footprint, rather
than shrinking it with the generated PNG's extra transparent padding. Its native
atlas trim offset is retained in the 190x190 canvas. Soft alpha edges are retained.
Its geometry is
checked before replacement. Each original Sprite retains its own reference and
cached replacements. Default restores those references and original tint colors,
including native alpha fades. Source `overrideSprite`, Image type, RectTransform,
raycast settings, masks, materials and control callbacks are retained.

The shared window art also fits the desktop input's native slice margins. Mobile
chat uses different input sprites and is excluded pending a separate layout/art
check. Some windows use different roots or background sprites and remain native;
this implementation does not claim coverage of every historical prefab variant.

## Verification completed

- Loader Release build: zero warnings and errors.
- 108 checks: achievement access, locked saved choices, missing identity, unrelated
  founder bits, per-character preference keys; allowed decorative bindings; negative checks for portraits, icons,
  fills, cooldown/hover/effect sprites, item rarity, and dropdown templates;
  native sprite geometry; all nine art hashes and embedded resource bytes/names;
  required method existence in the installed client assemblies.
- PNG inspection: genuine RGBA transparency outside the sprites and at the
  circular skill centers. Generated PNGs are copied without creative edits;
  resampling/padding happens only when packing them into native sprite canvases.
- Installed Options prefab: `fpsDropdown` points into General Options' real
  scroll Content, which has a VerticalLayoutGroup. The picker uses that layout.
- Browser preview: theme changes and Default inspected against the exact supplied
  screenshot, plus native icon layers and separate sprite-size samples.
- Promo tests: case/whitespace normalization, repeat claims, uncapped redemption,
  achievement preservation, character isolation, and the real login payload.
  The installed server code passed these checks in an isolated PostgreSQL schema;
  that test did not verify the active MySQL database. The later MySQL repair
  verified code/reward readback and the installed handler's normalized lookup,
  stopping before any claim/player write. No real player was awarded a test reward.

Reproduce the build with `dotnet build mod/InfinityLoader/InfinityLoader.csproj -c Release`.
Run checks from the repository root with
`dotnet run --project mod/NativeUIThemeChecks/NativeUIThemeChecks.csproj -c Release`.
Set `AQWI_GAME_DIR` if the Steam client is installed elsewhere.

## Remaining client verification

After installing a staged build in a test client, verify selection and persistence,
all normal/hover/pressed/disabled states, skill use/cooldown/availability feedback,
chat editing and submission, targeting, inventory/quest/shop/options opening and
closing, scrolling and dragging, scaling/resizing, switching between all themes,
Default restoration and confirmed Reset Settings. Compare portrait frame choices
before/after. Review loader logs for `[ui-skins]` entries.
Also verify locked dropdown choices, achievement-panel status, redeeming `border`
through the native modal, Reload Character, and selection persistence per character.

Compilation, metadata checks and browser compositions do not establish gameplay,
runtime rendering, picker placement or Default restoration in Unity.
