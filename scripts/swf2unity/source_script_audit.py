"""Hold procedural Flash animation that timeline/bitmap comparisons cannot exercise."""
import json
import re
import swfparse
from timeline_scripts import initial_stop

PROCEDURAL = re.compile(r'ENTER_FRAME|EXIT_FRAME|onEnterFrame|onClipEvent|setInterval\s*\(|setTimeout\s*\(|'
    r'TweenLite|TweenMax|new\s+Timer\s*\(|gotoAnd(?:Stop|Play)\s*\(|\b(?:nextFrame|prevFrame)\s*\(|'
    r'addEventListener\s*\(|\.(?:rotation|scaleX|scaleY|alpha|visible)\s*=')


def audit(source, directory, allow_initial_stops=False, allow_random_initial=False, allow_interactive=False, roots=None):
    movie = swfparse.load(str(source))
    reachable = None
    if roots is not None:
        reachable = set()
        def visit(cid):
            if cid in reachable:return
            reachable.add(cid)
            char = movie.chars.get(cid)
            if char is not None and char.kind == 'sprite':
                for frame in char.frames:
                    for placement in frame.places:
                        if placement.char is not None:visit(placement.char)
        for cid in roots:
            assert cid in movie.chars, 'Unknown source audit root'
            visit(cid)
        assert reachable, 'Empty source audit roots'
    symbols = {name.split('.')[-1]: cid for cid,name in movie.symbols.items()}
    counts = {name.split('.')[-1]: len(movie.chars[cid].frames) if cid in movie.chars else len(movie.root)
        for cid, name in movie.symbols.items()}
    files, held, frozen_single_frame, lowered, randomized, compiled, unused = [], [], [], [], [], [], []
    if allow_interactive:
        from interactive_morph import Compiler
        compiler = Compiler(movie, directory)
        assert compiler.controls, 'No declarative selector was compiled'
    for path in directory.rglob('*.as'):
        if reachable is not None and path.stem in symbols and symbols[path.stem] not in reachable:
            unused.append(str(path));continue
        text = path.read_text(encoding='utf-8', errors='replace')
        files.append(str(path))
        match = PROCEDURAL.search(text)
        if allow_interactive and (match or re.search(r'\b(?:stop|play)\s*\(', text)):
            from interactive_morph import source_program
            program = source_program(path)
            if program and program['kind'] in ('timeline', 'parent_play_button', 'random_initial'):
                compiled.append(dict(file=str(path), **program))
                continue
        if match:
            if allow_random_initial:
                from interactive_morph import source_program
                program = source_program(path)
                if program and program['kind'] == 'random_initial':
                    randomized.append(dict(file=str(path), **program))
                    continue
            held.append(dict(file=str(path), trigger=match.group()))
        elif re.search(r'\b(?:stop|play)\s*\(', text):
            program = initial_stop(text)
            if program and allow_initial_stops and program['stopped_frames'] == [0]:
                lowered.append(dict(file=str(path), **program))
            elif counts.get(path.stem) != 1:
                held.append(dict(file=str(path), trigger='play/stop action on a multi-frame or unknown timeline'))
            else:
                frozen_single_frame.append(str(path))
    result = dict(status='held' if held else 'passed', script_files=len(files),
        unsupported=held, single_frame_stop_actions=frozen_single_frame,
        deterministic_initial_stops=lowered, random_initial_selectors=randomized, compiled_interactive_programs=compiled,
        roots=roots, unreachable_symbol_scripts=unused)
    (directory.parent / 'source_script_audit.json').write_text(json.dumps(result, indent=2))
    assert not held, 'Procedural Flash script held: ' + repr(held[:2])
    return result
