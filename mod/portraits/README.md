# Custom Portrait Frame Authoring Kit

Everything needed to author new nameplate/portrait frames (like the potato frame, id 5)
that match the 5 shipped frames (Default, Tier0–Tier3).

## How the system works

The client renders every frame — vanilla or custom — into the **same fixed UI rects**:

| Layer                | PNG name (mod)          | UI rect   | Recommended canvas | Fit behaviour |
|----------------------|-------------------------|-----------|--------------------|---------------|
| Ring around portrait | `<key>_frame.png`       | 252×252   | 512×512 (square)   | preserveAspect (stays circular) |
| Name/HP plate        | `<key>_plate.png`       | 430×240   | 584×322            | Simple stretch to rect |
| Portrait backing     | `<key>_background.png`  | ~278×278  | 278×278            | behind the head, mostly hidden |
| Level badge          | `<key>_lvlcircle.png`   | 91×91     | 222×222 (square)   | preserveAspect |

PNGs live in the client's `UserData/Beyond/portraits/`. The mod
(`mod/InfinityLoader/Entrypoint.cs`) synthesizes a frame setting for ids > 4 from
`_customFrameKey` (e.g. `{5, "potato"}`). Missing layers fall back to Default's art.

**The mod caches sprites for the whole session — fully restart the client after
changing a PNG.**

## The three rules (learned the hard way)

A frame cannot change the UI rects — they are identical for every frame. What makes a
vanilla plate look "small" is **transparency inside its own canvas**:

1. **Never fill the plate canvas edge-to-edge.** Vanilla plates are only 73–82% opaque
   (Default is the lone full-bleed one). Solid art must end by canvas x≈545
   (rect x≈400) — beyond that, only thin flourishes/points, or the plate crowds the
   target-panel ornament ("the dragon") on its right.
2. **The grey field is fixed.** Name/class/HP text render at fixed positions; the
   charcoal box must sit at canvas x70–527, y57–248 (marked green in the template).
   Bigger reads as bloated; smaller clips the health bars.
3. **Left edge tucks behind the ring.** Canvas x<80 is covered by the portrait ring.
   Solid art there is fine (it's hidden); art that pokes above/below the ring's
   silhouette in that zone is *visible* and looks like dirt leaking behind the portrait
   — keep y<26 and y>278 airy (transparent gaps) or empty in that region.

## Templates (`templates/`)

- `plate_template_ghost.png` — Tier3 at 45% opacity + all zones marked. Use as a
  reference layer while painting.
- `plate_template_blank.png` — zone guides only, no ghost; drop on top of WIP art to
  check alignment.
- `ring_template.png` — 512×512; ring art out to 98% of canvas, transparent hole radius
  29.5–34% of canvas (vanilla range; hole must be fully transparent — the character
  head shows through).
- `lvlcircle_template.png` — circle ≈95% of canvas.
- `background_template.png` — simple full-bleed square, keep dark/simple.

Zone colours: **green** = grey field (exact), **orange** = decoration bands
(transparent gaps required), **red hatch** = must stay transparent,
**blue hatch** = hidden behind ring.

## Vanilla reference sprites (`reference/`)

All 14 layer sprites extracted from the client (`resources.assets`, UnityPy) for all 5
shipped frames. Measured geometry:

| Plate      | Canvas   | Opaque | Notes |
|------------|----------|--------|-------|
| Default    | 596×234  | 96%    | plain box, near full-bleed |
| Tier0/1    | 596×275  | 82%    | gold frame, cut corners |
| Tier2      | 596×297  | 78%    | heavier frame |
| Tier3      | 584×322  | 73%    | scalloped top-left, rope bottom-left, pointed right edge |

Rings: opaque to 98% of canvas, hole radius 59–68% diameter. LvlCircles: 111×111,
circle ≈95%. Background: 278×278 near-full square.

## Validate before launching the client

```
python mod/portraits/validate_portrait.py "<path to portraits dir>" <key>
```

Checks each layer against the rules above and prints pass/warn per rule — catches
full-bleed plates, oversized grey fields, and opaque-ring holes without a client
restart cycle.

## Adding a brand-new frame id

1. Server: add the id in `server/game.py` (`_CUSTOM_PORTRAIT_FRAMES`) and grant it.
2. Mod: add `{ id, "key" }` to `_customFrameKey` in `Entrypoint.cs`, rebuild, deploy.
3. Drop `<key>_frame/plate/background/lvlcircle.png` into `UserData/Beyond/portraits/`.
4. Validate, then restart the client.
