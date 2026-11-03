"""Map animation must retain child clocks and source transforms."""
import pytest
import swfparse as sp
import map_visuals as visual
from render import IDENT


def test_independent_three_and_five_frame_children_are_not_one_parent_clock():
    a=sp.Character(1,'sprite',39,frames=[sp.Frame() for _ in range(3)])
    b=sp.Character(2,'sprite',39,frames=[sp.Frame() for _ in range(5)])
    parent=sp.Character(3,'sprite',39,frames=[sp.Frame(places=[sp.Place(1,char=1,matrix=IDENT),sp.Place(2,char=2,matrix=(1,0,0,1,200,400))])])
    movie=sp.Movie(10,(0,1000,0,1000),24,1,{1:a,2:b,3:parent},[sp.Frame(places=[sp.Place(1,char=3,matrix=IDENT)])],{},[])
    layers=visual.split_cell(movie,{'source_frame':1},set())
    assert [layer['period'] for layer in layers]==[3,5]
    assert layers[1]['items'][0]['matrix'][4:]==(200,400)


def test_clock_over_limit_is_held_instead_of_truncated():
    sprite=sp.Character(1,'sprite',39,frames=[sp.Frame() for _ in range(961)])
    movie=sp.Movie(10,(0,1000,0,1000),24,1,{1:sprite},[sp.Frame(places=[sp.Place(1,char=1,matrix=IDENT)])],{},[])
    with pytest.raises(ValueError,match='Unbounded'):
        visual.split_cell(movie,{'source_frame':1},set())


def test_hidden_helper_does_not_contribute_to_visual_bands():
    shape=sp.Character(1,'shape',2,bounds=(0,20,0,20))
    movie=sp.Movie(10,(0,1000,0,1000),24,1,{1:shape},[sp.Frame(places=[sp.Place(1,char=1,matrix=IDENT)])],{},[])
    assert visual.split_cell(movie,{'source_frame':1},{1})==[]


def test_raster_crops_preserve_stage_pixel_phase(monkeypatch):
    monkeypatch.setattr(visual.render.RenderSwf,'job_bounds',lambda self,items:(-1.5,41.2,3.9,81.1))
    renderer=object.__new__(visual.StageAlignedRenderSwf)
    assert renderer.job_bounds([])==(-20,60,0,100)


def test_static_scenery_groups_are_split_without_merging_into_ground():
    shape=sp.Character(1,'shape',2,bounds=(0,20,0,20))
    parent=sp.Character(2,'sprite',39,frames=[sp.Frame(places=[sp.Place(1,char=1,matrix=IDENT),sp.Place(2,char=1,matrix=(1,0,0,1,200,0))])])
    movie=sp.Movie(10,(0,1000,0,1000),24,1,{1:shape,2:parent},[sp.Frame(places=[sp.Place(1,char=2,matrix=IDENT)])],{},[])
    layers=visual.split_cell(movie,{'source_frame':1},set(),{'1/2':'Foreground'})
    assert [(l['source_path'],l['group'],len(l['items'])) for l in layers]==[('1/1','Ground',1),('1/2','Foreground',1)]


def test_high_density_overscan_crop_retains_visible_pixels_and_fixed_clock_pivot():
    from PIL import Image,ImageDraw
    frames=[]
    for color in ('red','blue'):
        im=Image.new('RGBA',(3600,1800));ImageDraw.Draw(im).rectangle((300,150,3179,1631),fill=color);frames.append(im)
    source=dict(images=frames,origin=(-2000,-1000),origins=[(-2000,-1000)]*2)
    clipped=visual.clip_to_stage(source,(0,19200,0,9880),3)
    assert len(set(clipped['origins']))==1
    assert clipped['images'][0].width<=4096
    for original,retained in zip(frames,clipped['images']):
        x=round(-clipped['origin'][0]/20*3);y=round(-clipped['origin'][1]/20*3)
        assert retained.crop((x,y,x+2880,y+1482)).tobytes()==original.crop((300,150,3180,1632)).tobytes()


def test_sparse_tiles_drop_empty_space_keep_true_borders_and_align_to_compression_blocks():
    import numpy as np
    from PIL import Image
    rng=np.random.default_rng(7)
    big=np.zeros((400,700,4),np.uint8);big[20:180,30:300]=rng.integers(1,255,(160,270,4),dtype=np.uint8)
    big[300:390,500:690]=(10,20,30,255)
    small=Image.fromarray(rng.integers(0,255,(37,53,4),dtype=np.uint8))
    pages,frames,tiling=visual.pack_pages([Image.fromarray(big),small],{0})
    assert tiling['tile_refs']<(-(-700//visual.TILE))*(-(-400//visual.TILE))
    atlas=[np.asarray(p) for p in pages]
    assert all(p.shape[0]%visual.BLOCK==0 for p in atlas)
    padded=np.zeros((402,702,4),np.uint8);padded[1:-1,1:-1]=big
    rebuilt=np.zeros_like(big)
    for t in frames[0]['tiles']:
        assert (t['x']-1)%visual.BLOCK==0 and (t['y']-1)%visual.BLOCK==0
        cell=atlas[t['page']][t['y']-1:t['y']+t['h']+1,t['x']-1:t['x']+t['w']+1]
        assert np.array_equal(cell,padded[t['oy']:t['oy']+t['h']+2,t['ox']:t['ox']+t['w']+2])
        rebuilt[t['oy']:t['oy']+t['h'],t['ox']:t['ox']+t['w']]=cell[1:-1,1:-1]
    assert np.array_equal(rebuilt,big)
    f=frames[1];around=atlas[f['page']][f['y']-1:f['y']+f['h']+1,f['x']-1:f['x']+f['w']+1]
    assert np.array_equal(around[1:-1,1:-1],np.asarray(small))
    ring=np.ones(around.shape[:2],bool);ring[1:-1,1:-1]=False
    assert not around[...,3][ring].any()
