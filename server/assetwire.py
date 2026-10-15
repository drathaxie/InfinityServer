"""Use the published live asset version in every client loading phase.

AssetBundleData.version reads VersionContent while Main.Instance is null (character
selection), then the environment-specific field in-world. API catalog metadata is
preserved separately; this module only prepares outgoing bundle dictionaries.
"""
import functools
import json
from pathlib import Path


def _catalog():
    path=Path(__file__).resolve().parent.parent/'data/asset_bundles.json'
    try:
        stamp=path.stat()
        return _read_catalog(path,stamp.st_mtime_ns,stamp.st_size)
    except OSError:
        return {}


@functools.lru_cache(maxsize=1)
def _read_catalog(path,mtime_ns,size):
    # Versioned native replacements update the catalog while the game stays up.
    # Cache by the file revision so socket responses cannot keep advertising an old version.
    try:
        return {int(k):v for k,v in json.loads(path.read_text(encoding='utf-8')).items()}
    except (OSError,ValueError,TypeError):
        return {}


def normalize(value, catalog=None):
    """Return a copy only where an outgoing asset record needs its versions fixed."""
    if catalog is None:catalog=_catalog()
    if isinstance(value,list):
        result=[normalize(v,catalog) for v in value]
        return value if all(a is b for a,b in zip(result,value)) else result
    if not isinstance(value,dict):return value
    result={k:normalize(v,catalog) for k,v in value.items()}
    changed=any(result[k] is not v for k,v in value.items())
    filename=value.get('Filename') or value.get('FileName')
    if isinstance(filename,str) and filename.lower().endswith('.unity3d') and any(
            k in value for k in ('VersionContent','VersionStage','VersionLive')):
        try:known=catalog.get(int(value.get('ID')))
        except (ValueError,TypeError):known=None
        known_filename=(known or {}).get('Filename') or (known or {}).get('FileName')
        if known and filename.replace('\\','/').casefold()==(known_filename or '').replace('\\','/').casefold():
            try:
                version=int(known.get('_cdnvL',known.get('VersionLive')) or 0)
                published=int(known.get('VersionLive') or 0)>0
            except (ValueError,TypeError):version=0;published=False
            if published and version>0:
                for key in ('VersionContent','VersionStage','VersionLive'):
                    if result.get(key)!=version:
                        result[key]=version;changed=True
    return result if changed else value
