"""Capture exact remote source/catalog state before the guarded battlepet release."""
import json
from pathlib import Path
from trial_host import ROOT,server_script

SCRIPT='''import db,json,pathlib,hashlib,subprocess
c=db.connect();root=pathlib.Path('/opt/infinity');out={}
out['character']=dict(c.execute('SELECT id,account_id,name,class_id FROM characters WHERE id=8').fetchone())
assert out['character']['name'].lower()=='redux' and out['character']['account_id']==8
out['base_class']=dict(c.execute('SELECT * FROM classes WHERE class_id=1932').fetchone())
out['inventory_rows']=c.execute('SELECT COUNT(*) AS n FROM char_items WHERE char_id=8').fetchone()['n']
out['sources']={}
for name in ['server/combat.py','server/combat_engine/nodes.py','server/server.py','server/world.py','server/handlers/combat_cmds.py','server/handlers/dev.py','server/handlers/editors.py','server/handlers/context.py']:
 b=(root/name).read_bytes();out['sources'][name]={'sha256':hashlib.sha256(b).hexdigest()}
registry=json.loads((root/'data/asset_bundles.json').read_text());seeds=json.loads((root/'data/items.json').read_text())
i=2000000
while str(i) in seeds or c.execute('SELECT id FROM items WHERE id=?',(i,)).fetchone() or str(i+1) in seeds or c.execute('SELECT id FROM items WHERE id=?',(i+1,)).fetchone():i+=1
b=2000000
while str(b) in registry or c.execute('SELECT bundle_id FROM inf_asset_bundles WHERE bundle_id=?',(b,)).fetchone():b+=1
cl=2000000
while c.execute('SELECT class_id FROM classes WHERE class_id=?',(cl,)).fetchone():cl+=1
out.update(class_item_id=i,pet_item_id=i+1,bundle_id=b,class_id=cl)
for key,name in [('registry','data/asset_bundles.json'),('seeds','data/items.json'),('loader','data/mod/InfinityLoader.dll')]:out[key+'_sha256']=hashlib.sha256((root/name).read_bytes()).hexdigest()
out['services']=subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines()
print(json.dumps(out,default=str));c.close()
'''

def main():
    lab=ROOT/'outputs/battlepet_forge_lab/live';lab.mkdir(exist_ok=True)
    data=json.loads(server_script('-',data=SCRIPT.encode()))
    (lab/'live_preflight.json').write_text(json.dumps(data,indent=2))
    print(json.dumps({k:data[k] for k in ['character','class_item_id','pet_item_id','bundle_id','class_id','services']}))

if __name__=='__main__':main()
