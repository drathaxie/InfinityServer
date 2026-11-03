"""Extend native sword candidates only after proving each exact source is public Flash art."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import urllib.parse
import urllib.request

from trial_host import ROOT, server_script

BASE = ROOT / 'outputs/flash_converter_trial/native_replacements'


def header(relative):
    url = 'https://game.aq.com/game/gamefiles/' + urllib.parse.quote(relative, safe='/')
    result = dict(relative=relative, url=url)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=15) as response:
            result.update(status=response.status, type=response.headers.get_content_type(),
                          bytes=response.headers.get('Content-Length'))
    except Exception as error:
        result.update(status=0, error=str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', type=int, default=1024)
    args = parser.parse_args()
    assert 1 <= args.size <= 1024
    path = BASE / 'discovery.json'
    discovery = json.loads(path.read_text())
    existing = [e['template']['Bundle']['ID'] for e in discovery['candidates']]
    script = 'EXISTING=' + repr(existing) + '\nLIMIT=' + str(args.size) + '\n' + r'''
import db,json,re
from pathlib import Path
registry=json.loads(Path('/opt/infinity/data/asset_bundles.json').read_text())
base=Path('/var/www/swordshine/gamefiles').resolve()
seen=set(EXISTING);candidates=[];c=db.connect()
try:
 rows=c.execute("SELECT * FROM items WHERE Type='Sword' AND JSON_VALID(bundle) AND id<2000000 ORDER BY id DESC").fetchall()
 for row in rows:
  original=dict(row);inline=json.loads(original['bundle']);bid=int(inline.get('ID') or 0)
  if bid in seen or not 0<bid<2000000: continue
  relative=original.get('File') or ''
  if not re.fullmatch(r'items/swords/[^/\\]+\.swf',relative) or '..' in relative: continue
  if 'caladbolg' in (relative+' '+(original.get('Name') or '')).lower(): continue
  if not original.get('Link') or not original.get('prefab_name'): continue
  bundle=registry.get(str(bid)) or {};filename=bundle.get('FileName') or bundle.get('Filename') or ''
  if not filename.startswith('items/swords/') or not filename.endswith('.unity3d'): continue
  source=(base/relative).resolve()
  if not source.is_relative_to(base) or not source.is_file() or not 0<source.stat().st_size<=150000: continue
  template=db.item(c,original['id'])
  if not template or template.get('Bundle',{}).get('ID')!=bid: continue
  seen.add(bid)
  candidates.append(dict(original=original,template=template,source_bytes=source.stat().st_size))
  if len(candidates)>=LIMIT: break
finally: c.close()
print(json.dumps(candidates,default=str))
'''
    candidates = json.loads(server_script('-', data=script.encode()))
    print('DISCOVERED', len(candidates), 'additional native sword candidates', flush=True)
    headers_path = BASE / 'public_source_headers.json'
    headers = {e['relative']: e for e in json.loads(headers_path.read_text())}
    needed = sorted({e['original']['File'] for e in candidates} - set(headers))
    with ThreadPoolExecutor(max_workers=8) as executor:
        for count, result in enumerate(executor.map(header, needed), 1):
            headers[result['relative']] = result
            if count % 128 == 0:
                print('PUBLIC_HEADERS', count, '/', len(needed), flush=True)
    accepted = [e for e in candidates if headers[e['original']['File']].get('status') == 200
                and headers[e['original']['File']].get('type') == 'application/x-shockwave-flash']
    headers_path.write_text(json.dumps(list(headers.values()), indent=2))
    (BASE / 'discovery_extension.json').write_text(json.dumps(dict(candidates=len(candidates),
        accepted=len(accepted), unavailable=[e for e in candidates if e not in accepted]), indent=2))
    discovery['candidates'].extend(accepted)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(discovery, indent=2))
    temporary.replace(path)
    print('ACCEPTED', len(accepted), 'public Flash sources; total candidates', len(discovery['candidates']), flush=True)


if __name__ == '__main__':
    main()
