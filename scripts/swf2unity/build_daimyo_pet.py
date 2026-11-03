"""Compile Daimyo's labeled body states with an independent animated head clock.

This pilot accepts the audited source contract only. Idle's actual 289-frame
reset period is separately proved by source renders; no long clock is capped.
"""
import copy
import hashlib
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image

import render
import swfparse as sp
import rig
from armor import pack
from interactive_morph import hold
from trial_host import ROOT

OUT = ROOT/'outputs/battlepet_forge_lab/live/pet'
SOURCE = ROOT/'outputs/battlepet_forge_lab/sources/6673_ArmoredDaimyoBattle.swf'
RANGES = [('Walk',16,28),('Attack1',29,58),('Attack2',59,77),('Buff',78,101)]


def place(depth, state):
    return sp.Place(depth=depth, char=state['char'], matrix=state['matrix'], cxform=state['cxform'],
                    ratio=state['ratio'], clip_depth=state['clip'], blend=state['blend'],
                    filters_raw=state['filters_raw'], name=state['name'])


def segment(movie, start, end, side):
    original = movie.chars[54]
    lists = sp.display_lists(original.frames)
    def accepts(d,f):
        if side=='front':return d>41 or d==41 and bool(lists[f].get(41,{}).get('clip'))
        if d<41:return True
        h=lists[f].get(41,{})
        return d==41 and not h.get('clip') and not (h.get('char')==33 and not h.get('filters_raw'))
    tags = bytearray()
    for frame in range(start, end):
        if frame == start:
            for depth, state in sorted(lists[frame].items()):
                if accepts(depth,frame): tags += sp.encode_place(place(depth, state))
        else:
            for depth in original.frames[frame].removes:
                if accepts(depth,frame) or accepts(depth,frame-1): tags += sp.tag(28, struct.pack('<H',depth))
            if not accepts(41,frame) and accepts(41,frame-1):tags+=sp.tag(28,struct.pack('<H',41))
            for p in original.frames[frame].places:
                if accepts(p.depth,frame):
                    if p.depth==41 and not accepts(41,frame-1):tags+=sp.encode_place(place(41,lists[frame][41]))
                    else:tags += sp.encode_place(p)
        tags += sp.tag(1,b'')
    cid = max(movie.chars)+1
    raw = sp.define_sprite(cid,end-start,bytes(tags))
    movie.chars[cid] = sp.Character(cid,'sprite',39,frames=sp.parse_timeline(render.tag_body(raw),4,len(render.tag_body(raw))),raw=raw)
    movie.raw_defs.append(raw)
    return cid


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    survey=json.loads((OUT.parent.parent/'source_survey.json').read_text())
    assert source_hash==next(s['sha256'] for s in survey['inspected'] if s['item_id']==6673)
    probe=json.loads((OUT.parent/'idle_period_probe.json').read_text())
    assert all(p.get('max')==0 and p['same_geometry'] for p in probe['pairs'].values())
    m=sp.load(str(SOURCE));assert m.fps==24
    dl=sp.display_lists(m.chars[54].frames)
    # The only independent root leaf is the same head instance at depth 41.
    for _,start,end in RANGES:
        for f in range(start,end):
            head=dl[f].get(41)
            if head:
                assert head['char'] in (33,38,51) and head['cxform'] is None
                assert not head['clip'] or head['char']==51
            assert all(d>=41 for d,s in dl[f].items() if s['clip'])
    idle=copy.deepcopy(m);hold(idle,54,7)
    idle_result=render.render_jobs(idle,{},[dict(items=[dict(char=54)],frames=289)],2.,str(OUT/'render_idle'))[0]
    results={'Idle':idle_result};states=[]
    for cid in (33,):
        period=rig.Rig(m).period(cid);assert period<=126
        results['Head'+str(cid)]=render.render_jobs(m,{},[dict(items=[dict(char=cid)],frames=period)],2.,str(OUT/('render_head'+str(cid))))[0]
    for name,start,end in RANGES:
        for side in ['back','front']:
            cid=segment(m,start,end,side)
            results[name+'_'+side]=render.render_jobs(m,{},[dict(items=[dict(char=cid)],frames=end-start)],2.,str(OUT/('render_'+name+'_'+side)))[0]
        poses=[list(dl[f].get(41,{}).get('matrix') or render.IDENT) for f in range(start,end)]
        chars=[33 if dl[f].get(41,{}).get('char')==33 and not dl[f][41]['filters_raw'] else -1 for f in range(start,end)]
        tokens=[dl[f].get(41,{}).get('inst',-1) for f in range(start,end)]
        states.append(dict(name=name,frames=end-start,headMatrices=[dict(v=p) for p in poses],headChars=chars,headTokens=tokens))
    # Crop per frame, retaining its source-space origin. Fully transparent frames
    # still have a 1x1 transparent sprite and preserve the declared clock length.
    images=[];groups={}
    for name,result in results.items():
        indices=[]
        for i in range(len(result['images'])):
            image=result['images'][i]
            box=image.getchannel('A').getbbox() or (0,0,1,1)
            origin=[result['origins'][i][0]+box[0]*result['twips_per_px'],result['origins'][i][1]+box[1]*result['twips_per_px']]
            im=image.crop(box)
            indices.append(len(images));images.append((im,origin))
        groups[name]=indices
    positions,height=pack([(im.width,im.height) for im,_ in images],4096)
    assert positions is not None and height<=4096
    atlas=Image.new('RGBA',(4096,(-(-height//4))*4));sprites=[]
    for (im,origin),(x,y) in zip(images,positions):
        atlas.paste(im,(x,y))
        sprites.append(dict(rect=[x,atlas.height-y-im.height,im.width,im.height],origin=origin,tpp=10.))
    atlas.save(OUT/'atlas.png')
    data=dict(name='ArmoredDaimyo_Battle',fps=24,twipToUnit=6.1/2000,
              atlasWidth=atlas.width,atlasHeight=atlas.height,sprites=sprites,
              groups=[dict(name=k,indices=v) for k,v in groups.items()],states=states,
              sourceSha256=source_hash,idlePeriod=289,headPeriod=126)
    (OUT/'pet.json').write_text(json.dumps(data,separators=(',',':')))
    print(json.dumps(dict(status='prepared',images=len(images),atlas=atlas.size,source_sha256=source_hash)),flush=True)
    validate(m,results,states,data)


def composite(result, frame, canvas, origin, zoom=2., matrix=None):
    """Independent CPU source-space compositor, used for reference comparison."""
    im=result['images'][frame];ox,oy=result['origins'][frame];tpp=result['twips_per_px']
    if matrix is None:
        canvas.alpha_composite(im,(round((ox-origin[0])/tpp),round((oy-origin[1])/tpp)))
    else:
        # PIL output->input affine map, keeping Flash's y-down coordinates.
        inv=render.mat_inv(matrix);a,b,c,d,tx,ty=inv
        coeff=(a,c,(a*origin[0]+c*origin[1]+tx-ox)/tpp,
               b,d,(b*origin[0]+d*origin[1]+ty-oy)/tpp)
        transformed=im.transform(canvas.size,Image.Transform.AFFINE,coeff,Image.Resampling.BICUBIC)
        canvas.alpha_composite(transformed)


def validate(movie,results,states,data):
    comparisons=[]
    for state in states:
        name=state['name'];start=next(s for n,s,e in RANGES if n==name)
        # Render the source's complete state, including the independently timed
        # head, then compare our body/head/front composition at every root frame.
        full=copy.deepcopy(movie)
        # The source reference begins at the label with a fresh head instance.
        cid=segment(full,start,start+state['frames'],'back')
        original=full.chars[54];lists=sp.display_lists(original.frames);tags=bytearray()
        for f in range(start,start+state['frames']):
            if f==start:
                for d,s in sorted(lists[f].items()): tags+=sp.encode_place(place(d,s))
            else:
                for d in original.frames[f].removes:tags+=sp.tag(28,struct.pack('<H',d))
                for p in original.frames[f].places:tags+=sp.encode_place(p)
            tags+=sp.tag(1,b'')
        rid=max(full.chars)+1;raw=sp.define_sprite(rid,state['frames'],bytes(tags))
        full.chars[rid]=sp.Character(rid,'sprite',39,frames=sp.parse_timeline(render.tag_body(raw),4,len(render.tag_body(raw))),raw=raw);full.raw_defs.append(raw)
        reference=render.render_jobs(full,{},[dict(items=[dict(char=rid)],frames=state['frames'])],2.,str(OUT/('reference_'+name)))[0]
        previous=-1;age=0
        for f in range(state['frames']):
            ref=reference['images'][f];origin=reference['origins'][f]
            canvas=Image.new('RGBA',ref.size)
            composite(results[name+'_back'],f,canvas,origin)
            cid=state['headChars'][f]
            if cid!=previous:age=0
            if cid>=0:
                head=results['Head'+str(cid)]
                composite(head,age%len(head['images']),canvas,origin,matrix=state['headMatrices'][f]['v'])
            previous=cid;age+=1
            composite(results[name+'_front'],f,canvas,origin)
            a=np.asarray(ref).astype(float);b=np.asarray(canvas).astype(float)
            # Compare premultiplied channels so invisible RGB is irrelevant.
            a[:,:,:3]*=a[:,:,3:4]/255;b[:,:,:3]*=b[:,:,3:4]/255
            error=float(np.abs(a-b).mean())
            comparisons.append(dict(state=name,frame=f,mean=error))
    worst=max(c['mean'] for c in comparisons)
    result=dict(status='passed' if worst<=25 else 'failed',worst_mean=worst,comparisons=comparisons,
                idle_period_probe='Actual source loop-boundary equality across 289/578/867, not a capped LCM.')
    (OUT/'source_validation.json').write_text(json.dumps(result,indent=2))
    assert result['status']=='passed',result
    print(json.dumps(dict(status='source_passed',worst_mean=worst)),flush=True)


if __name__=='__main__':main()
