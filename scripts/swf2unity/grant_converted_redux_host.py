"""Stage a public item roster and anchored capacity patch; return metadata only."""
import argparse
import csv
import io
import json
import tarfile
from trial_host import ROOT,remote,server_script

FOLDER='/tmp/infinity-redux-converted-grant'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');args=ap.parse_args()
    base=ROOT/'outputs/flash_converter_trial/native_replacements';lab=ROOT/'outputs/battlepet_forge_lab/live'
    with (base/'published_items.csv').open(encoding='utf-8-sig',newline='') as f:
        roster=[dict(item_id=int(r['item_id']),name=r['name'],bundle_id=int(r['bundle_id']),version=int(r['version']),filename=r['filename'],prefab=r['prefab']) for r in csv.DictReader(f)]
    pre=json.loads(server_script('-',data=b"import pathlib,hashlib,json;print(json.dumps({'game_sha256':hashlib.sha256(pathlib.Path('/opt/infinity/server/game.py').read_bytes()).hexdigest()}))"))
    local=(ROOT/'server/game.py').read_text(encoding='utf-8');start=local.index('def storage_capacity(');end=local.index('# PlayerInfo.dateCreated',start)
    edits=[dict(old='DEFAULT_HOUSE_SLOTS = 50\n',new='DEFAULT_HOUSE_SLOTS = 50\n\n\n'+local[start:end])]
    for old,new in [
        ('>= DEFAULT_BANK_SLOTS:', '>= storage_capacity(conn, cid, banked=True):'),
        ('>= DEFAULT_BAG_SLOTS:', '>= storage_capacity(conn, cid):'),
        ('"BankSlots": DEFAULT_BANK_SLOTS,','"BankSlots": storage_capacity(conn, char[\'id\'], banked=True),'),
        ('"BagSlots": DEFAULT_BAG_SLOTS,','"BagSlots": storage_capacity(conn, char[\'id\']),')]:
        assert new in local;edits.append(dict(old=old,new=new))
    payload=dict(**pre,roster=roster,edits=edits,reason='User requested everything converted on Redux; grant missing audited items unequipped and provide character-only testing capacity.')
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        raw=json.dumps(payload).encode();info=tarfile.TarInfo('payload.json');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
        for name in ('grant_converted_redux.py','deploy_battlepet_live.py'):archive.add(ROOT/'scripts/swf2unity'/name,arcname=name)
    remote('mkdir -p '+FOLDER+' && tar -xf - -C '+FOLDER,stream.getvalue())
    result=json.loads(server_script(FOLDER+'/grant_converted_redux.py','--payload '+FOLDER+'/payload.json'+(' --apply' if args.apply else '')))
    (lab/('converted_grant_result.json' if args.apply else 'converted_grant_dry_run.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))


if __name__=='__main__':main()
