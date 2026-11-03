"""Independently inspect the serialized Cryo armor bundle before publication."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import UnityPy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial', type=Path, required=True)
    args = parser.parse_args()
    trial = args.trial.resolve()
    directory = trial / 'conversion/bundle'
    binaries = list(directory.glob('*.unity3d'))
    assert len(binaries) == 1
    binary = binaries[0]
    data = json.loads((trial / 'conversion/armor/armor.json').read_text())
    assert set(data['genders']) == {'m', 'f'}
    env = UnityPy.load(str(binary))
    objects = {obj.path_id: obj for obj in env.objects}
    types = Counter(obj.type.name for obj in env.objects)
    native_classes = {'ItemSlots', 'CustomizableSlot', 'NoFlashWhenHit'}
    scripts, behaviors, textures = [], Counter(), []
    slot_counts = []
    for obj in env.objects:
        if obj.type.name == 'MonoScript':
            script = obj.read()
            assert script.m_ClassName in native_classes
            assert script.m_Namespace == '' and script.m_AssemblyName.removesuffix('.dll') == 'Assembly-CSharp'
            scripts.append({'class': script.m_ClassName, 'assembly': script.m_AssemblyName})
        elif obj.type.name == 'MonoBehaviour':
            cls = obj.read().m_Script.read().m_ClassName
            assert cls in native_classes
            behaviors[cls] += 1
            if cls == 'ItemSlots':
                slots = obj.read_typetree()['slots']
                slot_counts.append(len(slots))
                for entry in slots:
                    assert objects[entry['sprite']['m_PathID']].type.name == 'Sprite'
                    mask = entry['CCMask']['m_PathID']
                    if mask:
                        assert objects[mask].type.name == 'Texture2D'
        elif obj.type.name == 'Texture2D':
            texture = obj.read()
            textures.append({'name': texture.m_Name, 'width': texture.m_Width,
                             'height': texture.m_Height, 'format': int(texture.m_TextureFormat),
                             'serialized_texel_bytes': int(texture.m_CompleteImageSize)})
    assert behaviors['ItemSlots'] == 2
    expected_layered = sum(bool(r.get('layers')) for gender in data['genders'].values()
                           for r in gender['renderers'].values())
    assert behaviors['NoFlashWhenHit'] == expected_layered
    assert sorted(slot_counts) == sorted(len(g['itemslots']) for g in data['genders'].values())
    containers = list(env.container)
    assert len(containers) == 2
    assert all(any(name.endswith('armorbones_' + gender + '.prefab') for name in containers)
               for gender in ('m', 'f'))
    manifest = binary.with_suffix(binary.suffix + '.manifest').read_text()
    crc = re.search(r'^CRC: (\d+)$', manifest, re.MULTILINE)
    assert crc is not None
    result = {'status': 'passed', 'file': str(binary), 'bytes': binary.stat().st_size,
              'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'windows_crc': int(crc[1]),
              'container_names': containers, 'object_types': dict(types), 'native_scripts': scripts,
              'native_component_counts': dict(behaviors), 'item_slot_counts': slot_counts,
              'serialized_texture_bytes': sum(t['serialized_texel_bytes'] for t in textures), 'textures': textures,
              'proof_boundary': 'Serialized asset/component contract; gameplay and player recoloring still require in-client observation.'}
    (directory / 'bundle_audit.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({key: result[key] for key in ('status', 'bytes', 'sha256', 'windows_crc',
        'object_types', 'native_component_counts', 'item_slot_counts', 'serialized_texture_bytes')}, indent=2))


if __name__ == '__main__':
    main()
