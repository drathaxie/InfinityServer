"""A running socket serializer must see newly published native bundle versions."""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import assetwire


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'data').mkdir()
        catalog = root / 'data/asset_bundles.json'
        def write(version):
            catalog.write_text(json.dumps({'7': dict(ID=7, Filename='items/swords/a.unity3d',
                VersionLive=version, _cdnvL=version)}))
            os.utime(catalog, ns=(version * 1000000000, version * 1000000000))
        wire = dict(ID=7, Filename='items/swords/a.unity3d', VersionLive=1, VersionContent=1)
        with patch.object(assetwire, '__file__', str(root / 'server/assetwire.py')):
            write(2)
            assert assetwire.normalize(wire)['VersionLive'] == 2
            write(3)
            updated = assetwire.normalize(wire)
            assert updated['VersionLive'] == updated['VersionContent'] == updated['VersionStage'] == 3
            assert wire['VersionLive'] == 1
    assetwire._read_catalog.cache_clear()
    print('assetwire catalog revision regression passed')


if __name__ == '__main__':
    main()
