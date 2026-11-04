"""Render a frame range of a Flash symbol from vectors into a grid flipbook sheet for a Unity particle.

usage: python bake_flipbook.py <swf> <char id> <first frame> <last frame> <out.png> [--zoom 3]

Frames are 1-based and inclusive, as in the Flash timeline (e.g. AQW Bat2's "Walk" loop is 17-29 of sprite 54).
Every frame shares one crop, so the pivot does not swim, and cells are square so the particle's quad is not
stretched. Writes <out>.json with the grid, frame count and source.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image

import render
import swfparse as sp


def bake(swf, char, first, last, out, zoom=3):
    data=Path(swf).read_bytes();movie=sp.load(data)
    job=dict(items=[dict(char=char,matrix=render.IDENT,cxform=None,blend=None,filters_raw=b'')],frames=last,group=0)
    result=render.render_jobs(movie,{},[job],zoom,str(Path(out).parent/'flipbook_work'),name='flipbook',bucket=False)[0]
    frames=result['images'][first-1:last]
    boxes=[im.getbbox() for im in frames if im.getbbox()]
    if not boxes:raise ValueError('Symbol renders nothing in that frame range')
    x0,y0=min(b[0] for b in boxes),min(b[1] for b in boxes);x1,y1=max(b[2] for b in boxes),max(b[3] for b in boxes)
    side=max(x1-x0,y1-y0)+4
    cells=[]
    for im in frames:
        cell=Image.new('RGBA',(side,side));crop=im.crop((x0,y0,x1,y1))
        cell.paste(crop,((side-crop.width)//2,(side-crop.height)//2));cells.append(cell)
    cols=math.ceil(math.sqrt(len(cells)));rows=math.ceil(len(cells)/cols)
    sheet=Image.new('RGBA',(cols*side,rows*side))
    for i,cell in enumerate(cells):sheet.paste(cell,((i%cols)*side,(i//cols)*side))
    sheet.save(out)
    meta=dict(source=str(swf),source_sha256=hashlib.sha256(data).hexdigest(),char=char,first=first,last=last,
              frames=len(cells),cols=cols,rows=rows,cell_px=side,zoom=zoom,fps=movie.fps)
    Path(out).with_suffix('.json').write_text(json.dumps(meta,indent=2))
    return meta


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('swf',type=Path);parser.add_argument('char',type=int)
    parser.add_argument('first',type=int);parser.add_argument('last',type=int);parser.add_argument('out',type=Path)
    parser.add_argument('--zoom',type=float,default=3)
    args=parser.parse_args()
    print(json.dumps(bake(args.swf,args.char,args.first,args.last,args.out,args.zoom),indent=2))
