"""Check native face/body skin agreement and opaque silhouette interiors."""
import json
import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion, binary_fill_holes
from trial_host import ROOT
from validate_armor import composite, COLOR

BASE = ROOT/'outputs/flash_converter_trial/body_limb_fix'

def main():
    p=BASE/'ChronomancerPirate/conversion'
    data=json.loads((p/'armor.json').read_text());checks=[]
    for gender,g in data['genders'].items():
        atlas=np.asarray(Image.open(p/g['atlas']).convert('RGBA'));samples={}
        for slot in ['head','frontshoulder']:
            s=next(s for s in g['sprites'] if s['name']==g['renderers'][slot]['sprite'])
            x,y,w,h=s['rect'];top=atlas.shape[0]-y-h
            mask=np.asarray(Image.open(p/s['mask']).convert('RGB').resize((w,h),Image.Resampling.BILINEAR))
            rgba=atlas[top:top+h,x:x+w]
            pure=(mask[:,:,0]>250)&(abs(mask[:,:,1].astype(int)-128)<3)&(abs(mask[:,:,2].astype(int)-128)<3)&(rgba[:,:,3]==255)&(rgba[:,:,0]==165)
            assert pure.sum()>100,(gender,slot,pure.sum())
            samples[slot]=(top,x,h,w,pure)
        for name,skin in [('dark',(45,28,20)),('light',(240,200,160)),('blue',(40,100,220))]:
            dest=composite(atlas,str(p),g,dict(COLOR,Skin=np.array(skin,np.float32)/255));colors={}
            for slot,(top,x,h,w,pure) in samples.items():
                colors[slot]=np.median(dest[top:top+h,x:x+w,:3][pure],axis=0)
            error=float(abs(colors['head']-colors['frontshoulder']).max())
            assert error<=2/255,(gender,name,colors)
            checks.append(dict(gender=gender,skin=name,face_body_max_error=error))
    p=BASE/'ChronoStriker/conversion'
    data=json.loads((p/'armor.json').read_text());backs=[]
    for gender,g in data['genders'].items():
        atlas=np.asarray(Image.open(p/g['atlas']).convert('RGBA'))
        for s in g['sprites']:
            if not s['name'].endswith('_silhouette'):continue
            x,y,w,h=s['rect'];a=atlas[atlas.shape[0]-y-h:atlas.shape[0]-y,x:x+w,3]
            # Exclude the two-pixel antialiased outer boundary.
            inside=binary_erosion(binary_fill_holes(a>8),iterations=2)
            assert np.all(a[inside]==255),s['name']
            backs.append(dict(gender=gender,sprite=s['name'],opaque_interior_pixels=int(inside.sum())))
        for slot in ['backfoot','backshin']:
            s=next(s for s in g['sprites'] if s['name']==g['renderers'][slot]['sprite'])
            x,y,w,h=s['rect'];im=Image.fromarray(atlas[atlas.shape[0]-y-h:atlas.shape[0]-y,x:x+w])
            bg=Image.new('RGBA',im.size,'#ddb899');bg.alpha_composite(im)
            bg.save(p.parent/(gender+'_'+slot+'_base.png'))
    result=dict(status='passed',skin_checks=checks,back_layer_checks=backs,
        scope='Native-gradient face/body agreement and silhouette alpha; live visual confirmation remains separate')
    (BASE/'regression_checks.json').write_text(json.dumps(result,indent=2))
    print('Passed',len(checks),'skin palettes and',len(backs),'back layers')

if __name__=='__main__':main()
