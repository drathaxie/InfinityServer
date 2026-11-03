"""Split map artwork into static bands and independently looping source clips.

No new runtime script is required: the Unity builder uses SpriteRenderers and
native Animator sprite curves. Stateful gameplay scripts remain explicit holds.
"""
import argparse
import copy
import hashlib
import json
import math
import re
import struct
from pathlib import Path

import numpy as np
from PIL import Image

import swfparse as sp
import render
from ccscripts import ffdec
from interactive_morph import functions


SAMPLES = [0, 1, 5, 10, 11, 24, 85, 86, 87, 171, 174]
# Static layers are stored as sparse payload tiles. Each cell keeps a one-texel border of the real neighbouring
# pixels, so bilinear sampling at a tile edge reads what the whole image would have, and adjacent tiles meet
# seamlessly. Cells and slots are multiples of BLOCK (lcm of BC7 4x4 and ASTC 6x6), so no compression block
# straddles two images.
TILE = 130
CELL = TILE + 2
BLOCK = 12
PAGE = 4096


def tile_image(image,T=TILE):
    """(x, y, w, h, cell) for every payload tile of image that holds any visible texel."""
    a=np.asarray(image.convert('RGBA'));H,W=a.shape[:2]
    padded=np.zeros((H+T+2,W+T+2,4),np.uint8);padded[1:H+1,1:W+1]=a
    for y in range(0,H,T):
        for x in range(0,W,T):
            cell=padded[y:y+T+2,x:x+T+2]
            if cell[...,3].any():yield x,y,min(T,W-x),min(T,H-y),cell


def pack_pages(unique,static_ids):
    """Shelf-pack direct images and deduplicated static tiles into block-aligned slots on 4096 pages."""
    items=[];cells={};tiled={};direct={}
    for index,image in enumerate(unique):
        if index in static_ids and image.width*image.height>=2*CELL*CELL:
            tiles=[]
            for x,y,w,h,cell in tile_image(image):
                digest=hashlib.sha256(cell.tobytes()).digest()
                if digest not in cells:cells[digest]=len(items);items.append((CELL,CELL,Image.fromarray(cell),0))
                tiles.append((cells[digest],x,y,w,h))
            tiled[index]=tiles
        else:
            # One transparent texel of gutter on every side, rounded up to whole blocks.
            direct[index]=len(items)
            items.append((math.ceil((image.width+2)/BLOCK)*BLOCK,math.ceil((image.height+2)/BLOCK)*BLOCK,image,1))
    usable=PAGE//BLOCK*BLOCK;placed={};heights=[0];x=y=shelf=0
    for i in sorted(range(len(items)),key=lambda i:(-items[i][1],-items[i][0])):
        w,h=items[i][:2]
        if w>usable or h>usable:raise ValueError('Source visual exceeds one full-density 4096 page')
        if x+w>usable:x=0;y+=shelf;shelf=0
        if y+h>usable:heights.append(0);x=y=shelf=0
        placed[i]=(len(heights)-1,x,y);x+=w;shelf=max(shelf,h);heights[-1]=max(heights[-1],y+h)
    # No mipmaps and a fixed camera zoom: pages need only whole compression blocks, not power-of-two sizes.
    atlases=[Image.new('RGBA',(PAGE,math.ceil(h/BLOCK)*BLOCK)) for h in heights]
    for i,(page,x,y) in placed.items():
        _,_,image,inset=items[i];atlases[page].paste(image,(x+inset,y+inset))
    frames=[]
    for index,image in enumerate(unique):
        name='frame_'+str(index)
        if index in tiled:
            frames.append(dict(name=name,w=image.width,h=image.height,tiles=[
                dict(page=placed[i][0],x=placed[i][1]+1,y=placed[i][2]+1,w=w,h=h,ox=ox,oy=oy) for i,ox,oy,w,h in tiled[index]]))
        else:
            page,x,y=placed[direct[index]];frames.append(dict(name=name,page=page,x=x+1,y=y+1,w=image.width,h=image.height))
    return atlases,frames,dict(tiled_images=len(tiled),tile_refs=sum(len(t) for t in tiled.values()),unique_tiles=len(cells))


def clip_to_stage(result,stage,raster_scale,margin=48):
    """Remove unreachable off-stage pixels, retaining parallax/filter overscan."""
    ox,oy=result['origin'];w,h=result['images'][0].size
    left=max(0,math.floor(((stage[0]/20-margin)-ox/20)*raster_scale))
    top=max(0,math.floor(((stage[2]/20-margin)-oy/20)*raster_scale))
    right=min(w,math.ceil(((stage[1]/20+margin)-ox/20)*raster_scale))
    bottom=min(h,math.ceil(((stage[3]/20+margin)-oy/20)*raster_scale))
    if right<=left or bottom<=top:raise ValueError('Visual lies wholly outside retained stage envelope')
    if (left,top,right,bottom)==(0,0,w,h):return result
    origin=(ox+left*20/raster_scale,oy+top*20/raster_scale)
    return dict(result,images=[im.crop((left,top,right,bottom)) for im in result['images']],origin=origin,
                origins=[origin]*len(result['images']))


class StageAlignedRenderSwf(render.RenderSwf):
    """Keep every crop on the original stage's pixel grid before rasterizing."""
    def job_bounds(self,items):
        x0,x1,y0,y1=super().job_bounds(items)
        return (math.floor(x0/20)*20,math.ceil(x1/20)*20,
                math.floor(y0/20)*20,math.ceil(y1/20)*20)


def prepare_movie(source):
    movie = sp.load(str(source/'source.swf'))
    scripts = source/'decompiled/scripts'
    hidden, stopped, holds = set(), set(), []
    for cid, symbol in movie.symbols.items():
        path = scripts.joinpath(*symbol.split('.')).with_suffix('.as')
        if not path.exists():
            continue
        text = path.read_text(encoding='utf-8-sig')
        body = functions(text).get('frame1','')
        if re.search(r'(?:^|;)\s*(?:this\.)?visible\s*=\s*false\s*;',body) and not re.search(r'\bif\s*\(',body):
            hidden.add(cid)
        if re.search(r'\bstop\(\);\s*$',body):
            stopped.add(cid)
        if body and (re.search(r'\bif\s*\(|addEventListener|rootClass|getQuest',body)):
            holds.append(dict(char=cid,symbol=symbol,file=str(path),reason='Gameplay/visibility script not compiled; authored visual preview state only.'))
    # Remove initial invisible helper instances at every depth. Preserve raw
    # sibling masks, filters, placement ages and names for the remaining graph.
    definitions=[]
    for raw in movie.raw_defs:
        code, body, _ = next(sp.iter_tags(raw,0,len(raw)))
        if code!=39:
            definitions.append(raw);continue
        cid,count=struct.unpack_from('<HH',body)
        active={}; tags=bytearray()
        for code,data,traw in sp.iter_tags(body,4,len(body)):
            if code==0:break
            if code in (4,26,70):
                pl=sp.parse_place(code,data)
                if pl.char is not None:active[pl.depth]=pl.char
                if active.get(pl.depth) in hidden:
                    tags+=sp.tag(28,struct.pack('<H',pl.depth));continue
            elif code in (5,28):active.pop(struct.unpack_from('<H',data,2 if code==5 else 0)[0],None)
            tags+=traw
            if code==1 and cid in stopped:
                count=1;break
        tags+=sp.tag(0,b'')
        raw=sp.define_sprite(cid,count,bytes(tags))
        movie.chars[cid].raw=raw
        movie.chars[cid].frames=sp.parse_timeline(render.tag_body(raw),4,len(render.tag_body(raw)))
        definitions.append(raw)
    movie.raw_defs=definitions
    # The shared item geometry reader does not bound button artwork. Supply
    # default Up-state geometry without altering the original button tag used
    # by JPEXS. Otherwise a standalone UI/button band can be cropped away.
    for char in movie.chars.values():
        if char.tag!=34:continue
        body=render.tag_body(char.raw);offset=5;places=[]
        while body[offset]:
            flags=body[offset];child,depth=struct.unpack_from('<HH',body,offset+1)
            matrix,offset=sp.read_matrix(body,offset+5);cx,offset=sp.read_cxform(body,offset,True)
            if flags&0x10:offset=sp.skip_filters(body,offset)
            if flags&0x20:offset+=1
            if flags&1:places.append(sp.Place(depth=depth,char=child,matrix=matrix,cxform=cx))
        char.kind='sprite';char.frames=[sp.Frame(places=places)]
    return movie,hidden,stopped,holds


def period(movie,cid,cache,stack=()):
    if cid in cache:return cache[cid]
    if cid in stack:raise ValueError('Recursive visual graph')
    ch=movie.chars[cid]
    value=max(1,len(ch.frames)) if ch.kind=='sprite' else 1
    for frame in ch.frames:
        for pl in frame.places:
            if pl.char in movie.chars:value=math.lcm(value,period(movie,pl.char,cache,stack+(cid,)))
    cache[cid]=value
    return value


def split_cell(movie,cell,hidden,groups=None):
    draws=[]; cache={};geometry=render.Geometry(movie)
    groups=groups or {}
    def walk(cid,matrix,cx,blend,filters,path,group='Ground'):
        group=groups.get(path,group)
        if cid in hidden:return
        bounds=geometry.timeline_bounds(cid)
        if bounds is not None:
            bounds=render.transform_bounds(matrix,bounds)
            # Some authored rooms include a long strip of copies outside the
            # Flash stage. Cull only content whose complete timeline envelope
            # cannot reach the stage, retaining a generous filter margin.
            margin=48*20
            if bounds[1]<movie.stage[0]-margin or bounds[0]>movie.stage[1]+margin or bounds[3]<movie.stage[2]-margin or bounds[2]>movie.stage[3]+margin:return
        ch=movie.chars[cid]; clock=period(movie,cid,cache)
        if clock>960:raise ValueError('Unbounded map visual clock: '+str((cid,clock)))
        split_for_layout=any(p.startswith(path+'/') for p in groups)
        if ch.kind=='sprite' and len(ch.frames)==1 and (clock>1 or split_for_layout) and not filters and blend in (None,0,1,2):
            children=sp.display_lists(ch.frames)[0]
            if any(p.get('clip') for p in children.values()):
                raise ValueError('Animated masked map container needs scoped compilation')
            for depth,state in sorted(children.items()):
                walk(state['char'],render.mat_mul(matrix,state['matrix']),render.cx_mul(cx,state['cxform']),
                     state['blend'],state['filters_raw'],path+'/'+str(depth),group)
        else:
            if blend not in (None,0,1,2):raise ValueError('Backdrop-dependent map blend requires compositor')
            item=dict(char=cid,matrix=matrix,cxform=cx,blend=blend,filters_raw=filters)
            if clock==1 and draws and draws[-1]['period']==1 and draws[-1].get('group')==group:
                draws[-1]['items'].append(item)
            else:draws.append(dict(period=clock,items=[item],source_path=path,char=cid,group=group))
    for depth,state in sorted(sp.display_lists(movie.root)[cell['source_frame']-1].items()):
        if state.get('clip'):raise ValueError('Root map mask requires scoped compilation')
        walk(state['char'],state['matrix'],state['cxform'],state['blend'],state['filters_raw'],str(depth))
    return draws


def reference(movie,cell,hidden,out,samples,raster_scale=1):
    controls=bytearray()
    for depth,state in sorted(sp.display_lists(movie.root)[cell['source_frame']-1].items()):
        if state['char'] in hidden:continue
        controls+=sp.encode_place(sp.Place(depth=depth,char=state['char'],matrix=state['matrix'],cxform=state['cxform'],
            clip_depth=state['clip'],blend=state['blend'],ratio=state['ratio'],filters_raw=state['filters_raw']))
    controls+=sp.tag(1,b'')*(max(samples)+1)+sp.tag(0,b'')
    body=render.rect_bytes(*movie.stage)+struct.pack('<HH',round(movie.fps*256),max(samples)+1)+b''.join(movie.raw_defs)+controls
    folder=out/'references'/cell['name'];folder.mkdir(parents=True,exist_ok=True)
    swf=folder/'source-reference.swf';swf.write_bytes(b'FWS'+bytes([movie.version])+struct.pack('<I',len(body)+8)+body)
    ffdec('-ignorebackground','-zoom',str(raster_scale),'-select',','.join(str(f+1) for f in samples),'-format','frame:png','-export','frame',
          str(folder/'frames'),str(swf),profile=str(out/'jpexs_profile'))
    found={int(p.stem)-1:p for p in (folder/'frames').rglob('*.png')}
    if not set(samples)<=set(found):raise ValueError('Incomplete independent source references')
    for frame in samples:
        with Image.open(found[frame]) as im:im.save(folder/(str(frame)+'.png'))


def convert(source,out,layout=None,reuse=None,raster_scale=1):
    # This CLI owns its rendering process; leave shared item renderer unchanged.
    render.RenderSwf=StageAlignedRenderSwf
    source,out=source.resolve(),out.resolve();out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((source/'map_source.json').read_text())
    movie,hidden,stopped,holds=prepare_movie(source)
    layout=json.loads(layout.read_text()) if layout else {}
    if layout:assert layout['source_sha256']==manifest['source_sha256']
    previous=json.loads((reuse/'visuals.json').read_text()) if reuse else None
    if previous:assert previous['source_sha256']==manifest['source_sha256']
    if previous:assert previous.get('raster_scale',1)==raster_scale,'Cannot reuse pixels from a different raster density'
    old_atlases=[Image.open(reuse/p['file']).convert('RGBA') for p in previous.get('atlases',[dict(file='atlas.png')])] if reuse else None
    cells=[c for c in manifest['cells'] if c['name'] in ('Enter','Garden1')]
    rendered=[]; plans=[]; unique=[]; seen={};render_cache={};cell_jobs=[];jobs_by_key={}
    for cell in cells:
        config=layout.get('cells',{}).get(cell['name'],{})
        draws=split_cell(movie,cell,hidden,config.get('paths',{}))
        jobs=[dict(items=d['items'],frames=d['period'],group=i) for i,d in enumerate(draws)]
        # Keep each clock's raster stage bounded by its own artwork. A torch
        # must not pay for a room-sized canvas on every frame.
        keys=[];anchors=[]
        for i,job in enumerate(jobs):
            anchor=(0,0)
            if job['frames']>1 and len(job['items'])==1:
                job=copy.deepcopy(job);m=job['items'][0]['matrix']
                anchor=(math.floor(m[4]/20)*20,math.floor(m[5]/20)*20)
                job['items'][0]['matrix']=(*m[:4],m[4]-anchor[0],m[5]-anchor[1])
            key=repr(job['items'])+str(job['frames'])
            job['group']=hashlib.sha256(key.encode()).hexdigest();jobs_by_key[key]=job
            keys.append(key);anchors.append(anchor)
            # Layout-only revisions can reuse previously verified animated
            # subtree pixels. Static bands change when scenery is separated.
            old=next((l for c in previous['cells'] if c['name']==cell['name'] for l in c['layers']
                      if l['source_path']==draws[i]['source_path'] and l['period']==job['frames'] and l['source_char']==draws[i]['char']),None) if previous and job['frames']>1 else None
            if old:
                images=[]
                for fid in old['frames']:
                    f=previous['frames'][fid];images.append(old_atlases[f.get('page',0)].crop((f['x'],f['y'],f['x']+f['w'],f['y']+f['h'])))
                w,h=images[0].size;origin=((old['x']-w/(2*raster_scale))*20-anchor[0],(old['y']-h/(2*raster_scale))*20-anchor[1])
                render_cache[key]=dict(images=images,origin=origin,origins=[origin]*len(images))
        cell_jobs.append((cell,draws,keys,anchors))
    bins={};rs=render.RenderSwf(movie,{})
    for key,job in jobs_by_key.items():
        if key in render_cache:continue
        folder=out/'work/shared'/hashlib.sha256(key.encode()).hexdigest()[:16]
        cached=list((folder/'visual_frames').rglob('*.png'))
        if len(cached)==job['frames']:
            render_cache[key]=render.render_jobs(movie,{},[job],raster_scale,str(folder),name='visual')[0];continue
        bounds=rs.job_bounds(job['items']);w=((bounds[1]-bounds[0])/20+96)*raster_scale;h=((bounds[3]-bounds[2])/20+96)*raster_scale
        size=(math.ceil(math.log2(max(1,w))),math.ceil(math.log2(max(1,h))))
        bins.setdefault(size,[]).append(key)
    for size,keys in sorted(bins.items()):
        print('Render bucket',size,'jobs',len(keys),'frames',sum(jobs_by_key[k]['frames'] for k in keys),flush=True)
        results=render.render_jobs(movie,{},[jobs_by_key[k] for k in keys],raster_scale,str(out/'work/bins'/('_'.join(map(str,size)))),name='visual',bucket=False)
        render_cache.update(zip(keys,results))
    for cell,draws,keys,anchors in cell_jobs:
        results=[]
        for key,anchor in zip(keys,anchors):
            result=dict(render_cache[key])
            if len(set(result['origins']))!=1:raise ValueError('Varying crop in shared visual clock')
            result['origin']=(result['origin'][0]+anchor[0],result['origin'][1]+anchor[1])
            result['origins']=[result['origin']]*len(result['images'])
            results.append(clip_to_stage(result,movie.stage,raster_scale))
        layers=[]
        for order,(draw,result) in enumerate(zip(draws,results)):
            ids=[]
            for image in result['images']:
                digest=hashlib.sha256(image.tobytes()).hexdigest();key=(image.size,digest)
                if key not in seen:
                    seen[key]=len(unique);unique.append(image.copy())
                ids.append(seen[key])
            # One union crop per visual clock keeps its pivot fixed across frames.
            if len(set(result['origins']))!=1:raise ValueError('Unexpected varying clip crop')
            ox,oy=result['origin'];w,h=result['images'][0].size
            layers.append(dict(name='Visual_'+str(order),order=order,period=draw['period'],frames=ids,
                x=ox/20+w/(2*raster_scale),y=oy/20+h/(2*raster_scale),source_path=draw['source_path'],source_char=draw['char'],group=draw['group']))
        plans.append(dict(name=cell['name'],layers=layers,groups=layout.get('cells',{}).get(cell['name'],{}).get('groups',[])))
        reference(movie,cell,hidden,out,SAMPLES,raster_scale)
    animated={fid for c in plans for l in c['layers'] if l['period']>1 for fid in l['frames']}
    pages,frames,tiling=pack_pages(unique,set(range(len(unique)))-animated)
    atlases=[]
    for page,atlas in enumerate(pages):
        name='atlas.png' if page==0 else 'atlas_'+str(page)+'.png'
        atlas.save(out/name);atlases.append(dict(file=name,width=atlas.width,height=atlas.height))
    texture_bytes=sum(p['width']*p['height']*4 for p in atlases)
    if texture_bytes>192*1024*1024:raise ValueError('Map diffuse atlas budget exceeded')
    gpu_bytes=dict(rgba32=texture_bytes,bc7=texture_bytes//4,
                   astc_6x6=sum(math.ceil(p['width']/6)*math.ceil(p['height']/6)*16 for p in atlases))
    result=dict(schema='flash-map-visuals-v2',source_sha256=manifest['source_sha256'],fps=movie.fps,atlas='atlas.png',
        atlas_width=atlases[0]['width'],atlas_height=atlases[0]['height'],texture_bytes=texture_bytes,gpu_bytes=gpu_bytes,
        tiling=dict(tiling,tile=TILE,cell=CELL,block=BLOCK),atlases=atlases,raster_scale=raster_scale,frames=frames,cells=plans,samples=SAMPLES,
        overscan_source_pixels=48,
        initially_hidden_symbols=sorted(hidden),initially_stopped_symbols=sorted(stopped),script_holds=holds,
        proof_boundary='Visual timelines only. Stateful NPC/quest/visibility behavior remains uncompiled.')
    (out/'visuals.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(cells=[(c['name'],len(c['layers']),sum(l['period']>1 for l in c['layers'])) for c in plans],
                         unique_images=len(unique),tiling=result['tiling'],atlases=atlases,gpu_bytes=gpu_bytes),indent=2),flush=True)
    return result


def repack(previous,out):
    """Re-store an exported visual set's exact frame pixels as sparse tiles, without re-rasterizing."""
    import shutil
    previous,out=previous.resolve(),out.resolve();out.mkdir(parents=True,exist_ok=True)
    data=json.loads((previous/'visuals.json').read_text())
    assert not any('tiles' in f for f in data['frames']),'Already tiled'
    pages=[Image.open(previous/p['file']).convert('RGBA') for p in data['atlases']]
    unique=[pages[f.get('page',0)].crop((f['x'],f['y'],f['x']+f['w'],f['y']+f['h'])) for f in data['frames']]
    animated={fid for c in data['cells'] for l in c['layers'] if l['period']>1 for fid in l['frames']}
    atlas_images,frames,tiling=pack_pages(unique,set(range(len(unique)))-animated)
    atlases=[]
    for page,atlas in enumerate(atlas_images):
        name='atlas.png' if page==0 else 'atlas_'+str(page)+'.png'
        atlas.save(out/name);atlases.append(dict(file=name,width=atlas.width,height=atlas.height))
    texture_bytes=sum(p['width']*p['height']*4 for p in atlases)
    gpu_bytes=dict(rgba32=texture_bytes,bc7=texture_bytes//4,
                   astc_6x6=sum(math.ceil(p['width']/6)*math.ceil(p['height']/6)*16 for p in atlases))
    data.update(schema='flash-map-visuals-v2',atlas_width=atlases[0]['width'],atlas_height=atlases[0]['height'],
                texture_bytes=texture_bytes,gpu_bytes=gpu_bytes,tiling=dict(tiling,tile=TILE,cell=CELL,block=BLOCK),
                atlases=atlases,frames=frames,repacked_from=str(previous))
    if (out/'references').exists():shutil.rmtree(out/'references')
    shutil.copytree(previous/'references',out/'references')
    (out/'visuals.json').write_text(json.dumps(data,indent=2))
    print(json.dumps(dict(tiling=data['tiling'],atlases=atlases,gpu_bytes=gpu_bytes),indent=2),flush=True)
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('out',type=Path)
    parser.add_argument('--layout',type=Path);parser.add_argument('--reuse',type=Path)
    parser.add_argument('--raster-scale',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--repack',action='store_true',help='source is an exported visuals directory; re-store it as sparse tiles')
    args=parser.parse_args()
    if args.repack:repack(args.source,args.out)
    else:convert(args.source,args.out,args.layout,args.reuse,args.raster_scale)
