"""Read back both new trial records, public binaries, and next-login inventory wire."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import db
import game


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--payload',type=Path,required=True)
    p=json.loads(ap.parse_args().payload.read_text())
    ids=','.join(str(e['bundle']['ID']) for e in p['entries'])
    with urllib.request.urlopen('https://divinityarts.mooo.com/data/GetAssetBundlesByIDs?ids='+ids,timeout=30) as response:
        bundles={b['ID']:b for b in json.loads(response.read())}
    c=db.connect()
    try:
        inventory={i['ID']:i for i in game.inventory(c,8)}
        checks=[]
        for e in p['entries']:
            url='https://content.130-162-189-229.sslip.io/'+Path(e['bundle_target']).relative_to('/opt/infinity/content').as_posix()
            with urllib.request.urlopen(url,timeout=30) as response:
                binary=response.read();assert response.status==200
            assert hashlib.sha256(binary).hexdigest()==e['bundle_sha256']
            bundle=bundles[e['bundle']['ID']]
            assert (bundle.get('Filename') or bundle.get('FileName'))==e['bundle']['FileName']
            assert bundle['CRCPC']==e['bundle']['CRCPC'] and bundle['VersionLive']==1
            item=inventory[e['item']['ID']]
            assert item['PrefabName']==e['item']['PrefabName'] and item['Bundle']['ID']==e['bundle']['ID']
            assert (item['ItemType'],item['EquipSpot'],item['MobileCompatibility'])==(
                e['item']['ItemType'],e['item']['EquipSpot'],1)
            owned=[dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=? AND item_id=?',(8,item['ID'])).fetchall()]
            assert len(owned)==1 and owned[0]['quantity']==1 and not owned[0]['banked']
            assert json.loads(json.dumps(dict(c.execute('SELECT * FROM items WHERE id=?',(e['source_id'],)).fetchone()),default=str))==e['original']
            checks.append(dict(id=item['ID'],name=item['Name'],bytes=len(binary),sha256=e['bundle_sha256'],
                prefab=item['PrefabName'],bundle_id=bundle['ID'],char_item_id=owned[0]['char_item_id'],
                public_delivery=True,inventory_wire=True,original_preserved=True))
    finally:c.close()
    services=subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines()
    assert services==['active','active']
    print(json.dumps(dict(status='passed',items=checks,services=services,
        proof_boundary=f"Built/rendered, public delivery and inventory verified. These {len(checks)} new items still need player equip and visual observation.")))


if __name__=='__main__':main()
