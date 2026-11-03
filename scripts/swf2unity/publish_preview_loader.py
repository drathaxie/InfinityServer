"""Publish a hash-guarded armor-preview loader build, retaining rollback files."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish(path, binary, metadata):
    assert path.parent.resolve() == Path('/opt/infinity/data/mod')
    fd, name = tempfile.mkstemp(prefix='.preview-loader-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(binary)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, metadata.st_mode & 0o777)
        os.chown(name, metadata.st_uid, metadata.st_gid)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    p = json.loads(args.payload.read_text())
    target = Path('/opt/infinity/data/mod/InfinityLoader.dll')
    hashfile = target.with_suffix('.dll.sha256')
    assert sha(target) == p['expected_before_sha256']
    assert hashfile.read_text().strip() == p['expected_before_sha256']
    assert sha(args.binary) == p['new_sha256'] and args.binary.read_bytes().startswith(b'MZ')
    assert p['new_sha256'] != p['expected_before_sha256']
    pid = subprocess.check_output(['systemctl', 'show', 'infinity-game', '-p', 'MainPID', '--value'], text=True).strip()
    result = dict(status='preflight_passed', before_sha256=sha(target), new_sha256=p['new_sha256'])
    if args.apply:
        backup = Path('/opt/infinity/backups') / datetime.now(timezone.utc).strftime('flash-rig-preview-loader-%Y%m%dT%H%M%SZ')
        backup.mkdir(mode=0o700)
        shutil.copy2(target, backup / target.name)
        shutil.copy2(hashfile, backup / hashfile.name)
        shutil.copy2(args.payload, backup / 'payload.json')
        metadata, hashmeta = target.stat(), hashfile.stat()
        try:
            publish(target, args.binary.read_bytes(), metadata)
            publish(hashfile, (p['new_sha256'] + '\n').encode(), hashmeta)
            assert sha(target) == hashfile.read_text().strip() == p['new_sha256']
        except Exception:
            publish(target, (backup / target.name).read_bytes(), metadata)
            publish(hashfile, (backup / hashfile.name).read_bytes(), hashmeta)
            raise
        result.update(status='published', backup=str(backup))
    result['game_pid_unchanged'] = pid == subprocess.check_output(
        ['systemctl', 'show', 'infinity-game', '-p', 'MainPID', '--value'], text=True).strip()
    result['services'] = subprocess.check_output(['systemctl', 'is-active', 'infinity-api', 'infinity-game'], text=True).splitlines()
    assert result['game_pid_unchanged'] and result['services'] == ['active', 'active']
    print(json.dumps(result))


if __name__ == '__main__':
    main()
