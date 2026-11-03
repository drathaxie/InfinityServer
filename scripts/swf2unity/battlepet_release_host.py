"""Stage the reviewed release and execute its guarded dry-run/apply."""
import argparse
import io
import json
import shlex
import tarfile
from trial_host import ROOT,remote,ENV

FOLDER='/tmp/infinity-battlepet-release-v1'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');ap.add_argument('--verify',action='store_true');ap.add_argument('--code-only',action='store_true');args=ap.parse_args()
    assert not(args.apply and args.verify)
    lab=ROOT/'outputs/battlepet_forge_lab/live'
    files={'deployment_payload.json':lab/'deployment_payload.json','deploy_battlepet_live.py':ROOT/'scripts/swf2unity/deploy_battlepet_live.py','battlepets.py':ROOT/'server/battlepets.py','daimyo_battlepet.unity3d':lab/'bundle/daimyo_battlepet.unity3d','InfinityLoader.dll':lab/'loader/InfinityLoader.dll'}
    if args.code_only:
        files={k:v for k,v in files.items() if not k.endswith(('.dll','.unity3d'))}
    if args.verify:
        files={'verify_battlepet_live.py':ROOT/'scripts/swf2unity/verify_battlepet_live.py'}
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as t:
        for name,path in files.items():t.add(path,arcname=name)
    remote('mkdir -p '+FOLDER+' && tar -xf - -C '+FOLDER,stream.getvalue(),timeout=180)
    command=FOLDER+('/verify_battlepet_live.py' if args.verify else '/deploy_battlepet_live.py');arguments='--folder '+FOLDER+(' --apply' if args.apply else '')
    result=json.loads(remote('sudo -n bash -c '+shlex.quote(ENV+'/opt/infinity/.venv/bin/python '+command+' '+arguments),timeout=180))
    (lab/('live_verification.json' if args.verify else 'deployment_result.json' if args.apply else 'deployment_dry_run.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
