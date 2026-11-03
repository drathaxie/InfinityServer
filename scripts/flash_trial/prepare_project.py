"""Prepare an isolated, native-component Unity project from validated SWF captures.

The SWF and source renderer stay outside this helper. This is a bounded raster
trial compiler, not a general SWF interpreter. Static layers are shared; changing
layers get sprite sequences. Inputs are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

from PIL import Image


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_file(directory: Path, layer: str, supplied: str) -> Path:
    """Resolve original and relocated manifests, including shared full-frame tiles."""
    supplied = Path(supplied)
    candidates = []
    if "canonical" in supplied.parts:
        candidates.append(directory / Path(*supplied.parts[supplied.parts.index("canonical"):]))
    if not supplied.is_absolute():
        candidates.append(directory / supplied)
    candidates.extend((directory / "canonical" / layer / supplied.name, supplied))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(f"capture file does not resolve: {supplied}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pixels-per-unit", type=float, default=100 / 1.2)
    parser.add_argument("--prefab-name", default="OmegaRapier_FlashTrial")
    parser.add_argument("--bundle-name", default="omega_rapier_flash_trial.unity3d")
    parser.add_argument("--asset-prefix", default="OmegaRapier")
    parser.add_argument("--preview-name", default="Omega Rapier")
    parser.add_argument("--frame-count", type=int,
                        help="Require this many captured timeline frames; never truncates the source")
    args = parser.parse_args()
    if not math.isfinite(args.pixels_per_unit) or args.pixels_per_unit <= 0:
        raise ValueError("pixels-per-unit must be positive")
    for value, label in ((args.prefab_name, "prefab-name"), (args.asset_prefix, "asset-prefix")):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", value):
            raise ValueError(f"{label} must be a single asset name without path separators")
    if not re.fullmatch(r"[a-z0-9_][a-z0-9_.-]*\.unity3d", args.bundle_name):
        raise ValueError("bundle-name must be a lowercase .unity3d filename")
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    if (not capture["inputs_unchanged"] or not isinstance(capture["frames"], int)
            or capture["frames"] < 1 or capture["fps"] <= 0):
        raise ValueError("unexpected or modified source capture")
    if args.frame_count is not None and args.frame_count != capture["frames"]:
        raise ValueError("frame-count differs from the captured timeline")
    layer_names = capture["partition_order_back_to_front"]
    if (not layer_names or len(set(name.casefold() for name in layer_names)) != len(layer_names)
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) for name in layer_names)):
        raise ValueError("capture must provide distinct ordered layer names without path separators")
    project = args.project.resolve()
    output = args.output.resolve()
    for name in ("Assets/Editor", "Assets/Trial/Frames", "Packages", "ProjectSettings"):
        (project / name).mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("FlashTrialBuilder.cs"),
                    project / "Assets/Editor/FlashTrialBuilder.cs")
    (project / "ProjectSettings/ProjectVersion.txt").write_text(
        "m_EditorVersion: 6000.3.18f1\n", encoding="utf-8")
    packages = {"dependencies": {f"com.unity.modules.{name}": "1.0.0" for name in
                ("animation", "assetbundle", "imageconversion", "jsonserialize", "ui")}}
    (project / "Packages/manifest.json").write_text(json.dumps(packages, indent=2), encoding="utf-8")
    origin_x, origin_y = capture["source_origin_cropped_top_left_pixels"]
    layers = []
    work = []
    path_layers = {}
    resolved_paths = {}
    inspected = {}
    source_frame_references = 0
    image_size = None
    visible_rects = capture.get("layer_visible_rects_top_left_pixels", {})
    gutter = capture.get("layer_gutter_pixels", 2)
    if not isinstance(gutter, int) or gutter < 1:
        raise ValueError("tile gutter must be a positive integer")

    def resolve_frame(layer, supplied):
        # Explicit canonical paths are independent of the referencing tile.
        # Bare names retain their layer context for older capture manifests.
        key = (None if "canonical" in Path(supplied).parts else layer, supplied)
        if key not in resolved_paths:
            resolved_paths[key] = capture_file(args.capture.parent, layer, supplied)
        return resolved_paths[key]

    for index, name in enumerate(layer_names):
        supplied_frames = capture["canonical_frames"][name]
        resolved = {p: resolve_frame(name, p) for p in dict.fromkeys(supplied_frames)}
        frames = [resolved[p] for p in supplied_frames]
        expected = capture["capture_stats"][name]["sha256"]
        if len(frames) != capture["frames"] or len(expected) != len(frames):
            raise ValueError(f"unexpected capture count for {name}")
        boxes = []
        for path, digest in zip(frames, expected):
            if path not in inspected:
                with Image.open(path) as image:
                    rgba = image.convert("RGBA")
                    inspected[path] = {"file": str(path), "sha256": sha(path),
                                       "size": rgba.size, "alpha_bbox": rgba.getchannel("A").getbbox()}
                    del rgba
            entry = inspected[path]
            if entry["sha256"] != digest:
                raise ValueError(f"capture hash mismatch for {name}: {path}")
            if image_size is None:
                image_size = entry["size"]
            if entry["size"] != image_size:
                raise ValueError("captures must share the source coordinate canvas")
            if entry["alpha_bbox"]:
                boxes.append(entry["alpha_bbox"])
            source_frame_references += 1
        width, height = image_size
        if name in visible_rects:
            visible = tuple(visible_rects[name])
            if (len(visible) != 4 or any(not isinstance(value, int) for value in visible)
                    or not 0 <= visible[0] < visible[2] <= width
                    or not 0 <= visible[1] < visible[3] <= height):
                raise ValueError(f"invalid visible tile rectangle: {name}")
            crop = (max(0, visible[0] - gutter), max(0, visible[1] - gutter),
                    min(width, visible[2] + gutter), min(height, visible[3] + gutter))
        else:
            if not boxes:
                raise ValueError(f"empty layer: {name}")
            crop = (max(0, min(b[0] for b in boxes) - 2), max(0, min(b[1] for b in boxes) - 2),
                    min(width, max(b[2] for b in boxes) + 2), min(height, max(b[3] for b in boxes) + 2))
            visible = crop
        asset_dir = project / "Assets/Trial/Frames" / name
        asset_dir.mkdir(parents=True, exist_ok=True)
        item = {"source_name": name, "frames": frames, "crop": crop,
                "asset_dir": asset_dir, "temporary": {}, "by_path": {},
                "layer": {"name": name[:1].upper() + name[1:], "sortingOrder": index,
                       "xPixels": (visible[0] + visible[2]) / 2 - origin_x,
                       "yPixels": origin_y - (visible[1] + visible[3]) / 2,
                       "width": visible[2] - visible[0], "height": visible[3] - visible[1],
                       "textureWidth": crop[2] - crop[0], "textureHeight": crop[3] - crop[1],
                       "spriteX": visible[0] - crop[0], "spriteY": crop[3] - visible[3],
                       "hasGutter": name in visible_rects,
                       "sourceCrop": list(crop)}}
        work.append(item)
        for path in dict.fromkeys(frames):
            path_layers.setdefault(path, []).append(item)

    # Decode each full source once, then crop all layers that reference it.
    # Only the current full image and current small tile are held in memory.
    for path, items in path_layers.items():
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
        for item in items:
            cropped = rgba.crop(item["crop"])
            digest = hashlib.sha256(cropped.tobytes()).hexdigest()
            if digest not in item["temporary"]:
                temporary = item["asset_dir"] / f"digest_{digest}.png"
                cropped.save(temporary)
                item["temporary"][digest] = temporary
            item["by_path"][path] = digest
            del cropped
        del rgba

    # Assign state IDs in each layer's own first-appearance order even when its
    # path order differs from other layers sharing the same decoded sources.
    for item in work:
        seen = {}
        sequence = []
        for path in item["frames"]:
            digest = item["by_path"][path]
            if digest not in seen:
                asset_path = f"Assets/Trial/Frames/{item['source_name']}/state_{len(seen):02}.png"
                item["temporary"][digest].replace(project / asset_path)
                seen[digest] = asset_path
            sequence.append(seen[digest])
        item["layer"].update(frames=sequence, uniqueStates=len(seen))
        layers.append(item["layer"])
    config = {"schemaVersion": 2, "prefabName": args.prefab_name,
              "bundleName": args.bundle_name, "assetPrefix": args.asset_prefix,
              "previewName": args.preview_name,
              "outputDirectory": str(output), "fps": capture["fps"], "frameCount": capture["frames"],
              "width": image_size[0], "height": image_size[1],
              "originX": origin_x, "originY": origin_y,
              "pixelsPerUnit": args.pixels_per_unit, "layers": layers}
    if visible_rects:
        config["referenceFrames"] = [str(resolve_frame("full", p))
                                     for p in capture["canonical_frames"]["full"]]
    (project / "trial_config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    preparation = {"capture_manifest": str(args.capture.resolve()),
                   "capture_manifest_sha256": sha(args.capture), "source_frames": list(inspected.values()),
                   "source_frame_references": source_frame_references,
                   "config": config, "project": str(project),
                   "raw_rgba8_unique_texture_bytes": sum(layer["textureWidth"] * layer["textureHeight"] * 4 * layer["uniqueStates"] for layer in layers),
                   "scale_policy": f"Explicit source pixels/unit: {args.pixels_per_unit:g}; preserve the captured SWF registration point",
                   "runtime_code": "Only Unity native Animator, Transform and SpriteRenderer; no custom MonoBehaviour"}
    (output / "preparation.json").write_text(json.dumps(preparation, indent=2), encoding="utf-8")
    print(json.dumps({"project": str(project), "output": str(output),
                      "unique_textures": sum(layer["uniqueStates"] for layer in layers),
                      "raw_rgba8_unique_texture_bytes": preparation["raw_rgba8_unique_texture_bytes"],
                      "fps": config["fps"], "frames": config["frameCount"],
                      "animated_layers": sum(layer["uniqueStates"] > 1 for layer in layers),
                      "static_layers": sum(layer["uniqueStates"] == 1 for layer in layers),
                      "pixels_per_unit": config["pixelsPerUnit"]}, indent=2))


if __name__ == "__main__":
    main()
