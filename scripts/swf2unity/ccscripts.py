"""Find an armor SWF's colour-custom clips: which AS3 classes call mcSetColor(this, Location, Shade).

For now this decompiles the frame scripts with JPEXS once per SWF (cached next to the output); the calls are
plain `MovieClip(...).mcSetColor(this,"Base","Dark")` lines. Returns {character id: (location, shade)}.

Flash applies the colour with AvatarMC.changeColor: a new ColorTransform whose .color is the player's colour
(multipliers 0, offsets = RGB, alpha reset to 1) plus the shade offsets below, replacing the clip's own transform.
"""
import os
import re
import subprocess
import shutil
from pathlib import Path

_LEGACY_JAVA = r"C:/Program Files/Eclipse Adoptium/jre-17.0.18.8-hotspot/bin/java.exe"
_JAVA_HOME_BINARY = str(Path(os.environ.get('JAVA_HOME', '')) / 'bin/java.exe')
_JAVA_INSTALLS = sorted(Path(os.environ.get('ProgramFiles', 'C:/Program Files'), 'Java').glob('jdk*/bin/java.exe'),
                        key=lambda path: path.stat().st_mtime, reverse=True)
JAVA = (os.environ.get('SWF2UNITY_JAVA') or
        (_JAVA_HOME_BINARY if os.environ.get('JAVA_HOME') and Path(_JAVA_HOME_BINARY).is_file() else None) or
        (_LEGACY_JAVA if Path(_LEGACY_JAVA).is_file() else None) or
        (str(_JAVA_INSTALLS[0]) if _JAVA_INSTALLS else None) or shutil.which('java'))
FFDEC_JAR = os.environ.get('SWF2UNITY_FFDEC', r"C:/Program Files (x86)/FFDec/ffdec.jar")
CALL = re.compile(r'mcSetColor\(this,"([^"]+)","([^"]*)"\)')

# AvatarMC.changeColor shade offsets per channel (Dark on Skin only darkens red by 25)
SHADES = {"": (0, 0, 0), "None": (0, 0, 0), "Light": (100, 100, 100), "Dark": (-50, -50, -50),
          "Darker": (-125, -125, -125)}
SHADES['__SkinDark'] = (-25, -50, -50)  # retain authored Skin shading when remapping coefficient channels


def shade_offsets(location, shade):
    if shade == "Dark" and location == "Skin":
        return (-25, -50, -50)
    return SHADES.get(shade, (0, 0, 0))


def ffdec(*args, profile=None):
    if not JAVA or not Path(JAVA).is_file():
        raise RuntimeError('Java runtime not found; set SWF2UNITY_JAVA or JAVA_HOME, or put java on PATH')
    env = dict(os.environ)
    cmd = [JAVA, "-Djava.awt.headless=true", "-Xmx3g"]
    if profile:
        os.makedirs(profile, exist_ok=True)
        cmd.append("-Duser.home=" + profile)
        env["APPDATA"] = profile
    cmd += ["-jar", FFDEC_JAR, "-cli"]
    if "-export" in args or "-render" in args:
        # JPEXS anti-aliases every fill separately, so two shapes sharing an edge each leave it half transparent
        # (conflation): see-through seams inside solid art that Flash Player never draws. Rendering at 16x and
        # box-reducing removes them (Hollowborn Paladin Helmet: 2,041 seam pixels -> none visible).
        cmd += ["-config", "reduceAntialiasConflationByScalingForExport=1,reduceAntialiasConflationByScalingValueForExport="
                + os.environ.get("SWF2UNITY_CONFLATION_SCALE", "16")]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)
    if r.returncode != 0:
        raise RuntimeError("ffdec failed (%d): %s %s" % (r.returncode, r.stdout[-2000:], r.stderr[-2000:]))
    return r.stdout


def cc_classes(swf_path, cache_dir):
    """{short class name: (location, shade)} from the SWF's frame scripts."""
    out_dir = os.path.join(cache_dir, "scripts")
    if not os.path.isdir(out_dir) or not any(True for _ in os.scandir(out_dir)):
        ffdec("-export", "script", out_dir, swf_path, profile=os.path.join(cache_dir, "jpexs_profile"))
    found = {}
    for root, _, files in os.walk(out_dir):
        for f in files:
            if not f.endswith(".as"):
                continue
            text = open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
            m = CALL.search(text)
            if m:
                found[f[:-3]] = (m.group(1), m.group(2) or "None")
    return found


def cc_chars(movie, swf_path, cache_dir):
    """{character id: (location, shade)} for the movie's exported classes that call mcSetColor."""
    by_class = cc_classes(swf_path, cache_dir)
    return {cid: by_class[name.split(".")[-1]] for cid, name in movie.symbols.items()
            if name.split(".")[-1] in by_class}
