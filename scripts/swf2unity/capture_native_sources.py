"""Capture only the public Flash sword art listed in the native-replacement discovery."""
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from trial_host import ROOT, server_script


def main():
    folder = ROOT / 'outputs/flash_converter_trial/native_replacements'
    discovery = json.loads((folder / 'discovery.json').read_text())
    headers = {h['relative']: h for h in json.loads((folder / 'public_source_headers.json').read_text())}
    existing = json.loads((folder / 'source_manifest.json').read_text()) if (folder / 'source_manifest.json').exists() else {}
    relatives = sorted({r['original']['File'] for r in discovery['candidates']} - set(existing))
    assert 1 <= len(relatives) <= 1024
    for relative in relatives:
        assert re.fullmatch(r'items/swords/[^/\\]+\.swf', relative) and '..' not in relative, relative
        assert headers[relative]['status'] == 200 and headers[relative]['type'] == 'application/x-shockwave-flash'
    script = 'RELATIVES=' + repr(relatives) + '\n' + '''
import io,tarfile,sys,hashlib,json
from pathlib import Path
stream=io.BytesIO();manifest={}
with tarfile.open(fileobj=stream,mode='w') as out:
 for relative in RELATIVES:
  file=Path('/var/www/swordshine/gamefiles')/relative
  data=file.read_bytes();assert data[:3] in (b'FWS',b'CWS',b'ZWS')
  manifest[relative]=dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
  info=tarfile.TarInfo(relative);info.size=len(data);out.addfile(info,io.BytesIO(data))
 data=json.dumps(manifest).encode();info=tarfile.TarInfo('manifest.json');info.size=len(data);out.addfile(info,io.BytesIO(data))
sys.stdout.buffer.write(stream.getvalue())
'''
    raw = server_script('-', data=script.encode())
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        manifest = json.load(archive.extractfile('manifest.json'))
        assert set(manifest) == set(relatives)
        for relative in relatives:
            content = archive.extractfile(relative).read()
            assert hashlib.sha256(content).hexdigest() == manifest[relative]['sha256']
            target = folder / 'sources' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    existing.update(manifest)
    (folder / 'source_manifest.json').write_text(json.dumps(existing, indent=2))
    print('captured', len(manifest), 'public sword SWFs,', sum(s['bytes'] for s in manifest.values()), 'bytes')


if __name__ == '__main__':
    main()
