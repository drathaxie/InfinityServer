"""Read back the deployed trial and its public delivery paths without changing state."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import urllib.request
import db
import game

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--payload',type=Path,required=True)
parser.add_argument('--deployment-result',type=Path,required=True)
args=parser.parse_args()
p=json.loads(args.payload.read_text())
deployed=json.loads(args.deployment_result.read_text())
assert deployed['status']=='applied' and deployed['item_id']==p['item_id']
assert (p['char_id'],p['account_id'],p['char_name'])==(8,8,'redux')
EXPECTED_SHA=p['bundle_sha256']
CONTENT_URL='https://content.130-162-189-229.sslip.io/'+Path(p['bundle_target']).relative_to('/opt/infinity/content').as_posix()
API_URL='https://divinityarts.mooo.com/data/GetAssetBundlesByIDs?ids='+str(p['bundle_id'])
with urllib.request.urlopen(CONTENT_URL,timeout=30) as response:
    binary=response.read()
    download={'url':CONTENT_URL,'status':response.status,'bytes':len(binary),
              'sha256':hashlib.sha256(binary).hexdigest()}
assert download['sha256']==EXPECTED_SHA and binary.startswith(b'UnityFS')
with urllib.request.urlopen(API_URL,timeout=30) as response:
    bundles=json.loads(response.read())
bundle=next(value for value in bundles if value['ID']==p['bundle_id'])
assert (bundle.get('Filename') or bundle.get('FileName'))==p['bundle']['FileName']
assert all(bundle[key]==1 for key in ('VersionContent','VersionStage','VersionLive'))
assert bundle['CRCPC']==p['bundle']['CRCPC']
c=db.connect()
owned=[dict(row) for row in c.execute('SELECT * FROM char_items WHERE char_id=? AND item_id=?',(8,p['item_id'])).fetchall()]
assert len(owned)==1 and owned[0]['quantity']==1 and not owned[0]['banked'] and not owned[0]['equipped']
wire=[item for item in game.inventory(c,8) if item['ID']==p['item_id']]
assert len(wire)==1 and wire[0]['CharItemID']==owned[0]['char_item_id']
assert wire[0]['Bundle']['ID']==p['bundle_id'] and wire[0]['PrefabName']==p['item']['PrefabName']
assert wire[0]['MobileCompatibility']==1
audit=[dict(row) for row in c.execute(
    "SELECT action,item_id,quantity,actor_name,detail FROM account_audit WHERE char_id=? AND item_id=? ORDER BY id",(8,p['item_id'])).fetchall()]
assert len(audit)==1 and audit[0]['quantity']==1 and audit[0]['detail']==p['reason']
shared=dict(c.execute('SELECT * FROM inf_asset_bundles WHERE bundle_id=?',(p['bundle_id'],)).fetchone())
assert shared['filename']==p['bundle']['FileName'] and shared['version_live']==1
before=json.loads((Path(deployed['backup'])/'database-before.json').read_text())
current=[dict(row) for row in c.execute('SELECT * FROM char_items WHERE char_id=? ORDER BY char_item_id',(8,)).fetchall()]
assert [row for row in current if row['item_id']!=p['item_id']]==before['redux_inventory']
assert dict(c.execute('SELECT * FROM items WHERE id=1').fetchone())==before['default_sword']
registry=json.loads(Path('/opt/infinity/data/asset_bundles.json').read_text())
assert registry[str(p['native_bundle_id'])]==p['native_bundle']
equipped=[dict(row) for row in c.execute(
    'SELECT ci.item_id,i.Name FROM char_items ci JOIN items i ON i.id=ci.item_id '
    'WHERE ci.char_id=? AND ci.equipped=1 AND i.equip_spot=2',(8,)).fetchall()]
game_pid=subprocess.check_output(['systemctl','show','infinity-game','-p','MainPID','--value'],text=True).strip()
assert game_pid==deployed['game_pid_before']
services=subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines()
assert services==['active','active']
print(json.dumps({'status':'passed','public_download':download,'public_api_bundle':bundle,
                  'inventory_row':owned[0],'inventory_wire_item':wire[0],'audit':audit,
                  'shared_bundle_catalog':shared,'equipped_weapon_preserved':equipped,
                  'services':services,'game_pid':subprocess.check_output(
                     ['systemctl','show','infinity-game','-p','MainPID','--value'],text=True).strip(),
                  'proof_boundary':'Public content/API and authoritative next-login inventory wire verified; installed-client equip/render remains to be observed.'},indent=2,default=str))
c.close()
