"""Build the AQW:I bundle for a converted armor (armor.py output) in the persistent swf2unity Unity project.

usage: python build_armor.py <armor out dir> <template dir> --bundle <id>_<Name>.unity3d [--project DIR]
                             [--validate 0,10,20] [--unity PATH]

Steps: refresh the project's scripts/shader from scripts/swf2unity/unity, copy the armor's atlases and masks into
Assets/Armors/<Name>, write the JsonUtility-friendly unity_input.json (flattened prefab template, flat key arrays),
emulate the client's compositor for validation, run Unity in batch mode, then compare Unity's renders with the
numpy emulation of the same frames.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

import validate_armor as va

HERE = os.path.dirname(os.path.abspath(__file__))
UNITY = r"C:\Program Files\Unity\Hub\Editor\6000.3.18f1\Editor\Unity.exe"
DEFAULT_PROJECT = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "swf2unity", "UnityProject")
MANIFEST = {"dependencies": {"com.unity.modules.animation": "1.0.0", "com.unity.modules.assetbundle": "1.0.0",
                             "com.unity.modules.imageconversion": "1.0.0", "com.unity.modules.jsonserialize": "1.0.0",
                             "com.unity.modules.ui": "1.0.0", "com.unity.modules.imgui": "1.0.0"}}


def unity_environment():
    environment = dict(os.environ)
    # Unity's default shader compiler deadline was exceeded by the audited
    # 128-layer shader under contention. Keep a bounded deadline and all gates.
    environment.setdefault('UNITY_SHADER_COMPILER_TASK_TIMEOUT_MINUTES', '20')
    return environment


def sync_project(project):
    os.makedirs(os.path.join(project, "Packages"), exist_ok=True)
    mpath = os.path.join(project, "Packages", "manifest.json")
    if not os.path.exists(mpath):
        json.dump(MANIFEST, open(mpath, "w"), indent=2)
    src = os.path.join(HERE, "unity", "Assets")
    for root, _, files in os.walk(src):
        rel = os.path.relpath(root, src)
        dst = os.path.join(project, "Assets", rel)
        os.makedirs(dst, exist_ok=True)
        for f in files:
            s, d = os.path.join(root, f), os.path.join(dst, f)
            if not os.path.exists(d) or open(s, "rb").read() != open(d, "rb").read():
                shutil.copyfile(s, d)


def remove_armor_asset_folder(project, folder):
    """Only replace one named armor asset directory inside this Unity project."""
    project_root = Path(project).resolve()
    base = (project_root / "Assets" / "Armors").resolve()
    target = Path(folder).resolve()
    if (not base.is_relative_to(project_root) or target.parent != base or target == base
            or Path(folder).is_symlink()):
        raise ValueError("refusing to remove an asset directory outside this project's named armor: " + str(target))
    if target.is_dir():
        shutil.rmtree(target)


def flatten_template(tree):
    nodes = []

    def walk(n, parent):
        idx = len(nodes)
        r = n.get("renderer")
        slot = next((s["slot"] for s in n["scripts"] if s.get("class") == "CustomizableSlot"), -1)
        nodes.append({"name": n["name"], "parent": parent, "active": n["active"], "pos": n["pos"], "rot": n["rot"],
                      "scale": n["scale"], "slot": slot, "hasRenderer": r is not None,
                      "order": r["order"] if r else 0, "sortingLayer": r["layer"] if r else 0,
                      "rendererEnabled": r["enabled"] if r else True,
                      "color": r["color"] if r else [1, 1, 1, 1]})
        for c in n["children"]:
            walk(c, idx)

    walk(tree, -1)
    return nodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("armor_out")
    ap.add_argument("template")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--output-dir", help="Bundle and validation output directory; defaults to <armor_out>/bundle")
    ap.add_argument('--prepare-only', action='store_true', help='Prepare input for ArmorBuilder.BuildMany without launching Unity')
    ap.add_argument('--texture-budget-bytes', type=int, default=0,
                    help='Texture budget waiver; only for bundles listed in batch_trials.TEXTURE_BUDGET_WAIVERS')
    ap.add_argument("--validate", default="0,7,19,33,47,61,79,95")
    ap.add_argument("--unity", default=UNITY)
    ap.add_argument("--max-mean-error", type=float, default=2.0,
                    help="Maximum mean GPU-versus-emulation channel error on the 0..255 scale")
    ap.add_argument("--max-p99-error", type=float, default=24.0,
                    help="Maximum 99th-percentile GPU-versus-emulation error; compressed edge pixels can differ more")
    args = ap.parse_args()
    args.armor_out = os.path.abspath(args.armor_out)
    args.template = os.path.abspath(args.template) if args.template != "-" else "-"
    args.project = os.path.abspath(args.project)
    for threshold in (args.max_mean_error, args.max_p99_error):
        if not math.isfinite(threshold) or threshold < 0:
            ap.error("validation error thresholds must be finite and nonnegative")
    data = json.load(open(os.path.join(args.armor_out, "armor.json")))
    template = json.load(open(os.path.join(args.template, "template.json"))) if args.template != "-" else {}
    name = data["name"]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
        raise ValueError("armor name must be a single asset directory name")
    sync_project(args.project)
    folder_rel = "Assets/Armors/" + name
    folder = os.path.join(args.project, *folder_rel.split("/"))
    remove_armor_asset_folder(args.project, folder)
    os.makedirs(folder)
    out_dir = os.path.abspath(args.output_dir or os.path.join(args.armor_out, "bundle"))
    frames = [int(f) for f in args.validate.split(",") if f.strip()] if args.validate else []
    if any(f < 0 for f in frames) or len(frames) != len(set(frames)):
        ap.error("validation frames must be unique nonnegative integers")
    genders = []
    sims = {}
    reconstructions = {}
    for g, gd in data["genders"].items():
        shutil.copyfile(os.path.join(args.armor_out, gd["atlas"]), os.path.join(folder, gd["atlas"]))
        mdir = os.path.join(args.armor_out, g + "_masks")
        if os.path.isdir(mdir):
            shutil.copytree(mdir, os.path.join(folder, g + "_masks"))
        reconstruction = flatten_reconstruction(gd.get("color_reconstruction"), args.armor_out, folder)
        dest_rel = None
        if frames:
            atlas = np.asarray(Image.open(os.path.join(args.armor_out, gd["atlas"])).convert("RGBA"))
            # armors: the client's compositor output; weapons/pets draw straight from their atlas
            dest = va.composite(atlas, args.armor_out, gd) if gd["itemslots"] and not gd.get('direct_atlas') else atlas.astype(np.float32) / 255
            dest_rel = "%s_dest_palette.png" % g
            Image.fromarray((np.clip(dest, 0, 1) * 255 + 0.5).astype(np.uint8)).save(os.path.join(folder, dest_rel))
            sims[g] = np.asarray(Image.open(os.path.join(folder, dest_rel)), np.float32) / 255
            reconstructions[g] = va.load_reconstruction(args.armor_out, gd)
        sprites = []
        for s in gd["sprites"]:
            x, y, w, h = s["rect"]
            sprites.append({"name": s["name"], "x": x, "y": y, "w": w, "h": h, "px": s["pivot"][0],
                            "py": s["pivot"][1], "ppu": s["ppu"], "mask": s["mask"] or ""})
        renderers = []
        for slot, r in gd["renderers"].items():
            layers = []
            for L in r.get("layers") or []:
                if any(len(k) != 18 for k in L["keys"]):
                    raise ValueError("layer keys must be A4 T2 CM4 CA4 Rect4")
                layers.append({"rect": L["rect"], "period": L["period"], "blend": float(L.get("blend") or 0),
                               'groupStart': bool(L.get('group_start')), 'groupEnd': L.get('group_end', -1),
                               'steppedRect': bool(L.get('stepped_rect')),
                               'morphFrame': int(L.get('morph_frame', -1)),
                               "stepped": bool(L.get("stepped")),
                               "keys": [float(v) for k in L["keys"] for v in k]})
            morph = r.get('morph')
            if morph:
                morph = dict(morph, hit_frames=[{'polygons': [{'xy': xy} for xy in polygons]}
                                               for polygons in morph['hit_frames']])
            renderers.append({"slot": slot, "sprite": r["sprite"], "layered": bool(r.get("layers")),
                              "tint": r.get("tint", [1, 1, 1, 1]), "layers": layers,
                              'morphJson': json.dumps(morph, separators=(',', ':')) if morph else ''})
        genders.append({"g": g, "atlas": gd["atlas"], "atlasW": gd["atlas_size"][0], "atlasH": gd["atlas_size"][1],
                        "sprites": sprites, "itemslots": gd["itemslots"], "renderers": renderers,
                        "nodes": gd.get("nodes") or flatten_template(template[g]["tree"]),
                        "destPalette": dest_rel or "", "reconstruction": reconstruction,
                        "prefabName": gd.get("prefab_name", ""), "atlasFormat": gd.get("atlas_format", "DXT5"),
                        "meshType": gd.get("mesh_type", "Tight"), "noItemSlots": not gd["itemslots"],
                        "petLoop": bool(gd.get("pet_loop")), "sourceFace": bool(gd.get('source_face'))})
    uin = {"name": name, "folder": folder_rel, "bundleName": args.bundle, "outputDir": out_dir.replace("\\", "/"),
           "fps": data["fps"], "genders": genders, "validateFrames": frames}
    if args.texture_budget_bytes:
        uin["textureBudgetBytes"] = args.texture_budget_bytes
    inpath = os.path.join(folder, "unity_input.json")
    json.dump(uin, open(inpath, "w"))
    os.makedirs(out_dir, exist_ok=True)
    if args.prepare_only:
        print('prepared Unity input', inpath, flush=True)
        return
    log = os.path.join(out_dir, "unity_build.log")
    cmd = [args.unity, "-batchmode", "-quit", "-projectPath", args.project, "-executeMethod", "ArmorBuilder.Build",
           "-armorInput", inpath, "-logFile", log]
    print("running Unity...", flush=True)
    launch = {}
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        launch = {"startupinfo": startup, "creationflags": subprocess.CREATE_NO_WINDOW}
    r = subprocess.run(cmd, timeout=3600, env=unity_environment(), **launch)
    text = open(log, encoding="utf-8", errors="replace").read() if os.path.exists(log) else ""
    if r.returncode != 0 or "ARMOR_BUILD_SUCCESS" not in text:
        tail = [l for l in text.splitlines() if "error" in l.lower() or "ARMOR_BUILD" in l][-30:]
        print("\n".join(tail))
        sys.exit("Unity build failed (exit %d), log: %s" % (r.returncode, log))
    print(open(os.path.join(out_dir, "unity_report.txt")).read())
    bundle = os.path.join(out_dir, args.bundle)
    print("bundle", bundle, os.path.getsize(bundle))
    summary = compare(data, sims, out_dir, frames, args.max_mean_error, args.max_p99_error,
                      reconstructions) if frames else {"status": "not_run", "expected_captures": 0, "compared_captures": 0}
    contract = json.load(open(os.path.join(out_dir, "bundle_contract.json")))
    evidence = {"status": "passed" if summary["status"] == "passed" else "built_without_render_validation",
                "bundle_name": args.bundle, "bundle_bytes": os.path.getsize(bundle),
                "bundle_sha256": hashlib.sha256(Path(bundle).read_bytes()).hexdigest(),
                "bundle_crc": contract["bundleCRC"], "unity_version": contract["unityVersion"],
                "unity_render_validation": summary, "bundle_contract": contract,
                "source_fidelity": {"scope": "separate Flash-source comparison; piecewise player-color response is approximate",
                                    "report": os.path.join(args.armor_out, "validation.json")}}
    json.dump(evidence, open(os.path.join(out_dir, "build_validation.json"), "w"), indent=2)


def flatten_reconstruction(model, armor_out, folder):
    if not model:
        return {"enabled": False}
    if model["model"] != "piecewise_linear" or not 0 < model["knot"] < 1:
        raise ValueError("unsupported color reconstruction model")
    result = {"enabled": True, "model": model["model"], "knot": model["knot"], "probes": model["probes"]}
    if model.get('direct_palette'):
        result.update(directPalette=True, paletteBase=[v / 255 for v in model['palette']['Base']],
                      paletteTrim=[v / 255 for v in model['palette']['Trim']],
                      paletteAccessory=[v / 255 for v in model['palette']['Accessory']])
        locations = ['Base', 'Trim', 'Accessory', 'Skin', 'Hair', 'Eye']
        result['paletteSources'] = [locations.index(model['direct_palette'][slot]) for slot in locations[:3]]
        if len(model['locations']) > 3:
            result['extraPalette'] = True
            for loc in locations[3:]:
                result['palette' + loc] = [v / 255 for v in model['palette'][loc]]
    result['coefficientFormat'] = model.get('coefficient_format', 'RGBA32')
    definitions = {"constant": model["constant_atlas"]}
    for location in model['locations']:
        field = location[0].lower() + location[1:]
        definitions[field + "Slope"] = model["slope_atlases"][location]
        definitions[field + "HighSlope"] = model["high_slope_atlases"][location]
    base = Path(armor_out).resolve()
    for key, definition in definitions.items():
        src = (base / definition["path"]).resolve()
        if not src.is_relative_to(base) or src == base or Path(definition["path"]).is_absolute():
            raise ValueError("coefficient texture path escapes armor output")
        dst = Path(folder) / definition["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        result[key] = {"path": definition["path"], "decodeScale": definition["decode_scale"],
                       "decodeBias": definition["decode_bias"],
                       "format": model.get('slope_coefficient_format', result['coefficientFormat'])
                                 if key != 'constant' else result['coefficientFormat']}
    return result


def compare(data, sims, out_dir, frames, max_mean_error=2.0, max_p99_error=24.0, reconstructions=None):
    """Unity's validation renders vs the numpy FlashRig emulation of the same frames (premultiplied RGBA)."""
    vdir = os.path.join(out_dir, "unity_validation")
    results = {}
    failures = []
    expected = 0
    for g, gd in data["genders"].items():
        sprites = {s["name"]: s for s in gd["sprites"]}
        dest = sims[g]
        for slot, r in gd["renderers"].items():
            if slot.startswith("eyes"):
                continue
            spr = sprites[r["sprite"]]
            worst = 0.0
            for f in frames:
                expected += 1
                p = os.path.join(vdir, "%s_%s_f%03d.png" % (g, slot, f))
                if not os.path.exists(p):
                    failures.append("missing Unity validation capture: " + p)
                    continue
                u = np.asarray(Image.open(p).convert("RGBA"), np.float32) / 255  # premultiplied, y down
                if r.get("layers"):
                    sim = va.shade_frame(dest, spr, r["layers"], f, r.get("tint", [1, 1, 1, 1]),
                                         reconstruction=(reconstructions or {}).get(g))
                else:
                    x,y,w,h = spr["rect"]
                    top = dest.shape[0]-y-h
                    sim = dest[top:top+h,x:x+w]
                spm = np.concatenate([sim[..., :3] * sim[..., 3:4], sim[..., 3:4]], axis=2)
                if u.shape != spm.shape:
                    failures.append("Unity validation capture has the wrong shape: " + p)
                    continue
                d = np.abs(u - spm).max(axis=2) * 255
                worst = max(worst, float(d.mean()))
                results["%s/%s/f%d" % (g, slot, f)] = {"mean": round(float(d.mean()), 3),
                                                       "p99": round(float(np.percentile(d, 99)), 2),
                                                       "max": round(float(d.max()), 1)}
                if float(d.mean()) > max_mean_error or float(np.percentile(d, 99)) > max_p99_error:
                    failures.append("GPU-versus-emulation error exceeds bounds: %s/%s/f%d" % (g, slot, f))
            print("%s/%-13s unity vs emulation: worst frame mean %.3f" % (g, slot, worst))
    json.dump(results, open(os.path.join(out_dir, "unity_vs_emulation.json"), "w"), indent=1)
    summary = {"status": "failed" if failures else "passed", "expected_captures": expected,
               "compared_captures": len(results), "max_mean_error": max_mean_error,
               "max_p99_error": max_p99_error, "failures": failures,
               "worst_mean_error": max((v["mean"] for v in results.values()), default=None),
               "worst_p99_error": max((v["p99"] for v in results.values()), default=None),
               "worst_max_error": max((v["max"] for v in results.values()), default=None),
               "scope": "GPU versus compositor/shader emulation; Flash source fidelity is checked separately"}
    json.dump(summary, open(os.path.join(out_dir, "unity_validation_summary.json"), "w"), indent=2)
    if failures:
        raise RuntimeError("Unity validation failed: " + "; ".join(failures[:8]))
    return summary


if __name__ == "__main__":
    main()
