"""Compare loaded native bundle captures with independent atlas composition."""
import json
import numpy as np
from PIL import Image
from trial_host import ROOT
import render


def main():
    lab=ROOT/'outputs/battlepet_forge_lab/live';data=json.loads((lab/'pet/pet.json').read_text())
    report=json.loads((lab/'bundle/native_pet_contract.json').read_text())
    atlas=Image.open(lab/'pet/atlas.png').convert('RGBA');groups={g['name']:g['indices'] for g in data['groups']};states={s['name']:s for s in data['states']};rows=[]
    for cap in report['captures']:
        name,t=cap['state'],cap['ticks'];matrix=None
        if name=='Idle':indices=[groups['Idle'][t%289],-1,-1]
        else:
            s=states[name];f=t%s['frames'];age=0;prev=(s['headChars'][0],s['headTokens'][0])
            for tick in range(1,t+1):
                frame=tick%s['frames'];token=(s['headChars'][frame],s['headTokens'][frame])
                age=0 if token!=prev else (age+1)%126;prev=token
            indices=[groups[name+'_back'][f],groups['Head33'][age] if s['headChars'][f]>=0 else -1,groups[name+'_front'][f]]
            matrix=s['headMatrices'][f]['v']
        canvas=Image.new('RGBA',(cap['width'],cap['height']))
        for layer,index in enumerate(indices):
            if index<0:continue
            spr=data['sprites'][index];x,y,w,h=spr['rect'];image=atlas.crop((x,data['atlasHeight']-y-h,x+w,data['atlasHeight']-y))
            inv=render.mat_inv(tuple(matrix)) if layer==1 else render.IDENT
            a,b,c,d,tx,ty=inv;ox,oy=spr['origin'];outx,outy=cap['origin'];tpp=cap['tpp']
            coeff=(a,c,(a*outx+c*outy+tx-ox)/tpp,b,d,(b*outx+d*outy+ty-oy)/tpp)
            transformed=image.transform(canvas.size,Image.Transform.AFFINE,coeff,Image.Resampling.BILINEAR)
            canvas.alpha_composite(transformed)
        actual=Image.open(lab/'bundle'/cap['file']).convert('RGBA')
        assert actual.size==canvas.size
        aa=np.asarray(actual).astype(float);bb=np.asarray(canvas).astype(float)
        # Sprites/Default writes premultiplied RGB into the transparent render
        # target (Blend One OneMinusSrcAlpha). PIL's compositor stores straight
        # RGB; premultiply that side exactly once before comparison.
        bb[:,:,:3]*=bb[:,:,3:4]/255
        error=np.abs(aa-bb);mean=float(error.mean());p99=float(np.quantile(error,.99))
        rows.append(dict(state=name,ticks=t,mean=mean,p99=p99))
        canvas.save(lab/'bundle'/('cpu_'+cap['file']))
    worst=max(r['mean'] for r in rows);p99=max(r['p99'] for r in rows)
    result=dict(status='passed' if worst<=2 and p99<=24 else 'failed',worst_mean=worst,worst_p99=p99,captures=rows)
    (lab/'bundle/gpu_validation.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='captures'}))
    assert result['status']=='passed'


if __name__=='__main__':main()
