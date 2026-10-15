"""Combine an armor bone template with a native skin-customizable face kit."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('skeleton',type=Path);ap.add_argument('face',type=Path);ap.add_argument('output',type=Path)
    args=ap.parse_args()
    assert args.output.resolve() not in (args.skeleton.resolve(),args.face.resolve())
    skeleton=json.loads((args.skeleton/'template.json').read_text())
    face=json.loads((args.face/'template.json').read_text())
    result=deepcopy(skeleton);files={}
    for g in ('m','f'):
        assert face[g]['face']['head'].get('mask_png'), 'head skin mask required for both genders'
        result[g]['face']=face[g]['face']
        for entry in face[g]['face'].values():
            for key in ('png','mask_png'):
                if key in entry:
                    source=args.face/entry[key]
                    assert source.resolve().is_relative_to(args.face.resolve())
                    files[entry[key]]=source
    args.output.mkdir(parents=True,exist_ok=True)
    for name,source in files.items():shutil.copy2(source,args.output/name)
    (args.output/'template.json').write_text(json.dumps(result,indent=2))
    (args.output/'face_provenance.json').write_text(json.dumps(dict(skeleton=str(args.skeleton),face=str(args.face),
        files={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files.items()}),indent=2))
    print('Prepared native skin-customizable face template:',args.output)


if __name__=='__main__':main()
