"""Read-only script feature inventory for specialty sources already captured."""
import json
from pathlib import Path
import re
import ccscripts
import swfparse
from trial_host import ROOT

base = ROOT / 'outputs/flash_converter_trial/native_replacements'
rows = json.loads((base/'native13/candidate_plan.json').read_text())
prepared = {r['native_bundle_id'] for r in json.loads((base/'native13/prepared_plan.json').read_text())}
output = base/'specialty_probes';output.mkdir(exist_ok=True)
checks = []
for row in rows:
    bid = row['template']['Bundle']['ID']
    if bid in prepared or row['original']['Type'] not in ('Helm','Gun','Gauntlet'):
        continue
    folder = output/str(bid);folder.mkdir(exist_ok=True)
    source = base/'specialty_sources'/row['original']['File']
    movie = swfparse.load(str(source))
    colors = ccscripts.cc_chars(movie,str(source),str(folder/'scripts'))
    patterns = []
    for path in (folder/'scripts').rglob('*.as'):
        text = path.read_text(encoding='utf-8',errors='replace')
        for match in re.finditer(r'[^\n]*(?:Math\.random|MouseEvent\.|ENTER_FRAME|nextFrame\(|gotoAnd(?:Stop|Play)\(|new Sound|SoundChannel)[^\n]*',text):
            patterns.append(dict(file=str(path),statement=match[0].strip()))
    result = dict(bundle_id=bid,name=row['original']['Name'],type=row['original']['Type'],
        colors=sorted({loc for loc,_ in colors.values()}),patterns=patterns,
        exported_timelines={name:len(movie.chars[cid].frames) for cid,name in movie.symbols.items()})
    checks.append(result);(output/'script_features.json').write_text(json.dumps(checks,indent=2))
    print('PROBED',bid,result['type'],result['name'],'patterns',patterns[:3],flush=True)
    if len(checks)>=6:break
