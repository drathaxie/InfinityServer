"""Rebuild the two accepted armor trials with a native skin-customizable face kit.

Only face pixels, mask, rect and pivot change; body images, rigs and clocks are retained.
Existing conversions remain immutable evidence. Publication is a separate guarded step.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import numpy as np
from PIL import Image
from armor import pot_nearest
from trial_host import ROOT
from validate_armor import composite, COLOR

BASE = ROOT / 'outputs/flash_converter_trial/skin_fix'
TEMPLATE = BASE / 'template'


def main():
    facekit = json.loads((TEMPLATE / 'template.json').read_text())
    trials = [('CryoChronomancer', 2000002, ROOT / 'outputs/flash_converter_trial/CryoChronomancer/evidence/rgb_cc_build/armor',
               '2000002_chronofrostmage_flash.unity3d'),
              ('ChronoStriker', 2000005, ROOT / 'outputs/flash_converter_trial/batch02/ChronoStriker/conversion',
               'chronostriker_batch02_flash_trial.unity3d')]
    plan = []
    for key, iid, original, filename in trials:
        folder = BASE / key
        out = folder / 'conversion'
        shutil.copytree(original, out, dirs_exist_ok=True)
        data = json.loads((out / 'armor.json').read_text())
        # Cryo predates animated image rects. Its constant rect is equivalent to
        # the four additional keyed values used by the current builder.
        for gd in data['genders'].values():
            for renderer in gd['renderers'].values():
                for layer in renderer.get('layers') or []:
                    if all(len(k) == 14 for k in layer['keys']):
                        layer['keys'] = [k + layer['rect'] for k in layer['keys']]
        checks = []
        for g, gd in data['genders'].items():
            atlas = np.asarray(Image.open(out / gd['atlas']).convert('RGBA')).copy()
            for slot, face in facekit[g]['face'].items():
                s = next(s for s in gd['sprites'] if s['name'] == gd['renderers'][slot]['sprite'])
                x, y, oldw, oldh = s['rect']
                top = atlas.shape[0] - y - oldh
                image = np.asarray(Image.open(TEMPLATE / face['png']).convert('RGBA'))
                h, w = image.shape[:2]
                assert w <= oldw and h <= oldh, 'replacement must fit existing face allocation'
                atlas[top:top+oldh, x:x+oldw] = 0
                atlas[top:top+h, x:x+w] = image
                s.update(rect=[x, atlas.shape[0]-top-h, w, h], pil=[x, top, w, h],
                         pivot=face['pivot'], ppu=face['ppu'])
                if 'mask_png' in face:
                    mask = Image.open(TEMPLATE / face['mask_png']).convert('RGB')
                    target = out / (g + '_masks/face_' + slot + '_mask.png')
                    target.parent.mkdir(exist_ok=True)
                    mask.resize((pot_nearest(w), pot_nearest(h)), Image.Resampling.BILINEAR).save(target)
                    s['mask'] = target.relative_to(out).as_posix()
            Image.fromarray(atlas).save(out / gd['atlas'])
            head = next(s for s in gd['sprites'] if s['name'] == gd['renderers']['head']['sprite'])
            assert head['mask'] and any(e['slot'] == 23 and e['sprite'] == head['name'] for e in gd['itemslots'])
            outputs = []
            for label, skin in [('dark',(45,28,20)), ('light',(240,200,160)), ('blue',(40,100,220))]:
                colors = dict(COLOR, Skin=np.array(skin,np.float32)/255)
                colored = composite(atlas, str(out), gd, colors)
                x,y,w,h = head['rect']; top = atlas.shape[0]-y-h
                image = colored[top:top+h,x:x+w]
                outputs.append(image)
                Image.fromarray(np.rint(image*255).astype(np.uint8)).save(folder / (g+'_skin_'+label+'.png'))
            assert np.max(np.abs(outputs[0][...,:3]-outputs[1][...,:3])) > .2
            assert np.array_equal(outputs[0][...,3],outputs[1][...,3])
            checks.append(dict(gender=g, skin_mask=head['mask'], changing_skin_changes_face=True, alpha_preserved=True))
        (out / 'armor.json').write_text(json.dumps(data,indent=1))
        (folder / 'skin_regression.json').write_text(json.dumps(checks,indent=2))
        cmd = [sys.executable,str(ROOT/'scripts/swf2unity/build_armor.py'),str(out),str(TEMPLATE),
               '--bundle',filename,'--project',str(ROOT/'outputs/flash_converter_trial/FlyingMoglin/UnityProject'),
               '--output-dir',str(folder/'bundle'),'--validate','0,19,199,1099,1100,2199,2200']
        print('BUILD',key,flush=True)
        r = subprocess.run(cmd,capture_output=True,text=True)
        (folder/'build.log').write_text(r.stdout+r.stderr)
        if r.returncode: raise RuntimeError(r.stdout[-2000:]+r.stderr[-2000:])
        print(r.stdout[-700:],flush=True)
        plan.append(dict(key=key,item_id=iid,bundle=str(folder/'bundle'/filename)))
        (BASE/'repair_plan.json').write_text(json.dumps(plan,indent=2))


if __name__ == '__main__': main()
