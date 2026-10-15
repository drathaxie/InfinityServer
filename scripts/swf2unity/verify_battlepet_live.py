"""Verify public delivery and Redux's generated wires; export summaries only."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

import db
import forge
import game
import seed
import shared_online


def read(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        assert response.status == 200
        return response.read()


def socket_probe(c, iid, pid):
    # Reuse a current token only on the host. Never rotate, export, or log it,
    # and do not interrupt an account that is already connected.
    account = c.execute('SELECT username,session_token FROM accounts WHERE id=8').fetchone()
    if not account or not game.resolve_session(c,account['username'],account['session_token']):
        return {'status':'not_run','reason':'No current Redux login token; no credential changes made.'}
    lease = shared_online.acquire(8)
    if lease is None:
        return {'status':'not_run','reason':'Redux is already online; existing connection preserved.'}
    lease.close()
    with socket.create_connection(('127.0.0.1',5588),timeout=10) as peer:
        peer.settimeout(10); buffer=bytearray(); total=0
        def send(cmd,params):
            peer.sendall(json.dumps({'Cmd':cmd,'Params':params},separators=(',',':')).encode()+b'\x00')
        def until(cmd):
            nonlocal total
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if b'\x00' not in buffer:
                    chunk=peer.recv(65536)
                    assert chunk,'Socket closed before expected response'
                    total+=len(chunk);assert total<32*1024*1024,'Unexpected response volume'
                    buffer.extend(chunk);continue
                pos=buffer.index(0);raw=bytes(buffer[:pos]);del buffer[:pos+1]
                packet=json.loads(raw)
                if packet.get('Cmd')==cmd:return packet
                if packet.get('Cmd')=='loginResponse':assert packet.get('Success',True),'Account became busy before verification'
            raise TimeoutError('Expected response did not arrive')
        send('Login',['',account['username'],account['session_token']])
        init=until('initPlayer');bar=until('sEAct')
        assert init['user']['eqp']['Pet']['ID']==pid
        assert any(i['ID']==iid and i['Equipped'] for i in init['classItems'])
        assert init['playerInfo']['BagSlots']>=len(init['items'])+len(init['classItems'])
        assert all(str(i) in bar['skillList'] for i in range(5))
        send('sfInit',[]);legacy=until('sfInit')
        assert 'PetAnimation' not in legacy['nodes'] and 'Daimyo Commander' not in legacy['classes']
        send('cmd',['bphello','1']);send('sfInit',[]);modern=until('sfInit')
        assert 'PetAnimation' in modern['nodes'] and 'Daimyo Commander' in modern['classes']
    return {'status':'passed','login':True,'equipped_wire':True,'five_skill_bar':True,'legacy_forge_filtered':True,'capability_forge_enabled':True}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--folder', type=Path, required=True)
    args = ap.parse_args(); p = json.loads((args.folder/'deployment_payload.json').read_text())
    cid = p['preflight']['class_id']; iid = p['class_item']['ID']; pid = p['pet_item']['ID']; bid = p['bundle']['ID']
    public = 'https://divinityarts.mooo.com/'
    bundle = read('https://content.130-162-189-229.sslip.io/game/assetbundles/windows/npcs/daimyo_battlepet/1/daimyo_battlepet.unity3d')
    assert hashlib.sha256(bundle).hexdigest() == p['bundle_sha256']
    catalog = json.loads(read(public+'data/GetAssetBundlesByIDs?ids='+str(bid)))
    entry = next(b for b in catalog if b['ID'] == bid)
    assert (entry.get('Filename') or entry.get('FileName')) == p['bundle']['FileName']
    assert str(entry['CRCPC']) == str(p['bundle']['CRCPC']) and entry['Type'] == 'NPC'
    loader = read(public+'mod/InfinityLoader.dll')
    loader_hash = read(public+'mod/InfinityLoader.dll.sha256').decode().strip().lower()
    assert hashlib.sha256(loader).hexdigest() == loader_hash == p['loader_sha256']
    c = db.connect()
    try:
        char = dict(c.execute('SELECT id,account_id,name,class_id FROM characters WHERE id=8').fetchone())
        assert char['account_id'] == 8 and char['name'].lower() == 'redux' and char['class_id'] == cid
        rows = [dict(r) for r in c.execute('SELECT item_id,quantity,equipped,banked FROM char_items WHERE char_id=8 AND item_id IN (?,?)',(iid,pid)).fetchall()]
        assert len(rows) == 2 and all(r['equipped'] and not r['banked'] and r['quantity']==(seed.CLASS_CP_MAX if r['item_id']==iid else 1) for r in rows), rows
        pet = next(i for i in game.inventory(c,8) if i['ID']==pid)
        armor = next(i for i in game.class_items(c,8) if i['ID']==iid)
        assert pet['Bundle']['ID']==bid and pet['PrefabName']=='ArmoredDaimyo_Battle'
        assert (armor['EquipSpot'],armor['PrefabName'])==(6,'ArmorSlots')
        assert forge.class_for_armor_item(c,iid)==cid
        bar = forge.build_seact(c,cid)['skillList']
        assert set(bar)==set(map(str,range(5)))
        assert [bar[str(i)]['nam'] for i in range(5)]==[s['name'] for s in p['skills']]
        rig = game._equipped_rig(c,8)
        assert rig['Pet']['ID']==pid
        before_path = sorted(Path('/opt/infinity/backups').glob('battlepet-live-*/database-before.json'))[-1]
        before = json.loads(before_path.read_text())
        for table,key,value,expected in [('items','id',6673,before['original_pet']),('classes','class_id',1932,before['base_class'])]:
            actual = dict(c.execute(f'SELECT * FROM {table} WHERE {key}=?',(value,)).fetchone())
            assert json.loads(json.dumps(actual,default=str))==expected
        count = c.execute('SELECT COUNT(*) AS n FROM char_items WHERE char_id=8').fetchone()['n']
        assert count >= len(before['inventory'])+2
        subsequent_inventory_rows=count-len(before['inventory'])-2
        expected=[]
        for old in before['inventory']:
            row=dict(old)
            if row['equipped'] and not row['banked'] and (db.item(c,row['item_id']) or {}).get('EquipSpot') in (5,6):
                row['equipped']=0
            expected.append(row)
        before_ids={r['char_item_id'] for r in before['inventory']}
        existing=[dict(r) for r in c.execute('SELECT * FROM char_items WHERE char_id=8 ORDER BY char_item_id').fetchall() if r['char_item_id'] in before_ids]
        assert json.loads(json.dumps(existing,default=str))==expected,'Prior inventory fields changed after activation'
        full_char=c.execute('SELECT * FROM characters WHERE id=8').fetchone()
        info=game.build_init_player(c,full_char)['playerInfo']
        assert info['BagSlots']==game.storage_capacity(c,8) and info['BankSlots']==game.storage_capacity(c,8,banked=True)
        native_grant_path=Path('/tmp/infinity-redux-converted-grant/payload.json')
        native_roster_owned=None
        if native_grant_path.is_file():
            roster=json.loads(native_grant_path.read_text())['roster']
            visible={i['ID'] for i in game.inventory(c,8)+game.class_items(c,8)+game.bank(c,8)}
            assert visible>={r['item_id'] for r in roster}
            native_roster_owned=len(roster)
        authenticated_socket=socket_probe(c,iid,pid)
    finally:
        c.close()
    with socket.create_connection(('127.0.0.1',5588),timeout=5):
        pass
    services = subprocess.check_output(['systemctl','is-active','infinity-api','infinity-game'],text=True).splitlines()
    assert services==['active','active']
    print(json.dumps(dict(status='passed',class_id=cid,class_item_id=iid,pet_item_id=pid,bundle_id=bid,
        skills=[bar[str(i)]['nam'] for i in range(5)],equipped=True,inventory_rows_added=2,subsequent_inventory_rows=subsequent_inventory_rows,native_roster_owned=native_roster_owned,capacity={'BagSlots':info['BagSlots'],'BankSlots':info['BankSlots']},
        original_records_preserved=True,public_bundle_bytes=len(bundle),bundle_sha256=p['bundle_sha256'],
        loader_sha256=loader_hash,services=services,game_socket='listening',authenticated_socket=authenticated_socket,
        proof_boundary='Public delivery and generated inventory/class wires verified; rendered gameplay requires the updated client.')))


if __name__=='__main__': main()
