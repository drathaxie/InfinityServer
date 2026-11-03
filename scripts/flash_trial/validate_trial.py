"""Check the prepared layers and Unity GPU captures against the full SWF reference.

This validates a bounded raster conversion, not Flash AVM execution or gameplay.
Requires Pillow; UnityPy is additionally used when --require-unity is selected.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from PIL import Image, ImageChops, ImageDraw, ImageStat

from prepare_project import capture_file


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(reference, actual):
    difference = ImageChops.difference(reference, actual)
    return {"max_channel_error": max(high for _, high in difference.getextrema()),
            "mean_absolute_channel_error": sum(ImageStat.Stat(difference).mean) / 3}


def over_background(image, color):
    return Image.alpha_composite(Image.new("RGBA", image.size, (*color, 255)), image).convert("RGB")


def pixel_hash(image):
    # RGB under fully transparent pixels has no visible meaning.
    rgba = image.convert("RGBA")
    visible = rgba.getchannel("A").point([0] + [255] * 255)
    normalized = Image.composite(rgba, Image.new("RGBA", rgba.size), visible)
    return hashlib.sha256(normalized.tobytes()).hexdigest()


def frame_classes(hashes):
    """Encode exact repeated-frame positions independent of the hash values."""
    classes = {}
    return [classes.setdefault(digest, len(classes)) for digest in hashes]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--require-unity", action="store_true")
    parser.add_argument("--max-channel-error", type=int, default=4)
    # Rotated, sub-pixel-offset tile seams round bilinear edges per tile; FlashTrialBuilder allows 6/255
    # (Caladbolg: one pixel of 1.2M at 5 on white). Frame-vs-source comparisons keep --max-channel-error.
    parser.add_argument("--max-seam-error", type=int, default=6)
    args = parser.parse_args()
    conversion = args.trial.resolve() / "conversion"
    capture_path = conversion / "source_capture/capture_manifest.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    config = json.loads((conversion / "UnityProject/trial_config.json").read_text(encoding="utf-8"))
    if (config["frameCount"] != capture["frames"] or config["fps"] != capture["fps"]
            or not config["layers"]):
        raise ValueError("prepared timeline differs from the source capture")
    for layer in config["layers"]:
        if (len(layer["frames"]) != config["frameCount"]
                or len(set(layer["frames"])) != layer["uniqueStates"]):
            raise ValueError("prepared layer sequence or unique-state count differs")
    animated_layers = sum(layer["uniqueStates"] > 1 for layer in config["layers"])
    backgrounds = {"dark": (35, 39, 51), "white": (255, 255, 255), "black": (0, 0, 0)}
    report = {"scope": "Full JPEXS reference versus prepared layers and optional Unity GPU renders; gameplay remains unverified",
              "status": "passed", "frames_checked": capture["frames"],
              "fps": capture["fps"], "backgrounds": backgrounds,
              "tolerance_max_channel_error": args.max_channel_error, "frames": [],
              "unity_checked": args.require_unity, "source_grip_origin_preserved": True,
              "animated_layers": animated_layers, "static_layers": len(config["layers"]) - animated_layers}
    previews = []
    preview_cache = {}
    source_hashes, gpu_hashes = [], []
    failures = []
    for frame in range(capture["frames"]):
        source_path = capture_file(capture_path.parent, "full", capture["canonical_frames"]["full"][frame])
        if sha(source_path) != capture["capture_stats"]["full"]["sha256"][frame]:
            raise ValueError(f"changed full source reference: {source_path}")
        source = Image.open(source_path).convert("RGBA")
        source_hashes.append(pixel_hash(source))
        prepared = Image.new("RGBA", source.size)
        for layer in config["layers"]:
            image = Image.open(conversion / "UnityProject" / layer["frames"][frame]).convert("RGBA")
            if layer.get("hasGutter"):
                # Sprite rect uses bottom-left texture coordinates; Pillow uses top-left.
                left = layer["spriteX"]
                top = image.height - layer["spriteY"] - layer["height"]
                image = image.crop((left, top, left + layer["width"], top + layer["height"]))
            left = config["originX"] + layer["xPixels"] - image.width / 2
            top = config["originY"] - layer["yPixels"] - image.height / 2
            if (left, top) != (round(left), round(top)):
                raise ValueError("layer offset does not reproduce integer source pixels")
            prepared.alpha_composite(image, (round(left), round(top)))
        targets = {"prepared": prepared}
        if args.require_unity:
            gpu_path = conversion / f"bundle/unity_frames/frame_{frame+1:02}.png"
            gpu = Image.open(gpu_path).convert("RGBA")
            if gpu.size != source.size:
                raise ValueError("GPU render size differs from reference")
            targets["unity_gpu"] = gpu
            gpu_hashes.append(pixel_hash(gpu))
        row = {"frame": frame + 1}
        for name, target in targets.items():
            comparisons = {background: metrics(over_background(source, color), over_background(target, color))
                           for background, color in backgrounds.items()}
            row[name] = comparisons
            for background, values in comparisons.items():
                if values["max_channel_error"] > args.max_channel_error:
                    failures.append(f"frame {frame+1}, {name}, {background}: {values}")
        report["frames"].append(row)
        if args.require_unity:
            # These are actual Unity camera frames, with an ordinary opaque preview background.
            if gpu_hashes[-1] not in preview_cache:
                preview_width = max(280, source.width + 24)
                canvas = Image.new("RGB", (preview_width, source.height + 30), backgrounds["dark"])
                canvas.paste(over_background(targets["unity_gpu"], backgrounds["dark"]), ((preview_width-source.width)//2, 30))
                ImageDraw.Draw(canvas).text((10, 8), "Unity bundle playback - " + config.get("previewName", "Omega Rapier"), fill="white")
                preview_cache[gpu_hashes[-1]] = canvas
            previews.append(preview_cache[gpu_hashes[-1]])
    report["expected_unique_source_frames"] = len(set(source_hashes))
    report["source_frame_classes"] = frame_classes(source_hashes)
    report["unique_textures"] = sum(layer["uniqueStates"] for layer in config["layers"])
    report["raw_rgba8_unique_texture_bytes"] = sum(layer.get("textureWidth", layer["width"])*layer.get("textureHeight", layer["height"])*4*layer["uniqueStates"] for layer in config["layers"])
    if args.require_unity:
        validation = json.loads((conversion / "bundle/unity_validation.json").read_text(encoding="utf-8"))
        if (validation["status"] != "passed" or validation["framesChecked"] != capture["frames"]
                or not validation["loopRestoresFirstState"] or validation["customRuntimeRequired"]
                or validation["dependencies"] or validation["prefabName"] != config["prefabName"]):
            raise ValueError("Unity runtime validation did not pass the expected contract")
        if config.get("schemaVersion", 1) >= 2:
            if (not validation.get("loopRestoresFirstPixels")
                    or validation.get("animatedLayers") != animated_layers
                    or validation.get("staticLayers") != len(config["layers"]) - animated_layers):
                raise ValueError("Unity loop pixels or animated/static layer counts differ")
            loop_path = conversion / "bundle/unity_frames/loop.png"
            loop = Image.open(loop_path).convert("RGBA")
            first_gpu = Image.open(conversion / "bundle/unity_frames/frame_01.png").convert("RGBA")
            if loop.size != first_gpu.size or loop.tobytes() != first_gpu.tobytes():
                raise ValueError("Unity loop pixels differ from the first frame")
            report["loop_pixels_equal_first"] = True
        if any(layer.get("hasGutter") for layer in config["layers"]):
            seam_checks = validation.get("seamChecks", [])
            if (not validation.get("seamChecksPassed") or not seam_checks
                    or {check["background"] for check in seam_checks} != set(backgrounds)
                    or any(check["maxChannelError"] > args.max_seam_error or check["rotationDegrees"] == 0
                           or check["offsetXPixels"] == round(check["offsetXPixels"])
                           or check["offsetYPixels"] == round(check["offsetYPixels"]) for check in seam_checks)):
                raise ValueError("rotated fractional-position GPU seam checks did not pass")
            report["gpu_tile_seam_checks"] = seam_checks
        import UnityPy
        bundle_path = conversion / "bundle" / config["bundleName"]
        bundle = UnityPy.load(str(bundle_path))
        types = Counter(obj.type.name for obj in bundle.objects)
        if types["MonoBehaviour"] or types["MonoScript"] or types["SpriteRenderer"] != len(config["layers"]):
            raise ValueError("unexpected runtime scripts or sprite renderer count in serialized bundle")
        expected_animators = int(animated_layers > 0)
        if (types["Animator"] != expected_animators or types["AnimationClip"] != expected_animators
                or types["Sprite"] != report["unique_textures"]):
            raise ValueError("unexpected native animation or sprite counts")
        if list(bundle.container) != [config["prefabName"].lower()]:
            raise ValueError("serialized prefab address differs")
        texture_bytes = 0
        texture_formats = Counter()
        for obj in bundle.objects:
            if obj.type.name == "Texture2D":
                texture = obj.read()
                if texture.m_MipCount != 1:
                    raise ValueError("trial textures unexpectedly contain mipmaps")
                texture_bytes += texture.m_CompleteImageSize
                texture_formats[str(texture.m_TextureFormat)] += 1
        if frame_classes(gpu_hashes) != report["source_frame_classes"]:
            raise ValueError("Unity repeated-frame positions or distinct states differ from the source")
        report["gpu_frame_classes"] = frame_classes(gpu_hashes)
        crc_match = re.search(r"^CRC: (\d+)$", bundle_path.with_suffix(bundle_path.suffix + ".manifest").read_text(), re.MULTILINE)
        if crc_match is None:
            raise ValueError("missing Unity bundle CRC")
        report["bundle"] = {"file": str(bundle_path), "bytes": bundle_path.stat().st_size,
                            "sha256": sha(bundle_path), "windows_crc": int(crc_match[1]), "object_types": dict(types),
                            "serialized_texture_bytes": texture_bytes, "serialized_texture_formats": dict(texture_formats),
                            "container_names": list(bundle.container), "unity_validation": validation,
                            "unique_gpu_frames": len(set(gpu_hashes))}
        durations = [round((i+1)*1000/config["fps"])-round(i*1000/config["fps"]) for i in range(config["frameCount"])]
        previews[0].save(conversion / "unity_playback.webp", save_all=True, append_images=previews[1:],
                         duration=durations, loop=0, lossless=True)
    report["failures"] = failures
    if failures:
        report["status"] = "failed"
    output = conversion / ("render_validation.json" if args.require_unity else "prepared_validation.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.require_unity and not failures:
        # Tie the local import contract to this validated build, including after a rebuild.
        manifest_path = args.trial / "trial_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if "conversion" in manifest:
                manifest["conversion"].update(bundle_sha256=report["bundle"]["sha256"],
                                              bundle_bytes=report["bundle"]["bytes"],
                                              windows_crc=report["bundle"]["windows_crc"])
                for entry in manifest.get("files", []):
                    path = args.trial / entry["path"]
                    entry.update(bytes=path.stat().st_size, sha256=sha(path))
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        plan_path = args.trial / "import_plan.json"
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["sha256"] = report["bundle"]["sha256"]
            plan["bundle_catalog_fields"]["CRCPC"] = str(report["bundle"]["windows_crc"])
            plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output),
                      "frames_checked": report["frames_checked"], "unity_checked": args.require_unity,
                      "worst_channel_error": max(v["max_channel_error"] for row in report["frames"]
                                                 for name, comparisons in row.items() if name != "frame"
                                                 for v in comparisons.values()), "failures": failures}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
