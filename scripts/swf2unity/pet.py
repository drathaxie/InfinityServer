"""Convert pets whose Idle/Walk labels share one continuously animated display list.

The first supported case is Flying Moglin. Source root scripts only stop/loop the
unchanging wrapper. Nested visual clocks remain independent when the native follower
switches between Idle and Walk. Different visual states are explicitly unsupported.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

import render
import rig
import swfparse as sp
import weapon


def freeze_wrapper(source, linkage, target):
    movie = sp.load(str(source))
    cid = next((c for c, n in movie.symbols.items() if n == linkage), None)
    if cid is None:
        raise ValueError('Pet linkage not found: ' + linkage)
    frames = movie.chars[cid].frames
    labels = {f.label: i for i, f in enumerate(frames) if f.label}
    if not {'Idle', 'Walk'}.issubset(labels):
        raise ValueError('Both Idle and Walk labels are required')
    states = sp.display_lists(frames)
    # Instance lifetimes are part of this equality: identical drawings that reset
    # children on a movement transition are a different conversion case.
    if any(state != states[labels['Idle']] for state in states):
        raise ValueError('Pet has distinct root drawing states; state conversion required')
    state = states[labels['Idle']]
    placements = b''.join(sp.encode_place(sp.Place(
        depth=depth, char=s['char'], matrix=s['matrix'], cxform=s['cxform'],
        ratio=s['ratio'], clip_depth=s['clip'], blend=s['blend'],
        filters_raw=s['filters_raw'], name=s['name'])) for depth, s in sorted(state.items()))
    wrapper_id = max(movie.chars) + 1
    wrapper = sp.define_sprite(wrapper_id, 1, placements + sp.tag(1, b''))
    symbols = sp.tag(76, struct.pack('<HH', 1, wrapper_id) + linkage.encode() + b'\0')
    header = render.rect_bytes(*movie.stage) + struct.pack('<HH', round(movie.fps * 256), 1)
    tags = sp.tag(69, struct.pack('<I', 8)) + b''.join(movie.raw_defs) + wrapper + symbols
    body = header + tags + sp.tag(1, b'') + sp.tag(0, b'')
    target.write_bytes(b'FWS' + bytes([max(movie.version, 10)]) + struct.pack('<I', len(body) + 8) + body)
    test = sp.load(str(target))
    decomposer = rig.Rig(test)
    layers = decomposer.decompose(wrapper_id)
    if decomposer.notes or len(layers) > 32:
        raise ValueError('Pet has unresolved rig coverage: ' + str(decomposer.notes))
    return dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                original_linkage=linkage, original_root=cid, labels=labels,
                root_state_invariant=True, layers=len(layers),
                independent_periods=sorted({layer.period for layer in layers}),
                scripted_behavior='Root stop/goto labels mapped to native Idle/Walk; no child scripts expected')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('swf', type=Path)
    ap.add_argument('linkage')
    ap.add_argument('out', type=Path)
    ap.add_argument('--prefab', required=True)
    ap.add_argument('--name', required=True)
    # Unscaled Flash display coordinates: native body robe is about 6.1 px/Flash px.
    ap.add_argument('--k', type=float, default=6.1)
    ap.add_argument('--validate-frames', type=int, default=0)
    ap.add_argument('--validate-step', type=int, default=1)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    frozen = args.out / 'pet_visual.swf'
    contract = freeze_wrapper(args.swf.resolve(), args.linkage, frozen)
    visual_name = args.prefab + '_Visual'
    data = weapon.convert(str(frozen), args.linkage, str(args.out), visual_name, args.k, args.name)
    gender = data['genders']['w']
    visual = gender['nodes'][0]
    visual['parent'] = 0
    root = dict(visual, name=args.prefab, parent=-1, hasRenderer=False)
    gender['nodes'] = [root, visual]
    gender['prefab_name'] = args.prefab
    gender['pet_loop'] = True
    data['pet_contract'] = contract
    (args.out / 'armor.json').write_text(json.dumps(data, indent=1))
    (args.out / 'source_contract.json').write_text(json.dumps(contract, indent=2))
    if args.validate_frames:
        weapon.validate(str(args.out), args.validate_frames, args.validate_step)


if __name__ == '__main__':
    main()
