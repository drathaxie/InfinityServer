"""Read a few unconverted pet records and copy their existing SWFs for local trials."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'outputs/flash_converter_trial/pet_candidates'
SSH = ['ssh', '-i', str(Path.home() / 'Downloads/ssh-key-2026-06-19.key'),
       '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', 'ubuntu@130.162.189.229']
IDS = (167, 323, 521, 5553)


def remote(command):
    result = subprocess.run(SSH + [command], capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    query = ('SELECT id,Name,File,Link FROM items WHERE Type=\'Pet\' AND bundle IS NULL AND id IN (' +
             ','.join(map(str, IDS)) + ') ORDER BY id')
    records = []
    for line in remote('sudo mysql aqwworlds -N -e ' + shlex.quote(query)).decode().splitlines():
        item_id, name, filename, linkage = line.split('\t')
        if not filename.startswith('items/pets/') or '..' in filename:
            raise ValueError(filename)
        binary = remote('cat -- ' + shlex.quote('/var/www/swordshine/gamefiles/' + filename))
        if binary[:3] not in (b'FWS', b'CWS', b'ZWS'):
            raise ValueError('Not a SWF: ' + filename)
        target = OUT / Path(filename).name
        target.write_bytes(binary)
        records.append(dict(id=int(item_id), name=name, filename=filename, linkage=linkage,
                            path=str(target), sha256=hashlib.sha256(binary).hexdigest(),
                            existing_unity_bundle=None))
        print(item_id, name, len(binary), flush=True)
    (OUT / 'source_manifest.json').write_text(json.dumps(records, indent=2))


if __name__ == '__main__':
    main()
