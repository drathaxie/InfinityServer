"""Read-only catalog/source survey for the battlepet Forge experiment."""
import hashlib
import json
from pathlib import Path
import shlex

import swfparse as sp
from trial_host import ROOT, remote, server_script

LAB = ROOT / 'outputs/battlepet_forge_lab'
SURVEY = '''import db,json,pathlib
c=db.connect();root=pathlib.Path('/var/www/swordshine/gamefiles');out=[]
for row in c.execute("SELECT id,Name,Description,File,Link,bundle FROM items WHERE Type='Pet' AND id<2000000 ORDER BY id").fetchall():
 r=dict(row);text=(str(r['Name'])+' '+str(r['Description'])).lower()
 if 'battlepet' not in text.replace(' ',''):continue
 p=pathlib.PurePosixPath(r['File'] or '')
 if p.is_absolute() or '..' in p.parts or not str(p).lower().endswith('.swf'):continue
 r['source_present']=(root/p).is_file();r['negative_description']='not a battle pet' in text
 out.append(r)
print(json.dumps(out,default=str));c.close()
'''


def main():
    LAB.mkdir(parents=True, exist_ok=True)
    rows = json.loads(server_script('-', data=SURVEY.encode()))
    inspected = []
    for row in [r for r in rows if r['source_present'] and not r['negative_description']][:6]:
        local = LAB / 'sources' / (str(row['id']) + '_' + Path(row['File']).name)
        local.parent.mkdir(exist_ok=True)
        blob = remote('cat -- ' + shlex.quote('/var/www/swordshine/gamefiles/' + row['File']))
        local.write_bytes(blob)
        movie = sp.load(str(local))
        timelines = []
        for cid, char in movie.chars.items():
            frames = getattr(char, 'frames', None)
            if not frames:
                continue
            labels = {f.label: i for i, f in enumerate(frames) if f.label}
            if labels:
                timelines.append(dict(character=cid, linkage=movie.symbols.get(cid),
                                      frames=len(frames), labels=labels))
        inspected.append(dict(item_id=row['id'], name=row['Name'], source=str(local),
                              sha256=hashlib.sha256(blob).hexdigest(), fps=movie.fps,
                              exports=movie.symbols, labeled_timelines=timelines))
    result = dict(candidates=rows, inspected=inspected,
                  proof_boundary='Description keyword survey, not complete battlepet classification. Source labels are not converted or playable behavior.')
    (LAB / 'source_survey.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(dict(candidates=len(rows), source_present=sum(r['source_present'] for r in rows),
                          inspected=[dict(name=r['name'], timelines=r['labeled_timelines']) for r in inspected]), indent=2))


if __name__ == '__main__':
    main()
