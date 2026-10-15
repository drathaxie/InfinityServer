"""Lower deterministic initial-frame stop scripts without freezing child timelines."""
import re

import swfparse as sp


def initial_stop(text):
    """Accept only a constructor plus straight-line stop frame callbacks."""
    text = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', text)
    cls = re.search(r'class\s+(\w+)\s+extends\s+MovieClip', text)
    functions = re.findall(r'function\s+(\w+)\s*\([^)]*\)\s*(?::\s*[\w*]+)?\s*\{([^{}]*)\}', text)
    if not cls or not functions:
        return None
    constructor = next((body for name, body in functions if name == cls[1]), '')
    calls = re.findall(r'addFrameScript\(([^)]*)\)', constructor)
    if len(calls) != 1:
        return None
    remaining = re.sub(r'\bsuper\(\)\s*;|addFrameScript\([^)]*\)\s*;', '', constructor)
    if remaining.strip():
        return None
    tokens = [t.strip() for t in calls[0].split(',')]
    if len(tokens) % 2:
        return None
    callbacks = {}
    for index, callback in zip(tokens[::2], tokens[1::2]):
        match = re.fullmatch(r'(?:this\.)?(\w+)', callback)
        if not index.isdigit() or not match:
            return None
        callbacks[int(index)] = match[1]
    bodies = dict(functions)
    if 0 not in callbacks or any(not re.fullmatch(r'\s*(?:this\.)?stop\(\)\s*;\s*', bodies.get(fn, ''))
                                 for fn in callbacks.values()):
        return None
    if set(bodies) != {cls[1], *callbacks.values()}:
        return None
    return dict(class_name=cls[1], stopped_frames=sorted(callbacks))


def lower_initial_stops(movie, directory):
    symbols = {name.split('.')[-1]: cid for cid, name in movie.symbols.items()}
    lowered = []
    for path in directory.rglob('*.as'):
        program = initial_stop(path.read_text(encoding='utf-8', errors='replace'))
        if not program or program['class_name'] not in symbols:
            continue
        cid = symbols[program['class_name']]
        ch = movie.chars.get(cid)
        if ch is None or ch.kind != 'sprite' or len(ch.frames) <= 1:
            continue
        old = ch.raw
        # Retain original placement tags, including masks, filters and instance
        # names. Only the stopped parent's timeline changes; descendants play.
        _, body, _ = next(sp.iter_tags(old, 0, len(old)))
        tags = []
        for code, _, raw in sp.iter_tags(body, 4, len(body)):
            tags.append(raw)
            if code == 1:
                break
        else:
            raise ValueError('Stopped sprite has no first ShowFrame')
        ch.raw = sp.define_sprite(cid, 1, b''.join(tags))
        ch.frames = ch.frames[:1]
        movie.raw_defs = [ch.raw if raw == old else raw for raw in movie.raw_defs]
        lowered.append(dict(symbol=cid, source_file=str(path), **program,
                            semantics='Initial parent frame held; child clocks preserved',
                            additional_stopped_states=program['stopped_frames'][1:]))
    return lowered
