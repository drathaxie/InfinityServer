"""Prepare a hash-bound, additive live release; remote backups remain on host."""
import hashlib
import json
from pathlib import Path
from trial_host import ROOT
from battlepet_class_lab import SKILLS,graph

LAB=ROOT/'outputs/battlepet_forge_lab/live'


def main():
    pre=json.loads((LAB/'live_preflight.json').read_text())
    native=json.loads((LAB/'bundle/native_pet_contract.json').read_text())
    gpu=json.loads((LAB/'bundle/gpu_validation.json').read_text());assert gpu['status']=='passed'
    source=json.loads((LAB/'pet/source_validation.json').read_text());assert source['status']=='passed'
    base=json.loads(pre['base_class']['rig']);rig=dict(base,ID=pre['class_item_id'])
    bid=pre['bundle_id'];filename='npcs/daimyo_battlepet.unity3d'
    bundle=dict(ID=bid,Name='Armored Daimyo Battlepet',Type='NPC',FileName=filename,Version=1,VersionContent=1,VersionStage=1,VersionLive=1,DependencyID=0,CRCPC=str(native['crc']),_cdnvL=1)
    inline={k:v for k,v in bundle.items() if k not in ('FileName','_cdnvL')};inline['Filename']=filename
    common=dict(Level=1,Quantity=1,StackSize=1,Rarity=10,Coins=False,Cost=0,UpgradeOnly=False,House=False,HouseInventory=False,MobileCompatibility=1,ReqQuests=[],boostValues={})
    classitem=dict(common,ID=pre['class_item_id'],Name='Daimyo Commander',Description='Command your Armored Daimyo battlepet with focused strikes, beams, and Companion Mend.',ItemType=21,EquipSpot=6,Linkage='',Icon='iiclass',isClass=True,Filename=base['Bundle']['Filename'],PrefabName='ArmorSlots',Bundle=base['Bundle'],ClassParticleBundle=base['ClassParticleBundle'])
    pet=dict(common,ID=pre['pet_item_id'],Name='Armored Daimyo Battlepet',Description='An armored companion trained to fight beside a Daimyo Commander.',ItemType=18,EquipSpot=5,Linkage='ArmoredDaimyoBattle',Icon='iipet',isClass=False,Filename='items/pets/ArmoredDaimyoBattle.swf',PrefabName='ArmoredDaimyo_Battle',Bundle=inline)
    skills=[]
    for slot,(name,state,duration,cd,mult,heal,action) in enumerate(SKILLS):
        data,layout=graph(state,duration,cd,mult,heal=heal)
        skills.append(dict(slot=slot,name=name,action=action,description=('Your companion restores your health.' if heal else 'Command your companion to strike your selected enemy.'),icon='iipet',data=data,forge=layout,mana=-[0,5,15,20,30][slot]))
    cap={str(pre['pet_item_id']):dict(bundle_id=bid,prefab=pet['PrefabName'],source_sha256=json.loads((LAB/'pet/pet.json').read_text())['sourceSha256'],states={'Attack1':29/24,'Attack2':18/24,'Buff':23/24},max_range=8)}
    edits=[]
    def edit(file,old,new):
        assert new in (ROOT/file).read_text(encoding='utf-8'),(file,new)
        edits.append(dict(file=file,old=old,new=new))
    edit('server/combat.py','    name = props.get("Name")\n    killed = []\n','    name = props.get("Name")\n    killed = []\n    if name == "PetAnimation":\n        from battlepets import render_animation\n        return render_animation(props, default_targets[0] if default_targets else None), 0, killed\n')
    for targets,damage,indent in [('ptgts','0','            '),('mtgts','dmg','        ')]:
        old=indent+'return ({"Name": "Damage", "DamageTypes": dtypes, "Damages": damages,\n'+indent+'         "Targets": '+targets+', "TargetHPs": hps}, '+damage+', killed)'
        new=indent+'out = {"Name": "Damage", "DamageTypes": dtypes, "Damages": damages,\n'+indent+'       "Targets": '+targets+', "TargetHPs": hps}\n'+indent+'if "Immediate" in props:\n'+indent+'    out["Immediate"] = bool(props["Immediate"])\n'+indent+'return out, '+damage+', killed'
        edit('server/combat.py',old,new)
    marker='# --- damage ------------------------------------------------------------------'
    edit('server/combat_engine/nodes.py',marker,'@renderer("PetAnimation")\ndef _pet_animation(ctx, props):\n    from battlepets import render_animation\n    return render_animation(props, ctx.target)\n\n\n'+marker)
    edit('server/world.py','import json\n','import json\nimport battlepets\n')
    edit('server/world.py','    __slots__ = ("uid", "name", "user_obj", "area", "frame", "x", "y", "writer")','    __slots__ = ("uid", "name", "user_obj", "area", "frame", "x", "y", "writer", "battlepet_v1")')
    edit('server/world.py','        self.writer = writer\n','        self.writer = writer\n        self.battlepet_v1 = False\n')
    # Restrict the capability fan-out change to broadcast's first encoding site.
    file=ROOT/'server/world.py';text=file.read_text();start=text.index('def broadcast(');end=text.index('\n\ndef send(',start)
    new=text[start:end];old=new.replace('    legacy = battlepets.legacy_packet(obj)\n    old_data = data if legacy is obj else json.dumps(assetwire.normalize(legacy), separators=(",", ":")).encode("utf-8") + b"\\x00"\n','').replace('data if getattr(m,\'battlepet_v1\',False) else old_data','data')
    edit('server/world.py',old,new)
    edit('server/handlers/combat_cmds.py','import spellstones\n','import spellstones\nimport battlepets\n')
    text=(ROOT/'server/handlers/combat_cmds.py').read_text();a=text.index('    resource_before = combat.resource_total(session.member.uid)\n',text.index('async def continue_cast'));b=text.index('    pkts, killed, dmg = combat.resume_cast',a)
    prefix='    node_name = params[2] if len(params) > 2 else "?"\n'
    edit('server/handlers/combat_cmds.py',prefix+'    resource_before = combat.resource_total(session.member.uid)\n',prefix+text[a:b])
    edit('server/handlers/combat_cmds.py','    forge_data = sk["forge"] if sk else None\n','    forge_data = sk["forge"] if sk else None\n    pet_error = battlepets.check_skill(session, sk, target)\n    if pet_error:\n        await send_obj(writer, combat.cast_failure(uid, slot_i, pet_error))\n        return\n')
    text=(ROOT/'server/handlers/dev.py').read_text();a=text.index('    sub = (params[0].lstrip');b=text.index('\n\n',a)
    old=text[a:text.index('\n    if sub == "bphello":',a)];edit('server/handlers/dev.py',old,text[a:b])
    edit('server/handlers/editors.py','import world\n','import world\nimport battlepets\n')
    text=(ROOT/'server/handlers/editors.py').read_text();a=text.index('    init = forge.build_init(session.conn)');b=text.index('    await send_obj(writer, init)',a)
    edit('server/handlers/editors.py','    init = forge.build_init(session.conn)\n',text[a:b])
    edit('server/server.py','import db\n','import db\nimport battlepets\n')
    text=(ROOT/'server/server.py').read_text();a=text.index('                pet_error = battlepets.check_skill(sess,');b=text.index('                if combat.ult_armed',a)
    old='                if combat.ult_armed';edit('server/server.py',old,text[a:b]+old)
    binary=LAB/'bundle/daimyo_battlepet.unity3d';loader=LAB/'loader/InfinityLoader.dll'
    payload=dict(preflight=pre,bundle=bundle,class_item=classitem,pet_item=pet,class_rig=rig,skills=skills,capabilities=cap,edits=edits,
                 bundle_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),loader_sha256=hashlib.sha256(loader.read_bytes()).hexdigest(),
                 module_sha256=hashlib.sha256((ROOT/'server/battlepets.py').read_bytes()).hexdigest(),source_mean=source['worst_mean'],gpu_mean=gpu['worst_mean'])
    (LAB/'deployment_payload.json').write_text(json.dumps(payload,indent=2))
    print(json.dumps(dict(status='prepared',class_id=pre['class_id'],class_item=classitem['ID'],pet_item=pet['ID'],edits=len(edits))))

if __name__=='__main__':main()
