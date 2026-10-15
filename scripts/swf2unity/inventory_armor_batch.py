"""Convert owned native armor/class bundles using both source genders and native slots."""
import argparse
import json
from pathlib import Path
import subprocess
import tarfile
import io
import native_batches as n
import build_armor
from trial_host import ROOT,server_script


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--batch',required=True);ap.add_argument('--size',type=int,default=6);args=ap.parse_args()
    assert n.re.fullmatch(r'native\d{2}',args.batch) and 1<=args.size<=16
    batch=n.BASE/args.batch;batch.mkdir(exist_ok=True)
    if (batch/'build_summary.json').exists():n.pipeline(batch,args.size,universal=True);return
    rows=json.loads((ROOT/'outputs/flash_converter_trial/redux_inventory/armor_candidates.json').read_text())
    used={e['bundle_id'] for p in n.BASE.glob('native*/live_verification.json') for e in json.loads(p.read_text())['entries']}
    attempted=set()
    for p in n.BASE.glob('native*/inventory_armor_attempts.json'):
        held_path=p.with_name('held_candidates.json')
        if p.parent==batch or not held_path.exists():continue
        for e in json.loads(held_path.read_text()):
            reason=e['reason']
            if 'RGB body reconstruction does not support location' in reason or ('Unresolved source rig' in reason and 'atlas' in reason) or 'Procedural Flash script held:' in reason or "KeyError: 'm'" in reason:continue
            attempted.add(e['bundle_id'])
    rows=[r for r in rows if r['template']['Bundle']['ID'] not in used|attempted]
    selected=rows[:args.size];assert selected,'No remaining native armor candidates'
    relative=sorted({s['relative'] for r in selected for s in r['sources']})
    raw=server_script('-',data=('FILES='+repr(relative)+'\n'+"import sys,tarfile,pathlib\nwith tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as t:\n for f in FILES:t.add(pathlib.Path('/var/www/swordshine/gamefiles')/f,arcname=f)\n").encode())
    source_root=batch/'sources';source_root.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive.getmembers():
            path=source_root/member.name;assert path.resolve().is_relative_to(source_root.resolve()) and member.isfile()
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(archive.extractfile(member).read())
    entries=[];held=[];attempts=[]
    for row in selected:
        bid=row['template']['Bundle']['ID'];folder=batch/('ReduxArmor'+str(bid));folder.mkdir(exist_ok=True);conversion=folder/'conversion';output=folder/'bundle'
        attempts.append(dict(bundle_id=bid,name=row['original']['Name']));n.write(batch/'inventory_armor_attempts.json',attempts)
        try:
            sources=[dict(s,path=str(source_root/s['relative'])) for s in row['sources']]
            for s in sources:assert n.sha(s['path'])==s['sha256']
            genders={Path(s['relative']).parent.name:s['path'] for s in sources}
            print('INVENTORY_ARMOR_CONVERT',bid,row['original']['Name'],flush=True)
            extra=['--source-head'] if 'morph' in row['original']['Name'].lower() else []
            # Only reuse the retained six-color output from the immediately
            # preceding, unpublished infrastructure-repair attempt. Re-run all
            # script, source, palette, GPU and serialized gates afterward.
            cached=n.BASE/'native35'/folder.name/'conversion'
            reuse=cached/'armor.json'
            if batch.name=='native37' and reuse.is_file():
                import shutil
                old=json.loads(reuse.read_text())
                assert old['name']==folder.name and old['source']==dict(faithful_effects=True,six_colors=True)
                assert bool(old.get('morph'))==bool(extra)
                assert (ROOT/'scripts/swf2unity/armor.py').stat().st_mtime<=reuse.stat().st_mtime
                assert all(n.sha(n.BASE/'native35/sources'/s['relative'])==s['sha256'] for s in sources)
                if not conversion.exists():shutil.copytree(cached,conversion)
                assert n.sha(conversion/'armor.json')==n.sha(reuse)
                print('INVENTORY_ARMOR_REUSE_CAPTURED',bid,flush=True)
            else:
                n.run('armor.py',folder,genders['M'],genders['F'],ROOT/'outputs/flash_converter_trial/skin_fix/template',conversion,
                      '--name',folder.name,'--rgb-cc','--six-colors','--faithful-effects','--max-period','10000',*extra)
            data=json.loads((conversion/'armor.json').read_text())
            warnings=[x for values in data['rig_notes'].values() for x in values]
            assert not warnings,'Unresolved source rig: '+repr(warnings)
            periods={l['period'] for g in data['genders'].values() for r in g['renderers'].values() for l in (r.get('layers') or [])}
            frames=sorted({0,1,7,19,59,*[f for p in periods if p>1 for f in (p-1,p,2*p-1,2*p,5*p-1,5*p,5*p+1)]})
            assert max(frames)<65535
            from source_script_audit import audit
            import swfparse
            from armor import PARTS, MORPH_HEAD
            for gender in ('m','f'):
                movie=swfparse.load(genders[gender.upper()])
                parts=PARTS+([MORPH_HEAD] if extra else [])
                roots=[cid for suffix,_,_,_ in parts for cid,name in movie.symbols.items() if '.' not in name and name.endswith(gender.upper()+suffix)]
                audit(genders[gender.upper()],conversion/'work'/(gender+'_cache'),allow_initial_stops=True,roots=roots)
            n.run('validate_armor.py',folder,conversion,genders['M'],genders['F'],'--samples',','.join(map(str,frames)))
            check=json.loads((conversion/'validation.json').read_text())
            assert check and all(v['worst_frame_mean']<=25 and v['clipped']<=.5 for v in check.values())
            for palette in ('dark-skin','light-skin'):
                n.run('validate_armor.py',folder,conversion,genders['M'],genders['F'],'--samples','0,1,7','--palette',palette)
                check=json.loads((conversion/('validation_'+palette+'.json')).read_text())
                assert check and all(v['worst_frame_mean']<=25 and v['clipped']<=.5 for v in check.values()),'Skin response fidelity failed'
            filename=Path(row['template']['Bundle'].get('Filename') or row['template']['Bundle'].get('FileName')).name
            n.run('build_armor.py',folder,conversion,ROOT/'outputs/flash_converter_trial/skin_fix/template','--bundle',filename,'--project',n.PROJECT,'--output-dir',output,'--validate',','.join(map(str,frames)),'--prepare-only')
            entry=dict(source_id=row['original']['id'],native_bundle_id=bid,key=folder.name,kind='armor',name=row['original']['Name'],prefab=row['template']['PrefabName'],
                       original=row['original'],conversion=str(conversion),bundle=str(output/filename),bundle_name=filename,frames=frames,sources=sources,
                       unity_input=str(n.PROJECT/'Assets/Armors'/folder.name/'unity_input.json'))
            entries.append(entry);n.write(batch/'prepared_plan.json',entries)
        except Exception as error:
            held.append(dict(source_id=row['original']['id'],bundle_id=bid,name=row['original']['Name'],reason=str(error)))
            n.write(batch/'held_candidates.json',held);print('INVENTORY_ARMOR_SOURCE_CASE',bid,str(error)[:300],flush=True)
    n.write(batch/'held_candidates.json',held)
    assert entries,'All selected armor sources need compiler work; inspect recorded cases'
    build_armor.sync_project(str(n.PROJECT));job=batch/'unity_batch.json';n.write(job,dict(inputs=[e['unity_input'] for e in entries]))
    subprocess.run([build_armor.UNITY,'-batchmode','-quit','-projectPath',str(n.PROJECT),'-executeMethod','ArmorBuilder.BuildMany',
        '-armorBatchInput',str(job),'-logFile',str(batch/'unity_batch.log')],creationflags=subprocess.CREATE_NO_WINDOW,env=build_armor.unity_environment(),timeout=7200)
    n.finalize(batch,entries,held,args.size);n.pipeline(batch,args.size,universal=True)


if __name__=='__main__':main()
