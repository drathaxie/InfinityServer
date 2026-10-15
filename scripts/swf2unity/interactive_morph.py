"""Compile bounded click-to-play Flash controls without stopping their children.

This is an explicit subset of AS3, not an AS3 interpreter. Unknown executable
statements fail before conversion. Inspector-only button properties are inert in
the source Button class and are recorded, never treated as avatar commands.
"""
import copy
import json
import re
import struct
from pathlib import Path

import swfparse as sp
from render import Geometry, IDENT, mat_mul, mat_apply, tag_body


def functions(text):
    """Balanced function bodies (the decompiler includes nested guards/try blocks)."""
    result = {}
    for match in re.finditer(r'function\s+(\w+)\s*\([^)]*\)\s*(?::\s*[\w*]+)?\s*\{', text):
        start, depth, index = match.end(), 1, match.end()
        while depth and index < len(text):
            depth += (text[index] == '{') - (text[index] == '}')
            index += 1
        if depth:
            raise ValueError('Unbalanced source function')
        result[match[1]] = text[start:index - 1].strip()
    return result


def callbacks(body):
    calls = re.findall(r'addFrameScript\(([^)]*)\)', body)
    if len(calls) != 1:
        raise ValueError('Expected one frame script table')
    tokens = [x.strip() for x in calls[0].split(',')]
    if len(tokens) % 2:
        raise ValueError('Invalid frame script table')
    result = {}
    for frame, method in zip(tokens[::2], tokens[1::2]):
        if not frame.isdigit() or not re.fullmatch(r'(?:this\.)?\w+', method):
            raise ValueError('Dynamic frame script table')
        result[int(frame)] = method.split('.')[-1]
    return result


def source_program(path):
    text = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', path.read_text(encoding='utf-8'))
    cls = re.search(r'class\s+(\w+)\s+extends\s+MovieClip', text)
    if not cls:
        return None
    fs = functions(text)
    constructor = fs.get(cls[1], '')
    if 'addFrameScript' not in constructor:
        if re.sub(r'\bsuper\(\)\s*;', '', constructor).strip() or len(fs) != 1:
            raise ValueError('Unsupported constructor: ' + str(path))
        return None
    table = callbacks(constructor)
    remaining = re.sub(r'\bsuper\(\)\s*;|addFrameScript\([^)]*\)\s*;', '', constructor)
    remaining = re.sub(r'this\.__setPropDict\s*=\s*new Dictionary\(true\)\s*;', '', remaining)
    if remaining.strip():
        raise ValueError('Unsupported constructor statement: ' + str(path))
    if table.keys() == {0} and set(fs) == {cls[1], table[0]} and re.sub(r'\s+', '', fs[table[0]]) == 'gotoAndStop(Math.round(Math.random()*(this.totalFrames-1)+1));':
        return dict(kind='random_initial', class_name=cls[1], distribution='rounded_uniform')
    # Color calls are compiled separately by ccscripts.
    if all('mcSetColor(' in fs[method] for method in table.values()):
        if set(fs) != {cls[1], *table.values()}:
            raise ValueError('Uncompiled functions in a color clip: ' + str(path))
        for method in table.values():
            body = fs[method]
            wrapped = re.fullmatch(r'try\s*\{\s*([^{}]+)\s*\}\s*catch\(e:Error\)\s*\{\s*\}', body)
            if wrapped:
                body = wrapped[1].strip()
            if not re.fullmatch(r'MovieClip\([^;]+\)\.mcSetColor\(this,"\w+","\w*"\);', body):
                raise ValueError('Color callback contains other behavior: ' + str(path))
        return None
    if 'MouseEvent.CLICK' in text:
        if len(table) != 1 or 0 not in table:
            raise ValueError('Click handler installation changes by frame')
        body = fs[table[0]]
        match = re.fullmatch(r'this\.(\w+)\.addEventListener\(MouseEvent.CLICK,this\.(\w+),false,0,true\);\s*stop\(\);', body)
        if not match or fs.get(match[2]) != 'MovieClip(parent).play();' or set(fs) != {cls[1], table[0], match[2]}:
            raise ValueError('Unsupported click action: ' + str(path))
        return dict(kind='parent_play_button', class_name=cls[1], button=match[1])
    stops, jumps, inspector = [], {}, {}
    used = {cls[1], *table.values()}
    for frame, method in table.items():
        body = fs[method]
        if re.fullmatch(r'(?:this\.)?stop\(\);', body):
            stops.append(frame)
            continue
        jump = re.fullmatch(r'gotoAndPlay\("(\w+)"\);', body)
        if jump:
            jumps[frame] = jump[1]
            continue
        # Some Adobe exports put the complete inspector guard in a setter,
        # and the frame callback only invokes that setter before stopping.
        call = re.fullmatch(r'this\.(\w+)\(\);\s*stop\(\);', body)
        if call:
            setter = fs.get(call[1], '')
            direct = re.fullmatch(r'if\(this\.__setPropDict\[this\.(\w+)\] == undefined \|\| int\(this\.__setPropDict\[this\.\1\]\) != (\d+)\)\s*\{\s*this\.__setPropDict\[this\.\1\] = \2;([\s\S]*)\}', setter)
            if not direct or int(direct[2]) != frame + 1:
                raise ValueError('Unsupported inspector setter call: ' + str(path))
            instance = re.escape(direct[1])
            assignments_body = re.sub(r'try\s*\{\s*this\.' + instance + r'\["componentInspectorSetting"\] = (?:true|false);\s*\}\s*catch\(e:Error\)\s*\{\s*\}', '', direct[3])
            assignments = re.findall(r'this\.' + instance + r'\.(\w+) = ("[^"]*"|true|false|\d+);', assignments_body)
            remainder = re.sub(r'this\.' + instance + r'\.\w+ = (?:"[^"]*"|true|false|\d+);', '', assignments_body)
            allowed = {'strAction', 'strFrame', 'strPad', 'strString', 'intID', 'bitCloseFront', 'bitCloseUI', 'intMinLevel'}
            if remainder.strip() or len(assignments) != len(allowed) or {a for a, _ in assignments} != allowed:
                raise ValueError('Inspector setter has executable behavior: ' + str(path))
            inspector[frame] = dict(instance=direct[1], properties=dict(assignments))
            used.add(call[1]);stops.append(frame)
            continue
        # Adobe inspector-generated setters only assign unused dynamic properties
        # to the simple local parent-play button. Validate the whole guard/body.
        guard = re.fullmatch(r'if\(this\.__setPropDict\[this\.(\w+)\] == undefined \|\| int\(this\.__setPropDict\[this\.\1\]\) != (\d+)\)\s*\{\s*this\.__setPropDict\[this\.\1\] = \2;\s*this\.(\w+)\(\);\s*\}\s*stop\(\);', body)
        if not guard or int(guard[2]) != frame + 1:
            raise ValueError('Unsupported frame callback: ' + str(path))
        setter = fs.get(guard[3], '')
        target = re.escape(guard[1])
        setter = re.sub(r'try\s*\{\s*this\.' + target + r'\["componentInspectorSetting"\] = (?:true|false);\s*\}\s*catch\(e:Error\)\s*\{\s*\}', '', setter)
        assignments = re.findall(r'this\.' + target + r'\.(\w+) = ("[^"]*"|true|false|\d+);', setter)
        remainder = re.sub(r'this\.' + target + r'\.\w+ = (?:"[^"]*"|true|false|\d+);', '', setter)
        allowed = {'strAction', 'strFrame', 'strPad', 'strString', 'intID', 'bitCloseFront', 'bitCloseUI', 'intMinLevel'}
        if remainder.strip() or {a for a, _ in assignments} != allowed:
            raise ValueError('Inspector setter has executable behavior: ' + str(path))
        inspector[frame] = dict(instance=guard[1], properties=dict(assignments))
        used.add(guard[3])
        stops.append(frame)
    if set(fs) != used or not stops:
        raise ValueError('Uncompiled source functions: ' + str(path))
    return dict(kind='timeline', class_name=cls[1], stops=sorted(stops), jumps=jumps, inspector=inspector)


def hold(movie, cid, frame):
    """Use a complete display-list snapshot, including masks, filters and names."""
    ch = movie.chars[cid]
    state = sp.display_lists(ch.frames)[frame]
    places = [sp.Place(depth=d, char=s['char'], matrix=s['matrix'], cxform=s['cxform'],
        ratio=s.get('ratio'), name=s.get('name'), clip_depth=s.get('clip'), blend=s.get('blend'),
        filters_raw=s.get('filters_raw', b'')) for d, s in state.items()]
    old = ch.raw
    ch.frames = [sp.Frame(places=places)]
    ch.raw = sp.define_sprite(cid, 1, b''.join(sp.encode_place(p) for p in places) + sp.tag(1, b''))
    movie.raw_defs = [ch.raw if raw == old else raw for raw in movie.raw_defs]


class Compiler:
    def __init__(self, movie, scripts):
        self.movie, self.original = movie, copy.deepcopy(movie)
        self.render_fallbacks = []
        symbols = {name.split('.')[-1]: cid for cid, name in movie.symbols.items()}
        self.programs = {}
        for path in Path(scripts).rglob('*.as'):
            program = source_program(path)
            if program:
                cid = symbols[program['class_name']]
                self.programs[cid] = program
        buttons = {cid for cid, p in self.programs.items() if p['kind'] == 'parent_play_button'}
        self.hit_only_buttons = set()
        # A hit-only SimpleButton has no Up/Over/Down artwork. Removing its
        # invisible placement from render-only copies avoids blank layers.
        for cid in buttons:
            children = [p.char for f in movie.chars[cid].frames for p in f.places]
            hit_only = True
            for child in children:
                button = movie.chars[child]
                if button.kind != 'button' or button.tag != 34:
                    hit_only = False
                    break
                body, pos = tag_body(button.raw), 5
                while body[pos]:
                    flags = body[pos]; pos += 5
                    _, pos = sp.read_matrix(body, pos)
                    _, pos = sp.read_cxform(body, pos, True)
                    if flags & 0x10:
                        pos = sp.skip_filters(body, pos)
                    if flags & 0x20:
                        pos += 1
                    hit_only = hit_only and not flags & 7
            if hit_only:
                self.hit_only_buttons.add(cid)
                old = movie.chars[cid].raw
                movie.chars[cid].frames = [sp.Frame()]
                movie.chars[cid].raw = sp.define_sprite(cid, 1, sp.tag(1, b''))
                movie.raw_defs = [movie.chars[cid].raw if r == old else r for r in movie.raw_defs]
        self.controls = {}
        for cid, program in self.programs.items():
            if program['kind'] == 'random_initial':
                count = len(movie.chars[cid].frames)
                if not 2 <= count <= 256:
                    raise ValueError('Random initial timeline must have 2..256 frames')
                self.controls[cid] = dict(program, stops=list(range(count)))
                hold(movie, cid, 0)
                continue
            if program['kind'] != 'timeline':
                continue
            ch = movie.chars[cid]
            children = {p.char for f in ch.frames for p in f.places}
            if children & buttons:
                if program['jumps']:
                    raise ValueError('Click timeline has unsupported jump')
                self.controls[cid] = program
                for info in program['inspector'].values():
                    child = next((s['char'] for dl in sp.display_lists(ch.frames) for s in dl.values()
                        if s.get('name') == info['instance']), None)
                    if child not in buttons:
                        raise ValueError('Inspector properties target a non-local button')
            elif 0 not in program['stops']:
                raise ValueError('Uncontrolled timeline starts playing: ' + program['class_name'])
            # Default stopped timelines keep their child animation independent.
            hold(movie, cid, 0)

    def reachable_controls(self, root):
        seen, found = set(), set()
        def visit(cid):
            if cid in seen:
                return
            seen.add(cid)
            if cid in self.controls:
                found.add(cid)
            ch = self.original.chars.get(cid)
            if ch and ch.kind == 'sprite':
                for f in ch.frames:
                    for p in f.places:
                        if p.char is not None:
                            visit(p.char)
        visit(root)
        return found

    def clone_pose(self, root, control, frame):
        """Clone only ancestors of the controlled clip; share animated children."""
        cache = {}
        def clone(cid):
            if cid in cache:
                return cache[cid]
            if control not in self.reachable_controls(cid):
                return cid
            original = self.original.chars[cid]
            new_id = max(self.movie.chars) + 1
            ch = copy.deepcopy(original)
            ch.id = new_id
            # The clone must never replace its original definition in raw_defs.
            ch.raw = b''
            self.movie.chars[new_id] = ch
            cache[cid] = new_id
            if cid == control:
                hold(self.movie, new_id, frame)
            else:
                hold(self.movie, new_id, 0)
            for p in ch.frames[0].places:
                p.char = clone(p.char)
            ch.raw = sp.define_sprite(new_id, 1, b''.join(sp.encode_place(p) for p in ch.frames[0].places) + sp.tag(1, b''))
            self.movie.raw_defs.append(ch.raw)
            return new_id
        return clone(root)

    def decompose(self, rig, root, layer_budget=128):
        controls = self.reachable_controls(root)
        if not controls:
            return rig.decompose(root), None
        if len(controls) != 1:
            raise ValueError('Multiple independent controls in one part require a multi-selector')
        control = controls.pop()
        poses, roots = [], []
        count = len(self.original.chars[control].frames)
        for frame in range(count):
            pose = self.clone_pose(root, control, frame)
            roots.append(pose)
            pose_layers = rig.decompose(pose)
            for layer in pose_layers:
                if layer.kind == 'band':
                    layer.items = [item for item in layer.items if item.char not in self.hit_only_buttons]
            pose_layers = [layer for layer in pose_layers if layer.items]
            for layer in pose_layers:
                layer.morph_frame = frame
            poses.append(pose_layers)
        if sum(map(len, poses)) > layer_budget:
            from rig import Layer, Item
            # Dense effect emitters can require hundreds of tracks per form.
            # Rasterize the complete form over its exact child-clock period,
            # preserving all masks, filters and painter order in one layer.
            for frame, (pose, pose_layers) in enumerate(zip(roots, poses)):
                if len(pose_layers) <= 1:
                    continue
                period = rig.period(pose)
                if rig.notes:
                    raise ValueError('Interactive pose requires an uncapped clock: ' + repr(rig.notes))
                layer = Layer(kind='flipbook', sprite=pose, period=period,
                              frames=[dict(m=IDENT, cx=None, vis=True) for _ in range(period)],
                              items=[Item(pose, IDENT)])
                layer.morph_frame = frame
                poses[frame] = [layer]
                self.render_fallbacks.append(dict(control=control, state=frame,
                    original_layers=len(pose_layers), exact_period=period))
        # Shared prefix artwork stays outside the selector. Child clocks are
        # unchanged, while duplicated unchanged static bands need no storage.
        prefix = []
        while all(pose for pose in poses) and all(repr(pose[0]) == repr(poses[0][0]) for pose in poses):
            if poses[0][0].kind != 'band' or poses[0][0].group_start or poses[0][0].group_end >= 0:
                break
            layer = poses[0][0]
            layer.morph_frame = -1
            prefix.append(layer)
            for pose in poses:
                pose.pop(0)
        layers = prefix + [layer for pose in poses for layer in pose]
        program = dict(frame_count=count, fps=self.original.fps, stops=self.controls[control]['stops'],
                       initial_playing=0 not in self.controls[control]['stops'],
                       random_initial=self.controls[control]['kind'] == 'random_initial',
                       hit_frames=[self.hit_polygons(root, control, frame) for frame in range(count)])
        return layers, program

    def hit_polygons(self, root, control, frame):
        """Conservative HitTest-state bounds transformed into part twips."""
        polygons = []
        geo = Geometry(self.original)
        def visit(cid, matrix, stack=()):
            if cid in stack:
                raise ValueError('Recursive click hierarchy')
            ch = self.original.chars.get(cid)
            if not ch:
                return
            if ch.kind == 'button':
                if ch.tag != 34:
                    raise ValueError('Only DefineButton2 hit geometry is implemented')
                body = tag_body(ch.raw)
                pos = 5
                while body[pos]:
                    flags = body[pos]; pos += 1
                    child, depth = struct.unpack_from('<HH', body, pos); pos += 4
                    mat, pos = sp.read_matrix(body, pos)
                    _, pos = sp.read_cxform(body, pos, True)
                    if flags & 0x10:
                        pos = sp.skip_filters(body, pos)
                    if flags & 0x20:
                        pos += 1
                    if flags & 8:
                        bounds = geo.bounds(child)
                        if bounds is None:
                            raise ValueError('Missing button hit bounds')
                        # Keep the hit state's bounds, not the much larger visible part bounds.
                        points = [(bounds[0], bounds[2]), (bounds[1], bounds[2]),
                                  (bounds[1], bounds[3]), (bounds[0], bounds[3])]
                        full = mat_mul(matrix, mat)
                        polygons.append([coordinate for p in points for coordinate in mat_apply(full, *p)])
                return
            if ch.kind == 'sprite':
                selected = frame if cid == control else 0
                for s in sp.display_lists(ch.frames)[selected].values():
                    visit(s['char'], mat_mul(matrix, s['matrix'] or IDENT), stack + (cid,))
        visit(root, IDENT)
        return polygons


def step(program, frame, playing, click=False):
    """Reference control clock; one call is one Flash frame tick."""
    if click:
        playing = True
    if playing:
        frame = (frame + 1) % program['frame_count']
        playing = frame not in program['stops']
    return frame, playing
