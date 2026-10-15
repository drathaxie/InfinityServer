"""Compile the explicit battlepet root-script subset; no animation baking yet.

Unknown constructors, callbacks, functions, labels and callback cycles fail closed.
This records control flow rather than assuming every labeled range plays through.
"""
import hashlib
import json
from pathlib import Path
import re

from interactive_morph import callbacks, functions
import swfparse as sp
from trial_host import ROOT


def compile_root(movie, cid, text):
    text = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*', '', text)
    cls = re.search(r'class\s+(\w+)\s+extends\s+MovieClip', text)
    if not cls:
        raise ValueError('Expected exported MovieClip root')
    fs = functions(text)
    constructor = fs.get(cls[1], '')
    table = callbacks(constructor)
    remainder = re.sub(r'\bsuper\(\)\s*;|addFrameScript\([^)]*\)\s*;', '', constructor)
    if remainder.strip() or set(fs) != {cls[1], *table.values()}:
        raise ValueError('Unsupported battlepet constructor/functions')
    frames = movie.chars[cid].frames
    labels = {f.label: i for i, f in enumerate(frames) if f.label}
    if not {'Ready', 'Idle', 'Walk', 'Attack1', 'Attack2'}.issubset(labels):
        raise ValueError('Missing battlepet root labels')
    actions = {}
    for frame, method in table.items():
        if not 0 <= frame < len(frames):
            raise ValueError('Callback outside timeline')
        body = re.sub(r'\s+', '', fs[method])
        if body in ('stop();', 'this.stop();'):
            action = {'op': 'stop'}
        elif match := re.fullmatch(r'gotoAnd(Play|Stop)\("(\w+)"\);', body):
            if match[2] not in labels:
                raise ValueError('Unknown callback label')
            action = {'op': 'jump', 'frame': labels[match[2]], 'playing': match[1] == 'Play'}
        elif match := re.fullmatch(r'if\(this.onMove\)\{gotoAndPlay\("(\w+)"\);\}', body):
            if match[1] != 'Walk':
                raise ValueError('Unexpected movement branch')
            action = {'op': 'walk_if_moving', 'frame': labels['Walk']}
        else:
            raise ValueError('Unsupported battlepet callback: ' + method)
        actions[str(frame)] = action
    program = dict(frame_count=len(frames), fps=movie.fps, labels=labels,
                   actions=actions, child_clock_policy='Preserve child timelines while root stops; reset only on source instance replacement.')
    clock = Clock(program)
    clock.enter('Ready')
    if clock.frame != labels['Idle'] or clock.playing:
        raise ValueError('Initial root does not enter stopped Idle')
    return program


class Clock:
    """Reference root clock, one tick per source frame; children are not advanced here."""
    def __init__(self, program):
        self.program = program
        self.frame, self.playing, self.moving = 0, True, False

    def _callbacks(self):
        seen = set()
        while True:
            if self.frame in seen:
                raise ValueError('Cyclic callbacks without a frame tick')
            seen.add(self.frame)
            action = self.program['actions'].get(str(self.frame), {})
            if action.get('op') == 'stop':
                self.playing = False
                return
            if action.get('op') == 'jump' or action.get('op') == 'walk_if_moving' and self.moving:
                self.frame = action['frame']
                self.playing = action.get('playing', True)
                continue
            return

    def enter(self, label, *, moving=False):
        self.frame = self.program['labels'][label]
        self.playing, self.moving = True, moving
        self._callbacks()

    def tick(self):
        if self.playing:
            self.frame = (self.frame + 1) % self.program['frame_count']
            self._callbacks()
        return self.frame


def main():
    lab = ROOT/'outputs/battlepet_forge_lab'
    survey = json.loads((lab/'source_survey.json').read_text(encoding='utf-8'))
    entries, checks = [], []
    for source in survey['inspected']:
        if hashlib.sha256(Path(source['source']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('Captured pet source changed since inspection')
        movie = sp.load(source['source'])
        timeline = next(t for t in source['labeled_timelines'] if {'Ready','Idle','Attack1','Attack2'} <= set(t['labels']))
        stem = Path(source['source']).stem
        scripts = list((lab/'scripts'/stem).rglob(timeline['linkage'].split('.')[-1] + '.as'))
        if len(scripts) != 1:
            raise ValueError('Ambiguous root script')
        script = scripts[0].read_text(encoding='utf-8')
        program = compile_root(movie, timeline['character'], script)
        clock = Clock(program)
        clock.enter('Ready')
        idle = clock.frame
        for _ in range(1000):
            assert clock.tick() == idle and not clock.playing
        checks.append(source['name'] + ': stopped Idle root retained for 1000 ticks')
        clock.enter('Walk', moving=True)
        for _ in range(200):
            assert program['labels']['Walk'] <= clock.tick() < program['labels']['Attack1']
        checks.append(source['name'] + ': moving Walk loop bounded')
        entry_results = {}
        for label in ('Attack1', 'Attack2', 'Buff', 'PowerUp'):
            if label not in program['labels']:
                continue
            clock.enter(label)
            entered = clock.frame
            elapsed = 0
            while clock.playing and elapsed <= program['frame_count'] * 2:
                clock.tick(); elapsed += 1
            assert clock.frame == idle and not clock.playing
            entry_results[label] = dict(requested_frame=program['labels'][label], effective_entry=entered,
                                        ticks_to_idle=elapsed, immediate_redirect=entered != program['labels'][label])
            checks.append(source['name'] + ': ' + label + ' returns to stopped Idle')
        # Prove executable statements cannot hide behind a supported callback.
        injected = script.replace('stop();', 'stop(); play();', 1)
        try:
            compile_root(movie, timeline['character'], injected)
        except ValueError:
            checks.append(source['name'] + ': extra executable statement rejected')
        else:
            raise AssertionError('Unsupported statement compiled')
        entries.append(dict(item_id=source['item_id'], name=source['name'],
                            source_sha256=source['sha256'], root_script_sha256=hashlib.sha256(script.encode()).hexdigest(),
                            program=program, command_behavior=entry_results))
    result = dict(status='passed', checks=checks, entries=entries,
                  proof_boundary='Compiled and simulated root script control flow only; child clock baking, native clips and runtime rendering remain pending.')
    (lab/'state_programs.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(dict(status='passed', pets=len(entries), checks=len(checks),
                          immediate_redirects=[dict(name=e['name'],state=s) for e in entries for s,b in e['command_behavior'].items() if b['immediate_redirect']]), indent=2))


if __name__ == '__main__':
    main()
