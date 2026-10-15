"""Complete every supported enhancement family/slot. Dry-run unless --apply is supplied.

Existing catalog definitions are preserved. New IDs occupy the explicit 210000-210051
range and collisions abort; compare against production before importing this delta.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import patterns


def build(items):
    result = deepcopy(items)
    slots = {'Weapon': patterns.WEAPON, 'Helm': patterns.HEAD,
             'Cape': patterns.BACK, 'Armor': patterns.ARMOR}
    existing = {}
    for item in result.values():
        if item.get('ItemType') != patterns.ITEMTYPE_GEM:
            continue
        family = patterns.archetype_of(item.get('Name'))
        for label, spot in slots.items():
            if family and label.lower() in item.get('Name', '').lower():
                key = (family, spot)
                if key in existing:
                    raise ValueError('Ambiguous existing family/slot: ' + str(key))
                existing[key] = item['ID']
    entries, added = [], []
    for index, (family, primary) in enumerate(patterns._ARCHETYPE_PRIMARY.items()):
        for offset, (label, spot) in enumerate(slots.items()):
            iid = existing.get((family, spot))
            if iid is None:
                iid = 210000 + index * 4 + offset
                if str(iid) in result:
                    raise ValueError('Reserved gem item ID is occupied: ' + str(iid))
                item = {'ID': iid, 'Name': f'{family.title()} {label} Gem',
                    'Description': f'A {label.lower()} enhancement favoring {primary}. '
                        'Rolls random stats and a rarity from Common to Mythic when acquired.',
                    'ItemType': patterns.ITEMTYPE_GEM, 'EquipSpot': 1, 'Linkage': '',
                    'Icon': 'iidesign', 'Level': 1, 'Quantity': 1, 'StackSize': 1,
                    'Element': 1, 'Faction': 1, 'strReqQuests': '', 'MetaString': '',
                    'DamageRange': 0.1, 'ReqQuests': [], 'boostValues': {}}
                result[str(iid)] = item
                added.append(item)
            entries.append({'family': family, 'primary_stat': primary, 'slot': label,
                            'equip_spot': spot, 'item_id': iid})
    return result, {'entries': entries}, added


def main():
    path = ROOT / 'data/items.json'
    original = json.loads(path.read_text(encoding='utf-8'))
    items, manifest, added = build(original)
    if '--apply' in sys.argv:
        path.write_text(json.dumps(items, separators=(',', ':')), encoding='utf-8')
        (ROOT / 'data/gem_catalog.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'families': len(patterns._ARCHETYPE_PRIMARY), 'combinations': len(manifest['entries']),
                      'existing': len(manifest['entries']) - len(added), 'added': len(added),
                      'applied': '--apply' in sys.argv}))


if __name__ == '__main__':
    main()
