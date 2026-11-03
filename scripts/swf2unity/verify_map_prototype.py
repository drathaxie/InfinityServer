"""Independently read the built map bundle and compare it to source geometry."""
import argparse
import json
from pathlib import Path

import numpy as np

from inspect_map_bundle import inspect
from build_map_prototype import verify_renders


def verify(source, cells=('Enter', 'Garden1')):
    manifest = json.loads((source / 'map_source.json').read_text())
    calibration=json.loads((source/'calibration.json').read_text()) if (source/'calibration.json').exists() else None
    ppu=calibration['pixelsPerUnit'] if calibration else 20
    bundle = source / 'unity_build' / 'map-shadowfall.prototype.unity3d'
    audit = inspect(bundle)
    if audit['read_failures']:
        raise ValueError('Serialized map audit has read failures')
    expected_cells = {c['name']: c for c in manifest['cells'] if c['name'] in cells}
    actual_cells = {c['path'].split('/')[-1]: c for c in audit['cells']}
    if set(actual_cells) != set(expected_cells):
        raise ValueError('Built cell names differ from source selection')
    allowed = {'MapPrefab', 'MapCell', 'MapSpawn', 'MapGoToCell', 'ClickableWalk',
               'MapBlocker', 'Initialization', 'State', 'ZOffset', 'BGParallax'}
    scripts = {c['script'] for node in audit['nodes'] for c in node['components'] if 'script' in c}
    if not scripts <= allowed:
        raise ValueError('Unexpected runtime scripts in map bundle: ' + str(scripts - allowed))
    for node in audit['nodes']:
        for component in node['components']:
            if 'script' not in component:
                continue
            namespace = 'Pixelplacement' if component['script'] in ('State', 'Initialization') else ''
            if component['assembly'] != 'Assembly-CSharp' or component['namespace'] != namespace:
                raise ValueError('Native script binding changed: ' + node['path'])
    checks = []
    for name, cell in expected_cells.items():
        native = actual_cells[name]
        spawns = {p['path'].split('/')[-1]: p['world_position'][:2] for p in native['components']['MapSpawn']}
        for pad in cell['spawns']:
            expected = [pad['matrix'][4] / (20*ppu), -pad['matrix'][5] / (20*ppu)]
            if not np.allclose(spawns[pad['name']], expected, atol=1e-5, rtol=0):
                raise ValueError('Serialized spawn moved: ' + name + '/' + pad['name'])
        for kind, path_part in (('walkables', '/Walkable/'), ('blockers', '/Blockers/'), ('exits', '/Exits/')):
            records = cell[kind]
            if kind == 'exits':
                records = [r for r in records if not r.get('target_map') and not r.get('conditional') and r['target_cell'] in cells]
            expected = [np.array([[x/ppu, -y/ppu] for x, y in polygon]) for record in records
                        for polygon in record['polygons_px']]
            actual = []
            for node in audit['nodes']:
                if not node['path'].startswith(native['path'] + path_part):
                    continue
                for component in node['components']:
                    if component['type'] == 'PolygonCollider2D':
                        actual += [np.array([[p['x'], p['y']] for p in polygon])
                                   for polygon in component['fields']['m_Points']['m_Paths']]
            unmatched = list(actual)
            for polygon in expected:
                index = next((i for i, candidate in enumerate(unmatched)
                              if polygon.shape == candidate.shape and np.allclose(polygon, candidate, atol=1e-5, rtol=0)), None)
                if index is None:
                    raise ValueError('Source polygon was changed or omitted: ' + name + '/' + kind)
                unmatched.pop(index)
            if unmatched:
                raise ValueError('Extra collider polygons in ' + name)
            checks.append(dict(cell=name, kind=kind, polygons=len(actual), passed=True))
        expected_exits = {(e['target_cell'], e['target_pad']) for e in cell['exits']
                          if not e.get('target_map') and not e.get('conditional') and e['target_cell'] in cells}
        actual_exits = {(e['fields']['TargetCell'], e['fields']['TargetSpawnPad'])
                        for e in native['components'].get('MapGoToCell', [])}
        if actual_exits != expected_exits:
            raise ValueError('Serialized exit graph differs from pilot selection')
        for renderer in native['components']['SpriteRenderer']:
            if not renderer.get('sprite'):
                raise ValueError('Map renderer has no sprite: ' + name)
        if calibration:
            fields=native['settings'][0]['fields']
            for key in ('entityScale','entitySpeed'):
                if not np.isclose(fields[key],calibration['cells'][name][key],atol=1e-6):
                    raise ValueError('Native calibration field differs: '+name+'/'+key)
            if calibration.get('cameraZoom') and not np.isclose(fields['zoom'],calibration['cameraZoom']):
                raise ValueError('Native camera zoom differs: '+name)
            if calibration.get('cameraZoom'):
                visual=json.loads((source/'visuals/visuals.json').read_text())
                groups=next(c['groups'] for c in visual['cells'] if c['name']==name)
                depth=[g for g in groups if g['kind'] in ('depth','actor')]
                z=native['components'].get('ZOffset',[])
                assert len(z)==len(depth)
                for component in z:
                    group=next(g for g in depth if component['path'].endswith('/'+g['name']))
                    assert np.allclose(component['world_position'][:2],[group['x']/ppu,-group['y']/ppu],atol=1e-5)
                    assert not component['fields']['IsStatic'] and component['fields']['yModifier']==500
                    assert component['fields']['rootTransform']['m_PathID'] and component['fields']['sortingGroup']['m_PathID']
                bg=native['components']['BGParallax'];assert len(bg)==1
                assert [l['Speed'] for l in bg[0]['fields']['levels']]==[0]+[g['speed'] for g in groups if g['kind']=='parallax']
                assert all(l['Layer']['m_PathID'] for l in bg[0]['fields']['levels'])
    if (source/'visuals/visuals.json').exists():
        renders=verify_layered_renders(source,cells)
    else:renders = verify_renders(source, cells)
    physics = json.loads((source / 'unity_build' / 'unity_validation.json').read_text())
    if physics['status'] != 'passed' or not all(c['passed'] for c in physics['checks']):
        raise ValueError('Unity physics/transition validation failed')
    report = dict(status='passed', source_sha256=manifest['source_sha256'], bundle_sha256=audit['sha256'],
                  cells=list(cells), geometry=checks, render=renders,
                  transition_checks=[c for c in physics['checks'] if c['kind'] == 'transition'],
                  proof_boundary=physics['proofBoundary'])
    if calibration:report['calibration']=calibration
    animation_checks=[c for c in physics['checks'] if c['kind']=='loaded_animator_frame']
    if animation_checks:report['loaded_animator_checks']=dict(passed=len(animation_checks),failed=0)
    presentation=[c for c in physics['checks'] if c['kind'] in ('depth_front_behind','camera_horizontal_travel','parallax_relative_motion','parallax_ground_fixed','dragon_background_binding','npc_depth_binding','npc_dragon_opaque_pixels','npc_dragon_overlap_exercised')]
    if presentation:report['presentation_checks']=presentation
    visual=json.loads((source/'visuals/visuals.json').read_text()) if (source/'visuals/visuals.json').exists() else {}
    if visual.get('raster_scale',1)>1:
        import UnityPy
        texture_checks=[]
        for obj in UnityPy.load(str(bundle)).objects:
            if obj.type.name!='Texture2D':continue
            tree=obj.read_typetree()
            assert tree['m_TextureSettings']['m_FilterMode']==1 and not tree['m_IsReadable']
            texture_checks.append(dict(name=tree['m_Name'],width=tree['m_Width'],height=tree['m_Height'],filter='Bilinear',cpu_copy_retained=False))
        assert sorted((t['width'],t['height']) for t in texture_checks)==sorted((p['width'],p['height']) for p in visual['atlases'])
        assert all(np.isclose(r['pixels_per_unit'],ppu*visual['raster_scale']) for c in audit['cells'] for r in c['components']['SpriteRenderer'])
        report['raster_quality']=dict(source_raster_scale=visual['raster_scale'],texture_bytes=visual['texture_bytes'],textures=texture_checks)
    (source / 'unity_build' / 'serialized_audit.json').write_text(json.dumps(audit, indent=2))
    (source / 'unity_build' / 'prototype_verification.json').write_text(json.dumps(report, indent=2))
    return report


def verify_layered_renders(source,cells):
    from PIL import Image
    visuals=json.loads((source/'visuals/visuals.json').read_text())
    results=[]
    for name in cells:
        for frame in visuals['samples']:
            with Image.open(source/'visuals/references'/name/(str(frame)+'.png')) as im:
                rgba=im.convert('RGBA')
                expected=np.asarray(Image.alpha_composite(Image.new('RGBA',rgba.size,(0,0,0,255)),rgba),dtype=np.int16)
            with Image.open(source/'unity_build'/(name+'-frame-'+str(frame)+'-unity.png')) as im:
                actual=np.asarray(im.convert('RGBA'),dtype=np.int16)
            if actual.shape!=expected.shape:raise ValueError('Animation reference dimensions changed')
            error=np.abs(expected-actual);mean=float(error.mean());p99=float(np.percentile(error,99))
            results.append(dict(cell=name,frame=frame,mean_error_255=mean,p99_error_255=p99,passed=mean<=2 and p99<=16))
    result=dict(status='passed' if all(r['passed'] for r in results) else 'failed',frames=results,
        limits=dict(mean_error_255=2,p99_error_255=16),background='opaque black',
        proof_boundary='Layered Unity frames against independent source timeline samples; stateful gameplay scripts remain uncompiled.')
    if any(c.get('groups') for c in visuals['cells']):
        result['capture_mode']='Original source painter order for artwork comparison; player-relative depth and camera/parallax checked separately.'
    (source/'unity_build/render_validation.json').write_text(json.dumps(result,indent=2))
    if result['status']!='passed':raise ValueError('Layered map render mismatch: '+str([r for r in results if not r['passed']]))
    return result


def verify_android(source):
    """Compare built platform geometry, sprite references, clips and atlas pixels."""
    import hashlib
    import UnityPy
    windows=source/'unity_build/map-shadowfall.prototype.unity3d'
    android=source/'unity_build/android/map-shadowfall.prototype.unity3d'
    audits=[inspect(p) for p in (windows,android)]
    def scrub(value):
        if isinstance(value,dict):
            return {k:scrub(v) for k,v in value.items() if k not in ('path_id','m_PathID')}
        if isinstance(value,list):return [scrub(v) for v in value]
        return value
    assert all(not a['read_failures'] for a in audits)
    assert scrub(audits[0]['nodes'])==scrub(audits[1]['nodes']),'Platform map geometry/bindings differ'
    signatures=[]
    for path in (windows,android):
        objects=list(UnityPy.load(str(path)).objects);lookup={o.path_id:o for o in objects}
        def normalize(value):
            if isinstance(value,dict):
                if set(value)=={'m_FileID','m_PathID'}:
                    target=lookup.get(value['m_PathID'])
                    if not target:return None
                    tree=target.read_typetree()
                    return [target.type.name,tree.get('m_Name','')]
                return {k:normalize(v) for k,v in value.items()}
            if isinstance(value,list):return [normalize(v) for v in value]
            return value
        clips={};textures={}
        for obj in objects:
            if obj.type.name=='AnimationClip':
                tree=obj.read_typetree();clips[tree['m_Name']]=normalize(tree)
            elif obj.type.name=='Texture2D':
                texture=obj.read();im=texture.image.convert('RGBA')
                textures[texture.m_Name]=dict(size=im.size,sha256=hashlib.sha256(im.tobytes()).hexdigest())
        signatures.append(dict(clips=clips,textures=textures))
    assert signatures[0]==signatures[1],'Platform animation/atlas data differ'
    result=dict(status='passed',windows_sha256=audits[0]['sha256'],android_sha256=audits[1]['sha256'],
                clips=len(signatures[0]['clips']),textures=signatures[0]['textures'],device_render_verified=False)
    (source/'unity_build/android/platform_verification.json').write_text(json.dumps(result,indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--android',action='store_true')
    args = parser.parse_args()
    print(json.dumps(verify_android(args.source.resolve()) if args.android else verify(args.source.resolve()), indent=2))
