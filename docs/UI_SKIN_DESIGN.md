# UI skins: native client design plan

Status: native-asset audit, redesigned artwork and local implementation complete;
Unity runtime verification pending. The earlier generic border prototype is rejected,
archived under `outputs/ui-themes-20261007/rejected-prototype/`, and disconnected from the loader.
It was never deployed. Its geometry tests were not evidence of a suitable game UI design.

## Existing portrait system

The top-left portrait already has a layered frame system: `NameplatePortraitFixer`,
`NameplatePortraitFixerData.FindByFrame`, and the existing frame picker. InfinityLoader
already supplies custom frames through `FindByFrame_Postfix` and `ApplyPortrait_Postfix`.
Portrait ring, plate, background, and level circle are separate sprites.

Do not add a second border around `UIPlayerPanel`, or turn the portrait into a rectangular
card. Any new portrait artwork must use the existing layer/frame pipeline. Preserve existing
frame choices and server-granted frame IDs. Portrait changes are a separate design decision
from skinning the remaining HUD.

## Work from the real shapes

| Surface visible in the supplied game screenshot | Audited native decoration | Design constraint |
| --- | --- | --- |
| Top-right menu controls | `MiniMenuBG_0`, `MiniMenuBG_1`; prefab paths under `HUDCanvasPC/SafeArea/MiniMenu` | Rounded square silhouettes; icons and notification dots are separate layers. Preserve both normal and highlight states. |
| Bottom-right skills | `SkillButton_Frame`, `SkillButton_L1`, `SkillButton_L2`, `SkillButton_HoverFrame` | Circular frames and transparent centers; preserve icon, cooldown, and slot-number layers. |
| Chat input | `InputFieldFloatingBG` at `UIChat/UIChatBody/InputField (TMP)` | Skin the input's existing edge, not `mainbox`, the chat container, or the transcript. |
| Bottom XP/rank/gold strip | `BarFillBackgroundSmall`, `Button Grey` under `HUDCanvasPC/SafeArea/XPBars` | Change decorative backing/trim. Fill width, XP/rank colors, and labels continue to express game state. |
| Inventory, shop, quest, and options windows | `9 Slice Main Window Background Round`, direct window corners/dragon decorations, `9 Slice Tab` at the window header | Preserve native nine-slice margins and ornament silhouettes. `Window Top Mid Border` is used by PatternPreview, not a general window header. Keep close-button states native. |

This is a coordinated set of replacement decorative sprites, not a generic border attached
to whichever Image happens to be largest. Bind by verified sprite identity and UI root.
Restore original sprites on Default; keep original layout, interaction, and animation behavior.

## Art direction

- **DoomKnight:** the game's angular, shaded fantasy style; blackened armor, crimson accents,
  and restrained pointed details. Match the existing rounded/circular footprints.
- **Chaos:** violet organic trim, eye/crystal motifs, and asymmetric details placed around
  the native edges. Keep the control's center and icon readable.
- **BLOD:** ivory/silver metal, gold accents, and axe/blade forms incorporated into the native
  trim. Match the game's flat shaded art, rather than a generic white/gold rectangle.

Create one coherent set per theme, including control states. First preview it in the supplied
actual game screenshot and a verified native window layout. Do not substitute an invented
inventory/HUD mockup. The concept must establish shape, detail density, readability, and
compatibility with the portrait system before hooking it into the client.

## Evidence and validation

The installed client assets were copied before inspection. `resources.assets`, its texture
sidecar, and `globalgamemanagers.assets` originals were hash-checked and remain unchanged.
The audit exported native sprites and recorded 5,454 Image-to-sprite references with prefab
paths and RectTransform data. These include inactive/older prefab variants and therefore
are not, by themselves, proof that every asset is visible in the active game UI.

Evidence is under `outputs/ui-themes-native-audit/`: `sprites.json`, `image-bindings.json`,
`all-sprite-names.json`, and `source-sha256.json`. Image references were identified from the
unique local Sprite pointer in each serialized `UnityEngine.UI.Image` component. Full custom
MonoBehaviour typetrees were not available in the installed extraction library.

The local implementation and completed checks are described in `UI_THEMES.md`.
The preview contains the supplied HUD screenshot and native sprite samples; it does
not invent a window layout. Full window rendering still needs Unity verification.

Final validation must cover the actual running client: normal/hover/pressed/disabled states,
skill cooldowns, chat entry, targeting, window dragging/scrolling/closing, HUD scaling,
resizing, theme switching, Default restoration, and persistence. A clean DLL build is only
compiler evidence. Neither a standalone preview nor asset extraction establishes runtime
rendering success.
