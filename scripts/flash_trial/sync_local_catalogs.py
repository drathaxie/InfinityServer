"""Append the two verified deployed trial records without rewriting other local rows."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


def sha(contents):
    return hashlib.sha256(contents).hexdigest()


def replace(path, contents):
    fd, temporary = tempfile.mkstemp(prefix='.flash-trial-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    conversion = args.trial / 'conversion'
    p = json.loads((conversion / 'deployment_payload.json').read_text())
    verification = json.loads((conversion / 'live_verification.json').read_text(encoding='utf-8-sig'))
    assert verification['status'] == 'passed'
    assert verification['public_download']['sha256'] == p['bundle_sha256']
    assert verification['inventory_wire_item']['ID'] == p['item_id']
    backup = conversion / datetime.now(timezone.utc).strftime('local_catalog_before_sync-%Y%m%dT%H%M%SZ')
    pending = []
    for name, key, value in (('items.json', str(p['item_id']), p['item']),
                              ('asset_bundles.json', str(p['bundle_id']), p['bundle'])):
        path = args.repo.resolve() / 'data' / name
        raw = path.read_bytes()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        if key in parsed:
            assert parsed[key] == value, 'local trial ID collision: ' + key
            continue
        closing = raw.rfind(b'}')
        assert closing >= 0 and not raw[closing+1:].strip()
        member = json.dumps(key).encode() + b': ' + json.dumps(value, ensure_ascii=True).encode()
        updated = raw[:closing] + (b',\n' if parsed else b'\n') + member + b'\n' + raw[closing:]
        check = json.loads(updated)
        assert check.pop(key) == value and check == parsed
        assert updated.startswith(raw[:closing]), 'existing bytes must remain intact'
        pending.append((path, raw, updated))
    if pending:
        backup.mkdir(parents=True)
        for path, raw, _ in pending:
            (backup / path.name).write_bytes(raw)
        changed = []
        try:
            for path, raw, updated in pending:
                assert path.read_bytes() == raw, 'local catalog changed while preparing: ' + path.name
                replace(path, updated)
                changed.append((path, raw, updated))
                assert path.read_bytes() == updated
        except Exception:
            for path, raw, updated in changed:
                if path.read_bytes() == updated:
                    replace(path, raw)
            raise
    result = {'status': 'passed', 'backup': str(backup) if pending else None,
              'existing_records_and_bytes_preserved': True,
              'files': [{'path': str(path), 'before_sha256': sha(raw), 'after_sha256': sha(updated)}
                        for path, raw, updated in pending]}
    (conversion / 'local_catalog_sync.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
