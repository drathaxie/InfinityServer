"""InfinityServer gem balance v2. Authored rules, not claims about AE's hidden formulas.

Power is exactly the six-stat budget. Base damage and helm HP are separate, explicit
slot properties. Existing owned Patterns are never regenerated when read or moved.
"""
import math
import random

VERSION = 2
STATS = ('STR', 'END', 'DEX', 'INT', 'WIS', 'LUK')
SLOT_SHARE = {2: 30, 3: 20, 4: 15, 7: 35}
SLOT_NAME = {2: 'Weapon', 3: 'Helm', 4: 'Cape', 7: 'Armor'}
QUALITY_NAME = {5: 'Common', 6: 'Uncommon', 7: 'Rare', 8: 'Epic', 9: 'Legendary', 10: 'Mythic'}
# Favored stat, minimum focus %, then relative weights for supporting stats.
# DEX/WIS builds retain enough STR/INT to work with the current AP/SP formulas.
PROFILES = {
    'warrior': ('STR', 50, {'DEX': 55, 'END': 30, 'LUK': 15}),
    'fighter': ('STR', 50, {'END': 50, 'DEX': 35, 'LUK': 15}),
    'brute': ('STR', 50, {'END': 50, 'LUK': 30, 'DEX': 20}),
    'wizard': ('INT', 50, {'WIS': 50, 'END': 30, 'LUK': 20}),
    'mage': ('INT', 50, {'WIS': 40, 'END': 30, 'LUK': 30}),
    'sorcerer': ('INT', 50, {'LUK': 50, 'WIS': 30, 'END': 20}),
    'rogue': ('DEX', 44, {'STR': 70, 'LUK': 20, 'END': 10}),
    'thief': ('DEX', 44, {'STR': 65, 'LUK': 25, 'END': 10}),
    'assassin': ('DEX', 44, {'STR': 65, 'LUK': 30, 'END': 5}),
    'healer': ('WIS', 44, {'INT': 65, 'END': 25, 'LUK': 10}),
    'cleric': ('WIS', 44, {'INT': 50, 'END': 40, 'LUK': 10}),
    'lucky': ('LUK', 44, {'STR': 40, 'INT': 40, 'DEX': 10, 'END': 10}),
    'hybrid': ('END', 50, {'STR': 40, 'INT': 40, 'DEX': 10, 'WIS': 10}),
}


def normalize_level(level):
    # Current gameplay cap is game.MAX_LEVEL (6). The model also supports future
    # authored levels up to 100; acquisition uses the actual character/encounter level.
    return max(1, min(100, int(level or 1)))


def budget(level, slot, quality):
    if slot not in SLOT_SHARE or quality not in QUALITY_NAME:
        raise ValueError('Unsupported gem slot or rarity')
    common = math.ceil((40 + 8 * normalize_level(level)) * SLOT_SHARE[slot] / 100)
    return common + (quality - 5) * math.ceil(common / 5)


def primary_bounds(level, slot, quality, family):
    _, focus, _ = PROFILES[family]
    common = budget(level, slot, 5)
    floor = max(1, common * focus // 100)
    step = max(1, math.ceil(floor / 5))
    low = floor + (quality - 5) * step
    # No roll in this tier reaches the next tier's minimum favored stat.
    high = max(low, min(low + step - 1, budget(level, slot, quality) * (focus + 4) // 100))
    return low, high


def roll(level, slot, quality, family, rng=None):
    rng = rng or random
    level = normalize_level(level)
    primary, _, profile = PROFILES[family]
    power = budget(level, slot, quality)
    low, high = primary_bounds(level, slot, quality, family)
    stats = dict.fromkeys(STATS, 0)
    stats[primary] = rng.randint(low, high)
    remaining = power - stats[primary]
    weights = {k: w * rng.randint(90, 110) for k, w in profile.items()}
    total = sum(weights.values())
    ideals = {k: remaining * w / total for k, w in weights.items()}
    cap = stats[primary] - 1
    for k in weights:
        stats[k] = min(cap, math.floor(ideals[k]))
    # Exact integer budget with bounded supporting stats and deterministic rounding.
    while sum(stats.values()) < power:
        choices = [k for k in weights if stats[k] < cap]
        if not choices:
            raise ValueError('Gem profile cannot spend its budget')
        chosen = max(choices, key=lambda k: (ideals[k] - stats[k], weights[k], k))
        stats[chosen] += 1
    assert sum(stats.values()) == power
    base = math.ceil((16 + 3 * level) * (100 + 8 * (quality - 5)) / 100) if slot == 2 else 0
    hp = math.ceil((7 + 2 * level) * (100 + 8 * (quality - 5)) / 100) if slot == 3 else 0
    return {'ID': 1, 'BalanceVersion': VERSION, 'Family': family,
            'Quality': quality, 'Level': level, 'EquipSpot': slot, 'Power': power,
            'Name': f'{QUALITY_NAME[quality]} {family.title()} {SLOT_NAME[slot]}',
            'Base': base or hp, 'HP': hp, 'Wild': 0.1 if slot == 2 else 0.0,
            'Dust': {5: 5, 6: 10, 7: 20, 8: 40, 9: 80, 10: 160}[quality], **stats}


def default(level, slot):
    """Free Power Up: deterministic Common budget, no dust yield or random reroll."""
    level = normalize_level(level)
    power = budget(level, slot, 5)
    weights = dict(zip(STATS, (3, 1, 2, 3, 2, 1)))
    stats = {k: power * w // 12 for k, w in weights.items()}
    for k in sorted(STATS, key=lambda k: (-(power * weights[k] % 12), STATS.index(k)))[:power-sum(stats.values())]:
        stats[k] += 1
    base = 16 + 3 * level if slot == 2 else 0
    hp = 7 + 2 * level if slot == 3 else 0
    return {'ID': 1, 'BalanceVersion': VERSION, 'Family': 'balanced', 'Quality': 5,
            'Level': level, 'EquipSpot': slot, 'Power': power, 'Name': 'Common ' + SLOT_NAME[slot],
            'Base': base or hp, 'HP': hp, 'Wild': 0.1 if slot == 2 else 0.0, 'Dust': 0, **stats}
