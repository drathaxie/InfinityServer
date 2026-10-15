"""Read/capture selected unconverted swords and screen their rigs before expensive rendering."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
from trial_host import server_script, remote
import rig
import swfparse as sp


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('output',type=Path);ap.add_argument('ids',nargs='+',type=int)
    args=ap.parse_args();assert all(i>0 for i in args.ids)
    ids=','.join(str(i) for i in args.ids)
    script="import db,json\nc=db.connect()\nprint(json.dumps([dict(r) for r in c.execute(\"SELECT id,Name,File,Link,Type FROM items WHERE bundle IS NULL AND Type='Sword' AND id IN ("+ids+")\").fetchall()]))\nc.close()"
    rows=json.loads(server_script('-',data=script.encode()))
    args.output.mkdir(parents=True,exist_ok=True)
    for row in rows:
        relative=row['File'];assert not Path(relative).is_absolute() and '..' not in relative and '\\' not in relative
        raw=remote('cat -- '+shlex.quote('/var/www/swordshine/gamefiles/'+relative))
        assert raw[:3] in (b'FWS',b'CWS',b'ZWS')
        path=args.output/Path(relative).name;path.write_bytes(raw)
        movie=sp.load(raw);source_rig=rig.Rig(movie)
        cid=next((c for c,n in movie.symbols.items() if n==row['Link']),None)
        if cid is None:
            row.update(local_source=str(path.resolve()),holds=['catalog linkage absent from source symbols'])
            print(row['id'],row['Name'],row['holds'],flush=True)
            continue
        layers=source_rig.decompose(cid)
        row.update(local_source=str(path.resolve()),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),
            layers=[dict(kind=l.kind,period=l.period) for l in layers],holds=source_rig.notes)
        print(row['id'],row['Name'],row['layers'],row['holds'],flush=True)
    (args.output/'survey.json').write_text(json.dumps(rows,indent=2))


if __name__=='__main__':main()
