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


def layer_pixels(visual_data, visuals, layer, box):
    """RGBA pixels (raster density) of one exported layer inside a source-px box (x0, y0, x1, y1)."""
    rs=visual_data['raster_scale'];frame=visual_data['frames'][layer['frames'][0]]
    pages=[np.asarray(Image.open(visuals/p['file']).convert('RGBA')) for p in visual_data['atlases']]
    left=round((box[0]-layer['x'])*rs+frame['w']/2);top=round((box[1]-layer['y'])*rs+frame['h']/2)
    w,h=round((box[2]-box[0])*rs),round((box[3]-box[1])*rs);out=np.zeros((h,w,4),np.uint8)
    pieces=frame.get('tiles') or [dict(page=frame['page'],x=frame['x'],y=frame['y'],w=frame['w'],h=frame['h'],ox=0,oy=0)]
    for t in pieces:
        x0,y0=max(left,t['ox']),max(top,t['oy']);x1,y1=min(left+w,t['ox']+t['w']),min(top+h,t['oy']+t['h'])
        if x1>x0 and y1>y0:
            out[y0-top:y1-top,x0-left:x1-left]=pages[t['page']][t['y']+y0-t['oy']:t['y']+y1-t['oy'],t['x']+x0-t['ox']:t['x']+x1-t['ox']]
    return out


def socket_mask(visual_data, visuals, cell, effect, out_png):
    """The eye socket's own dark interior in the art below the effect, pulled in from the rim: a sprite mask
    so the glow shows only through the hole, as if it came from inside the skull."""
    from scipy import ndimage
    layer=next(l for l in cell['layers'] if l['source_path']==effect['after'])
    r=effect['size'];box=(effect['x']-r,effect['y']-r,effect['x']+r,effect['y']+r)
    pixels=layer_pixels(visual_data,visuals,layer,box);rs=visual_data['raster_scale']
    dark=(pixels[...,:3].max(2)<20)&(pixels[...,3]>200)
    labels,_=ndimage.label(dark);cy,cx=pixels.shape[0]//2,pixels.shape[1]//2
    ys,xs=np.nonzero(labels)
    if not len(ys):raise ValueError('No dark socket under effect at '+str((effect['x'],effect['y'])))
    nearest=np.argmin((ys-cy)**2+(xs-cx)**2);hole=labels==labels[ys[nearest],xs[nearest]]
    hole=ndimage.binary_erosion(hole,iterations=max(1,round(rs)))
    alpha=ndimage.gaussian_filter(hole.astype(float),rs*.6)
    image=np.zeros(pixels.shape,np.uint8);image[...,:3]=255;image[...,3]=np.clip(alpha*255*1.4,0,255).astype(np.uint8)
    Image.fromarray(image).save(out_png)
    return dict(mask_px=int(hole.sum()),box=box)


def stage(source, project, cells, visuals=None, calibration=None, gpu_compression=False, effects_bundle=None,
          effect_sources=(), bat_sheet=None):
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
    sources=([('firemage',Path(effects_bundle))] if effects_bundle else [])+[(n,Path(p)) for n,p in effect_sources]
    if sources:
        # Native effect art comes from live client bundles; their textures are extracted (one folder per source)
        # so the map's own materials own them: a bundle cannot reference another bundle's assets.
        import UnityPy
        for name,path in sources:
            art=assets/'EffectArt'/name;art.mkdir(parents=True,exist_ok=True)
            for obj in UnityPy.load(str(path)).objects:
                if obj.type.name=='Texture2D':
                    texture=obj.read();texture.image.save(art/(texture.m_Name+'.png'))
    bat=None
    if bat_sheet:
        bat=json.loads(Path(bat_sheet).with_suffix('.json').read_text())
        (assets/'EffectArt'/'bat').mkdir(parents=True,exist_ok=True)
        shutil.copyfile(bat_sheet,assets/'EffectArt'/'bat'/'BatFlap.png')
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
    if sources:
        spec.update(effectSources=[dict(name=n,path=str(p.resolve())) for n,p in sources],effectArtDir='Assets/EffectArt')
    if bat:
        spec.update(batCols=bat['cols'],batRows=bat['rows'],batFrames=bat['frames'])
    if visual_data:
        masks=assets/'EffectArt'/'masks';masks.mkdir(parents=True,exist_ok=True)
        for cell in visual_data['cells']:
            for i,effect in enumerate(cell.get('effects',[])):
                if effect['kind']=='dracolich_eye':
                    name=cell['name']+'_eye_'+str(i)+'.png';info=socket_mask(visual_data,visuals,cell,effect,masks/name)
                    if info['mask_px']<20:raise ValueError('Eye socket mask too small: '+str(info))
                    effect['mask']='Assets/EffectArt/masks/'+name
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
    parser.add_argument('--effect-source',action='append',default=[],metavar='NAME=BUNDLE',help='Extra effect bundle, e.g. dracolich=...')
    parser.add_argument('--bat-sheet',type=Path,help='bake_flipbook.py sheet for the sky bats (with its .json)')
    args = parser.parse_args()
    spec = stage(args.source, args.project, args.cells.split(','),args.visuals,args.calibration,args.gpu_compression,args.effects_bundle,
                 [tuple(v.split('=',1)) for v in args.effect_source],args.bat_sheet)
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
