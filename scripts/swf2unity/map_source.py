"""Extract Flash map cells and source geometry for a local Unity prototype.

This reads inspector literals; it never executes ActionScript. Unknown scripts
remain explicit holds. Room snapshots are not a completed animation conversion.
"""
import argparse
import hashlib
import json
import re
import shutil
import struct
from pathlib import Path
import xml.etree.ElementTree as ET

import swfparse as sp
from ccscripts import ffdec
from interactive_morph import functions
from render import IDENT, mat_apply, mat_mul, rect_bytes, tag_body


def literal(text):
    text = text.strip()
    if text.startswith('[') and text.endswith(']'):
        value = json.loads(text)
        if not isinstance(value, list) or any(type(item) not in (int, float, str, bool) for item in value):
            raise ValueError('Unsupported inspector array')
        return value
    if text in ('true', 'false'):
        return text == 'true'
    if re.fullmatch(r'"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?', text):
        # JPEXS escapes apostrophes even inside double-quoted AS3 strings.
        return json.loads(text.replace("\\'", "'"))
    raise ValueError('Not an inspector literal: ' + text)


def inspectors(text):
    """Adobe setter evidence, including its explicit one-based frame guard."""
    output = []
    for name, body in functions(text).items():
        if not name.startswith('__setProp_') or name == '__setProp_handler':
            continue
        assignments = list(re.finditer(r'(?:this\.)?(__id\d+_)\.(\w+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]+);', body))
        if not assignments:
            continue
        frames = re.search(r'param1\s*>=\s*(\d+)\s*&&\s*param1\s*<=\s*(\d+)', body)
        if not frames:
            raise ValueError('Setter lacks a supported frame guard: ' + name)
        properties = {}
        instances = set()
        for match in assignments:
            instances.add(match[1])
            properties[match[2]] = literal(match[3])
        if len(instances) != 1:
            raise ValueError('Ambiguous inspector targets: ' + name)
        output.append(dict(method=name, instance=instances.pop(), start=int(frames[1]),
                           end=int(frames[2]), properties=properties))
    return output


def button_hit(movie, cid):
    char = movie.chars[cid]
    if char.tag != 34:
        raise ValueError('Only DefineButton2 hit states are supported')
    body = tag_body(char.raw)
    p, result = 5, []
    while body[p]:
        flags = body[p]
        child, depth = struct.unpack_from('<HH', body, p + 1)
        matrix, p = sp.read_matrix(body, p + 5)
        cx, p = sp.read_cxform(body, p, True)
        if flags & 0x10:
            p = sp.skip_filters(body, p)
        if flags & 0x20:
            p += 1
        if flags & 8:
            result.append(dict(char=child, matrix=matrix, depth=depth))
    if not result:
        raise ValueError('Button has no authored hit state')
    return result


def shape_refs(movie, cid, matrix=IDENT, stack=()):
    if cid in stack:
        raise ValueError('Recursive collision symbol')
    char = movie.chars[cid]
    if char.kind == 'shape':
        return [dict(char=cid, matrix=list(matrix))]
    if char.kind == 'button':
        children = button_hit(movie, cid)
    elif char.kind == 'sprite' and len(char.frames) == 1:
        children = list(sp.display_lists(char.frames)[0].values())
    else:
        raise ValueError('Animated or unsupported collision definition: ' + str(cid))
    output = []
    for child in children:
        if child.get('clip') or child.get('filters'):
            raise ValueError('Masked/filtered collision definition')
        output.extend(shape_refs(movie, child['char'], mat_mul(matrix, child['matrix']), stack + (cid,)))
    return output


def straight_path(data):
    """Exact straight-edged filled contours; reject curves and multiple contours."""
    tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', data)
    points, index, command = [], 0, None
    while index < len(tokens):
        if re.fullmatch('[A-Za-z]', tokens[index]):
            command = tokens[index]
            index += 1
            if command == 'Z':
                if index != len(tokens):
                    raise ValueError('Multiple collider contours require explicit hole handling')
                break
            if command not in ('M', 'L') or (command == 'M' and points):
                raise ValueError('Unsupported collider path command: ' + command)
        if command not in ('M', 'L') or index + 1 >= len(tokens):
            raise ValueError('Malformed collider path')
        points.append([float(tokens[index]), float(tokens[index + 1])])
        index += 2
        command = 'L'
    if points and points[-1] == points[0]:
        points.pop()
    if len(points) < 3:
        raise ValueError('Collider has fewer than three points')
    return points


def svg_polygon(path):
    tree = ET.parse(path)
    paths = [element for element in tree.iter() if element.tag.endswith('}path')
             and element.get('fill', 'none') != 'none']
    if len(paths) != 1:
        raise ValueError('Expected one filled collider contour: ' + str(path))
    # JPEXS's outer group recenters the exported image. Path d retains the
    # original symbol-local coordinates, which are needed with the SWF matrix.
    return straight_path(paths[0].get('d', ''))


def extract(source, out):
    source, out = source.resolve(), out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cached = out / 'source.swf'
    if cached.exists() and hashlib.sha256(cached.read_bytes()).hexdigest() != digest:
        raise ValueError('Output already belongs to a different source; use a fresh directory')
    if source != cached:
        shutil.copyfile(source, cached)
    scripts = out / 'decompiled' / 'scripts'
    if not scripts.exists():
        ffdec('-export', 'script', str(out / 'decompiled'), str(cached), profile=str(out / 'jpexs_profile'))
    movie = sp.load(str(cached))
    root_symbol = movie.symbols.get(0)
    if not root_symbol:
        raise ValueError('Map has no root SymbolClass')
    main_path = scripts.joinpath(*root_symbol.split('.')).with_suffix('.as')
    main_text = main_path.read_text(encoding='utf-8-sig')
    setters = inspectors(main_text)
    sources = {}
    hidden = set()
    for cid, symbol in movie.symbols.items():
        path = scripts.joinpath(*symbol.split('.')).with_suffix('.as')
        if path.exists():
            text = path.read_text(encoding='utf-8-sig')
            sources[cid] = text
            # Only an unconditional assignment directly in frame1 is used.
            body = functions(text).get('frame1', '')
            if re.search(r'(?:^|;)\s*(?:this\.)?visible\s*=\s*false\s*;', body) and not re.search(r'\bif\s*\(', body):
                hidden.add(cid)
    labels = [(i, frame.label) for i, frame in enumerate(movie.root) if frame.label]
    display = sp.display_lists(movie.root)
    cells, all_shapes = [], set()
    for index, (frame, label) in enumerate(labels):
        if label in ('Wait', 'Blank') or label.lower().startswith('cut'):
            continue
        end = labels[index + 1][0] if index + 1 < len(labels) else len(movie.root)
        props = {setter['instance']: setter for setter in setters if setter['start'] <= frame + 1 <= setter['end']}
        cell = dict(name=label, source_frame=frame + 1, end_frame=end, placements=[],
                    spawns=[], exits=[], blockers=[], walkables=[], monsters=[], npcs=[], settings={})
        for depth, state in sorted(display[frame].items()):
            cid, name = state['char'], state['name']
            text = sources.get(cid, '')
            inspector = props.get(name, {})
            fields = inspector.get('properties', {})
            node = dict(depth=depth, char=cid, name=name, symbol=movie.symbols.get(cid),
                        matrix=list(state['matrix']), cxform=state['cxform'],
                        clip_depth=state['clip'], blend=state['blend'],
                        hidden_by_initial_script=cid in hidden, inspector=inspector)
            cell['placements'].append(node)
            record = dict(name=name, char=cid, depth=depth, matrix=list(state['matrix']),
                          source_symbol=node['symbol'], fields=fields)
            kind = None
            if re.search(r'\bisSolid\s*=\s*true', text):
                kind = 'blockers'
            elif 'onWalkClick()' in text:
                kind = 'walkables'
            elif 'tCell' in fields and 'tPad' in fields:
                kind = 'exits'
                record.update(target_cell=fields['tCell'], target_pad=fields['tPad'],
                              conditional=bool(re.search(r'\bif\s*\(', functions(text).get('onEnter', ''))))
            elif 'strNewMap' in fields:
                kind = 'exits'
                record.update(target_map=fields['strNewMap'], target_cell=fields.get('strSpawnCell'),
                              target_pad=fields.get('strSpawnPad'), conditional=True)
            elif 'MonMapID' in fields:
                kind = 'monsters'
            elif 'npcLinkage' in fields:
                kind = 'npcs'
            elif 'intScale' in fields:
                cell['settings'] = fields
            elif name and re.search(r'(?:^|\.)Pad_', node['symbol'] or ''):
                cell['spawns'].append(record)
            if kind:
                if kind in ('blockers', 'walkables', 'exits'):
                    try:
                        if kind == 'walkables':
                            children = sp.display_lists(movie.chars[cid].frames)[0]
                            refs = []
                            for child in children.values():
                                if child['name'] == 'btnWalkingArea':
                                    refs += shape_refs(movie, child['char'], mat_mul(state['matrix'], child['matrix']))
                        else:
                            children = sp.display_lists(movie.chars[cid].frames)[0]
                            refs = []
                            for child in children.values():
                                if child['name'] == 'shadow':
                                    refs += shape_refs(movie, child['char'], mat_mul(state['matrix'], child['matrix']))
                        if not refs:
                            raise ValueError('No authored collision child found')
                        record['shapes'] = refs
                        all_shapes.update(ref['char'] for ref in refs)
                    except ValueError as exc:
                        record['geometry_hold'] = str(exc)
                cell[kind].append(record)
        cells.append(cell)
    geometry_dir = out / 'geometry'
    if all_shapes:
        ffdec('-selectid', ','.join(map(str, sorted(all_shapes))), '-format', 'shape:svg', '-export', 'shape',
              str(geometry_dir), str(cached), profile=str(out / 'jpexs_profile'))
    for cell in cells:
        for kind in ('walkables', 'blockers', 'exits'):
            for record in cell[kind]:
                polygons = []
                for ref in record.get('shapes', []):
                    try:
                        points = svg_polygon(geometry_dir / (str(ref['char']) + '.svg'))
                        # SVG d uses source pixels; placement translation uses twips.
                        polygons.append([[v / 20 for v in mat_apply(ref['matrix'], x * 20, y * 20)] for x, y in points])
                    except ValueError as exc:
                        record['geometry_hold'] = str(exc)
                record['polygons_px'] = polygons
    by_name = {cell['name']: cell for cell in cells}
    problems = []
    for cell in cells:
        for exit in cell['exits']:
            if exit.get('target_map'):
                continue
            target = by_name.get(exit['target_cell'])
            if target is None or exit['target_pad'] not in {pad['name'] for pad in target['spawns']}:
                problems.append(dict(cell=cell['name'], exit=exit['name'], target=exit['target_cell'], pad=exit['target_pad']))
    manifest = dict(schema='flash-map-source-v1', source_sha256=digest, source=str(source), fps=movie.fps,
                    stage_px=[v / 20 for v in movie.stage], cells=cells, graph_problems=problems,
                    holds=['ActionScript gameplay and conditional visibility are inventoried, not executed.',
                           'Room snapshots do not preserve animation and are only structural prototype art.',
                           'Unity world scale and movement speed require installed-client calibration.'],
                    script_inventory=[dict(path=str(path.relative_to(out)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                                      for path in sorted(scripts.rglob('*.as'))])
    (out / 'map_source.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return movie, manifest, hidden


def snapshot(movie, cell, hidden, out):
    """Authoring snapshot with only proven initially invisible root markers hidden."""
    controls = bytearray()
    for depth, state in sorted(sp.display_lists(movie.root)[cell['source_frame'] - 1].items()):
        if state['char'] in hidden:
            continue
        controls += sp.encode_place(sp.Place(depth=depth, char=state['char'], matrix=state['matrix'],
                                             cxform=state['cxform'], clip_depth=state['clip'], blend=state['blend'],
                                             ratio=state['ratio'], filters_raw=state['filters_raw'], name=state['name']))
    controls += sp.tag(1, b'') + sp.tag(0, b'')
    header = rect_bytes(*movie.stage) + struct.pack('<HH', round(movie.fps * 256), 1)
    definitions = []
    for raw in movie.raw_defs:
        code, definition, _ = next(sp.iter_tags(raw, 0, len(raw)))
        if code == 39:
            changed = bytearray()
            active = {}
            for tag_code, tag_data, tag_raw in sp.iter_tags(definition, 4, len(definition)):
                if tag_code in (4, 26, 70):
                    placement = sp.parse_place(tag_code, tag_data)
                    if placement.char is not None:
                        active[placement.depth] = placement.char
                    if active.get(placement.depth) in hidden:
                        placement.visible = 0
                        tag_raw = sp.encode_place(placement)
                elif tag_code in (5, 28):
                    active.pop(struct.unpack_from('<H', tag_data, 2 if tag_code == 5 else 0)[0], None)
                changed += tag_raw
            raw = sp.define_sprite(struct.unpack_from('<H', definition)[0],
                                   struct.unpack_from('<H', definition, 2)[0], bytes(changed))
        definitions.append(raw)
    body = header + b''.join(definitions) + controls
    raw = b'FWS' + bytes([movie.version]) + struct.pack('<I', len(body) + 8) + body
    folder = out / 'snapshots' / cell['name']
    folder.mkdir(parents=True, exist_ok=True)
    swf = folder / 'snapshot.swf'
    swf.write_bytes(raw)
    ffdec('-ignorebackground', '-select', '1', '-format', 'frame:png', '-export', 'frame', str(folder / 'render'),
          str(swf), profile=str(out / 'jpexs_profile'))
    images = list((folder / 'render').rglob('*.png'))
    if len(images) != 1:
        raise ValueError('Snapshot render did not produce exactly one image')
    shutil.copyfile(images[0], folder / 'snapshot.png')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('out', type=Path)
    parser.add_argument('--snapshot', default='', help='Comma-separated cells; structural art only')
    args = parser.parse_args()
    movie, manifest, hidden = extract(args.source, args.out)
    for name in filter(None, args.snapshot.split(',')):
        cell = next(cell for cell in manifest['cells'] if cell['name'] == name)
        snapshot(movie, cell, hidden, args.out.resolve())
    print(json.dumps(dict(cells=len(manifest['cells']), spawns=sum(len(c['spawns']) for c in manifest['cells']),
                          exits=sum(len(c['exits']) for c in manifest['cells']), graph_problems=manifest['graph_problems'],
                          geometry_holds=[(c['name'], r['name'], r['geometry_hold']) for c in manifest['cells']
                                          for kind in ('walkables', 'blockers', 'exits') for r in c[kind] if 'geometry_hold' in r]), indent=2))


if __name__ == '__main__':
    main()
