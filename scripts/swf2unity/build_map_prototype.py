"""Stage an isolated, local-only two-cell Unity map prototype.

Uses snapshot art deliberately: animation/gameplay conversion remains held.
Never syncs or opens the shared item-conversion Unity project.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
UNITY = Path(r'C:\Program Files\Unity\Hub\Editor\6000.3.18f1\Editor\Unity.exe')

STUBS = {
    'StateMachine': ('Pixelplacement', '''public GameObject defaultState, currentState;
        public bool _unityEventsFolded, verbose, allowReentry, returnToDefaultOnDisable = true;'''),
    'State': ('Pixelplacement', ''),
    'Initialization': ('Pixelplacement', ''),
    'MapPrefab': ('', 'public string CurrentCell; public MapCell MapCell;'),
    'MapCell': ('', '''public MapPrefab ParentMap; public MapSpawn[] SpawnPads;
        public MapMonSpawn[] MonSpawnPads; public BoxCollider ConfinerCollider;
        public bool OverrideZoom; public float zoom = 16, entityScale = 1, entitySpeed = 12;
        public Sprite BookEndLeft, BookEndRight; public int bookEndOption; public Color32 blockerColor;'''),
    'MapSpawn': ('', 'public bool isCheckpoint;'),
    'MapMonSpawn': ('', 'public int MonMapID; public int Facing = 1;'),
    'ClickableWalk': ('', 'public LayerMask ClickableLayer; public GameObject walkTarget;'),
    'MapBlocker': ('', ''),
    'ZOffset': ('', '''public bool IsStatic, isHouseItem;
        public int yModifier = 500, currentOffset, extraSortingOrder;
        public float yOffset, lastY, lastScale, lastScaleY, initScale;
        public int Facing = 1; public Transform rootTransform;
        public UnityEngine.Rendering.SortingGroup sortingGroup;'''),
    'BGParallax': ('', '''[System.Serializable] public class ParallaxLayer { public float Speed; public GameObject Layer; }
        public ParallaxLayer[] levels; public Camera mainCamera;
        public System.Collections.Generic.List<Vector3> _initLayerPos = new System.Collections.Generic.List<Vector3>();'''),
    'MapGoToCell': ('', '''public string TargetCell, TargetSpawnPad; public bool sendFade, askFirst;
        public string askMessage = "Are you sure you want to go this way?";
        public bool LockedInCombat; public int LockedUntilQuestCompleteID;
        public string CustomQuestLockedMessage = "", LockedUntilMobDead = "";'''),
}


def stage(source, project, cells, visuals=None, calibration=None, gpu_compression=False, effects_bundle=None):
    source, project = source.resolve(), project.resolve()
    manifest = json.loads((source / 'map_source.json').read_text(encoding='utf-8'))
    marker = project / 'map-prototype-owner.json'
    if project.exists() and any(project.iterdir()) and not marker.exists():
        raise ValueError('Refusing to change an existing non-prototype Unity project')
    if marker.exists() and json.loads(marker.read_text())['source_sha256'] != manifest['source_sha256']:
        raise ValueError('Prototype project belongs to a different source')
    project.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(dict(source_sha256=manifest['source_sha256'])))
    assets = project / 'Assets'
    for folder in ('Editor', 'Scripts', 'MapArt'):
        (assets / folder).mkdir(parents=True, exist_ok=True)
    for name, (namespace, fields) in STUBS.items():
        base = 'Pixelplacement.StateMachine' if name == 'MapPrefab' else 'MonoBehaviour'
        code = 'using UnityEngine;\n'
        if namespace:
            code += 'namespace ' + namespace + ' {\n'
        code += f'public class {name} : {base} {{ {fields} }}\n'
        if namespace:
            code += '}\n'
        (assets / 'Scripts' / (name + '.cs')).write_text(code)
    for name in ('PreviewProbe.cs', 'Editor/MapPrototypeBuilder.cs'):
        shutil.copyfile(HERE / 'map_unity' / name, assets / ('Scripts' if '/' not in name else '') / name)
    packages = project / 'Packages'
    packages.mkdir(exist_ok=True)
    (packages / 'manifest.json').write_text(json.dumps({'dependencies': {
        'com.unity.modules.physics2d': '1.0.0', 'com.unity.modules.physics': '1.0.0',
        'com.unity.modules.imageconversion': '1.0.0', 'com.unity.modules.jsonserialize': '1.0.0',
        'com.unity.modules.animation': '1.0.0',
        'com.unity.modules.particlesystem': '1.0.0',  # native map effects; without it bundles drop ParticleSystems
        'com.unity.modules.imgui': '1.0.0', 'com.unity.modules.assetbundle': '1.0.0'}}, indent=2))
    visual_data=None
    if visuals:
        visuals=visuals.resolve();visual_data=json.loads((visuals/'visuals.json').read_text())
        assert visual_data['source_sha256']==manifest['source_sha256']
        pages=visual_data.get('atlases',[dict(file='atlas.png',height=visual_data['atlas_height'])])
        for i,page in enumerate(pages):
            shutil.copyfile(visuals/page['file'],assets/'MapArt'/('layered_atlas'+('' if i==0 else '_'+str(i))+'.png'))
    if effects_bundle:
        # Native effect art comes from a live client bundle; its textures are extracted so the map's own
        # materials own them (a bundle cannot reference another bundle's assets).
        import UnityPy
        art=assets/'EffectArt';art.mkdir(exist_ok=True)
        for obj in UnityPy.load(str(effects_bundle)).objects:
            if obj.type.name=='Texture2D':
                texture=obj.read();texture.image.save(art/(texture.m_Name+'.png'))
    scale=json.loads(calibration.read_text()) if calibration else None
    inputs = []
    for cell in manifest['cells']:
        if cell['name'] not in cells:
            continue
        name = cell['name']
        if not visual_data:
            shutil.copyfile(source / 'snapshots' / name / 'snapshot.png', assets / 'MapArt' / (name + '.png'))
        polygons = lambda records: [dict(points=[dict(x=p[0], y=p[1]) for p in polygon])
                                    for record in records for polygon in record['polygons_px']]
        exits = []
        for exit in cell['exits']:
            if exit.get('target_map') or exit['target_cell'] not in cells or exit.get('conditional'):
                continue
            if exit.get('geometry_hold'):
                raise ValueError('Pilot exit geometry is held')
            exits.append(dict(name=exit['name'], targetCell=exit['target_cell'], targetPad=exit['target_pad'],
                              polygons=polygons([exit])))
        inputs.append(dict(name=name, image='Assets/MapArt/' + name + '.png',
                           sourceScale=scale['cells'][name]['entityScale'] if scale else cell['settings']['intScale'],
                           sourceSpeed=scale['cells'][name]['entitySpeed'] if scale else cell['settings']['intSpeed'],
                           spawns=[dict(name=s['name'], x=s['matrix'][4]/20, y=s['matrix'][5]/20) for s in cell['spawns']],
                           walkables=polygons(cell['walkables']), blockers=polygons(cell['blockers']), exits=exits))
    if {cell['name'] for cell in inputs} != set(cells):
        raise ValueError('Unknown or missing prototype cell')
    output = source / 'unity_build'
    output.mkdir(exist_ok=True)
    spec = dict(width=manifest['stage_px'][1]-manifest['stage_px'][0],
                height=manifest['stage_px'][3]-manifest['stage_px'][2], pixelsPerUnit=scale['pixelsPerUnit'] if scale else 20,
                outputDir=str(output), cells=inputs,layered=bool(visual_data),gpuCompression=gpu_compression,cameraZoom=scale.get('cameraZoom',0) if scale else 0)
    if visual_data:
        spec.update(atlas='Assets/MapArt/layered_atlas.png',atlasHeight=visual_data['atlas_height'],
                    visualFrames=visual_data['frames'],visualCells=visual_data['cells'],fps=visual_data['fps'],
                    sampleFrames=visual_data['samples'],rasterScale=visual_data.get('raster_scale',1),
                    visualAtlases=[dict(path='Assets/MapArt/layered_atlas'+('' if i==0 else '_'+str(i))+'.png',height=page['height']) for i,page in enumerate(pages)])
    if effects_bundle:
        spec.update(effectsBundle=str(effects_bundle.resolve()),effectArtDir='Assets/EffectArt')
    if scale:
        (source/'calibration.json').write_text(json.dumps(scale,indent=2))
    spec_path = project / 'map_input.json'
    spec_path.write_text(json.dumps(spec, indent=2))
    return spec_path


def verify_renders(source, cells):
    """A physics/build pass cannot conceal an empty or incorrect Unity render."""
    results = []
    for name in cells:
        with Image.open(source / 'snapshots' / name / 'snapshot.png') as image:
            rgba = image.convert('RGBA')
            # The Unity camera clears to opaque black. Compare both images over
            # that same background, rather than mistaking source translucency
            # for a renderer error or comparing straight versus premultiplied RGB.
            expected = np.asarray(Image.alpha_composite(Image.new('RGBA', rgba.size, (0, 0, 0, 255)), rgba), dtype=np.int16)
        with Image.open(source / 'unity_build' / (name + '-unity.png')) as image:
            actual = np.asarray(image.convert('RGBA'), dtype=np.int16)
        if actual.shape != expected.shape:
            raise ValueError('Unity capture dimensions changed for ' + name)
        error = np.abs(actual - expected)
        mean, p99 = float(error.mean()), float(np.percentile(error, 99))
        results.append(dict(cell=name, mean_error_255=mean, p99_error_255=p99, passed=mean <= 1 and p99 <= 4))
    report = dict(status='passed' if all(row['passed'] for row in results) else 'failed', cells=results,
                  comparison_background='opaque black, matching the Unity capture camera',
                  proof_boundary='Unity rendering versus the extracted still snapshots only; not Flash animation or gameplay parity.')
    (source / 'unity_build' / 'render_validation.json').write_text(json.dumps(report, indent=2))
    if report['status'] != 'passed':
        raise ValueError('Unity snapshot render comparison failed: ' + repr(results))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--cells', default='Enter,Garden1')
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--android-only', action='store_true', help='Build Android from the existing verified prefab')
    parser.add_argument('--visuals',type=Path)
    parser.add_argument('--calibration',type=Path)
    parser.add_argument('--gpu-compression',action='store_true',help='BC7 (Standalone) / ASTC 6x6 (Android) atlas pages')
    parser.add_argument('--effects-bundle',type=Path,help='Live client bundle supplying native effects (Skeletal Fire Mage)')
    args = parser.parse_args()
    spec = stage(args.source, args.project, args.cells.split(','),args.visuals,args.calibration,args.gpu_compression,args.effects_bundle)
    print('Isolated project:', args.project.resolve(), flush=True)
    if args.build or args.android_only:
        log = args.project.resolve() / ('android-build.log' if args.android_only else 'build.log')
        result = subprocess.run([str(UNITY), '-batchmode', '-quit', '-projectPath', str(args.project.resolve()),
                                 '-executeMethod', 'MapPrototypeBuilder.BuildAndroid' if args.android_only else 'MapPrototypeBuilder.Build', '-mapInput', str(spec),
                                 '-logFile', str(log)])
        print('Unity exit:', result.returncode, 'log:', log)
        if result.returncode == 0 and not args.android_only and not args.visuals:
            print(json.dumps(verify_renders(args.source.resolve(), args.cells.split(',')), indent=2))
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
