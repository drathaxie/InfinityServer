"""Build the validated battlepet in an isolated Unity project."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from trial_host import ROOT


def main():
    lab=ROOT/'outputs/battlepet_forge_lab/live';project=lab/'UnityProject';output=lab/'bundle'
    for folder in [project/'Assets/Editor',project/'Assets/Pet',project/'Packages',project/'ProjectSettings',output]:folder.mkdir(parents=True,exist_ok=True)
    (project/'Packages/manifest.json').write_text(json.dumps({'dependencies':{'com.unity.modules.animation':'1.0.0','com.unity.modules.assetbundle':'1.0.0','com.unity.modules.imageconversion':'1.0.0','com.unity.modules.jsonserialize':'1.0.0'}}))
    (project/'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: 6000.3.18f1\n')
    shutil.copy2(ROOT/'scripts/swf2unity/pet_unity/PetBuilder.cs',project/'Assets/Editor/PetBuilder.cs')
    shutil.copy2(ROOT/'mod/InfinityLoader/FlashPetPlayback.cs',project/'Assets/Editor/FlashPetPlayback.cs')
    for name in ['atlas.png','pet.json']:shutil.copy2(lab/'pet'/name,project/'Assets/Pet'/name)
    env=dict(os.environ);env['FLASH_PET_OUTPUT']=str(output)
    cmd=['C:/Program Files/Unity/Hub/Editor/6000.3.18f1/Editor/Unity.exe','-batchmode','-quit','-projectPath',str(project),'-executeMethod','PetBuilder.Build','-logFile',str(lab/'pet_unity.log')]
    run=subprocess.run(cmd,env=env,creationflags=subprocess.CREATE_NO_WINDOW,timeout=1800)
    assert run.returncode==0,'Pet Unity build failed; inspect pet_unity.log'
    report=json.loads((output/'native_pet_contract.json').read_text());assert report['status']=='passed'
    print(json.dumps(dict(status='native_built',bundle=report['bundle'],captures=len(report['captures']),texture_bytes=report['textureBytes'])))


if __name__=='__main__':main()
