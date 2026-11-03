"""Source-only Armored Daimyo timeline preview; not a Unity runtime proof."""
import json
from PIL import Image, ImageDraw

import render
import swfparse as sp
from trial_host import ROOT


def main():
    lab = ROOT / 'outputs/battlepet_forge_lab'
    survey = json.loads((lab / 'source_survey.json').read_text(encoding='utf-8'))
    source = next(r for r in survey['inspected'] if r['item_id'] == 6673)
    movie = sp.load(source['source'])
    timeline = next(t for t in source['labeled_timelines'] if t['linkage'] == 'ArmoredDaimyoBattle')
    result = render.render_jobs(movie, {}, [dict(items=[dict(char=timeline['character'])],
        frames=timeline['frames'])], 4., str(lab / 'source_preview'))[0]
    # Explicit callbacks from exported source: Idle stops at frame 7; Walk loops
    # 16..27 while onMove; attack 29..58 returns to stopped Idle at frame 7.
    # These previews display source timeline artwork only, without Flash runtime
    # callbacks or independently stopped/playing child-clock validation.
    sequence = [('Idle', [7] * 12), ('Walk', list(range(16, 28)) * 2),
                ('Attack1', list(range(29, 59))), ('Idle', [7] * 12),
                ('Attack2', list(range(59, 78))), ('Buff', list(range(78, 102)))]
    frames, examples = [], []
    for state, indices in sequence:
        for index in indices:
            art = result['images'][index]
            # Long-range effects make the union canvas thousands of pixels wide.
            # Bound only this presentation copy, preserving one common scale.
            art.thumbnail((608, 300), Image.Resampling.LANCZOS)
            canvas = Image.new('RGB', (max(360, art.width + 32), art.height + 96), '#172432')
            canvas.paste(art, ((canvas.width - art.width) // 2, 58), art)
            draw = ImageDraw.Draw(canvas)
            draw.text((16, 12), 'Armored Daimyo - ' + state, fill='white')
            draw.text((16, 30), 'Flash source artwork / Forge research', fill='#b4c5d5')
            frames.append(canvas)
        if state != 'Idle' or not examples:
            pose = result['images'][indices[len(indices) // 2]]
            pose = pose.crop(pose.getchannel('A').getbbox() or (0, 0, 1, 1))
            pose.thumbnail((328, 220), Image.Resampling.LANCZOS)
            example = Image.new('RGB', (360, 300), '#172432')
            example.paste(pose, ((360 - pose.width) // 2, 48 + (220 - pose.height) // 2), pose)
            draw = ImageDraw.Draw(example)
            draw.text((16, 14), 'Armored Daimyo - ' + state, fill='white')
            draw.text((16, 280), 'Flash source pose / scale varies per panel', fill='#b4c5d5')
            examples.append(example)
    output = lab / 'armored_daimyo_source.gif'
    pending = output.with_name(output.stem + '.next.gif')
    frames[0].save(pending, save_all=True, append_images=frames[1:],
                   duration=round(1000 / movie.fps), loop=0)
    pending.replace(output)
    sheet = Image.new('RGB', (examples[0].width * len(examples), examples[0].height), '#172432')
    for column, example in enumerate(examples):
        sheet.paste(example, (column * example.width, 0))
    sheet.save(lab / 'armored_daimyo_source.png')
    contract = dict(item_id=6673, source_sha256=source['sha256'], fps=movie.fps,
                    labels=timeline['labels'], preview=str(output),
                    proof_boundary='Source artwork sequence only; no native pet conversion, independent child-clock or gameplay proof.')
    (lab / 'source_preview_contract.json').write_text(json.dumps(contract, indent=2), encoding='utf-8')
    print(json.dumps(dict(preview=str(output), frames=len(frames), bytes=output.stat().st_size)))


if __name__ == '__main__':
    main()
