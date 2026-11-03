"""Reproducible full-set comparisons using current combat stats and hit coefficients.

Outputs expected unmodified damage per hit, not class DPS or a playtest verdict.
No database is opened. Results contain no player data.
"""
import json
from pathlib import Path
import random
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'server'))
import combat
import game
import gem_balance as balance
import patterns


def expected_hit(sta, weapon, magical=False):
    power=sta['sp'] if magical else sta['ap']
    return (sum(weapon)/2 * (1+power*combat.AP_DMG_COEF) *
            (1+sta['tcr']*(sta['scm']-1)) * sta['tha'] * (1-combat.DODGE_CHANCE))


def full_set(level, quality, family, rng):
    gems=[balance.roll(level,slot,quality,family,rng) for slot in balance.SLOT_SHARE]
    bonus={s:sum(g[s] for g in gems) for s in balance.STATS}
    bonus['LCK']=bonus.pop('LUK')
    bonus['hp']=sum(g['HP'] for g in gems)
    bonus['weapon']=patterns.weapon_range(next(g for g in gems if g['EquipSpot']==2))
    sta,hp=game.build_combat_stats({'level':level},bonus)
    return {'power':sum(g['Power'] for g in gems),'hp':hp,
            'physical':expected_hit(sta,bonus['weapon']),
            'magical':expected_hit(sta,bonus['weapon'],True),
            'crit_chance':sta['tcr'],'crit_multiplier':sta['scm'],
            'stats':{s:bonus['LCK' if s=='LUK' else s] for s in balance.STATS}}


def run(samples=128):
    rng=random.Random(20260911)
    rows=[]
    for level in range(1,game.MAX_LEVEL+1):
        for quality in range(5,11):
            for family in balance.PROFILES:
                draws=[full_set(level,quality,family,rng) for _ in range(samples)]
                rows.append({'level':level,'quality':quality,'family':family,
                             **{k:round(statistics.mean(d[k] for d in draws),4) for k in
                                ('power','hp','physical','magical','crit_chance','crit_multiplier')}})
    by={(r['level'],r['quality'],r['family']):r for r in rows}
    checks=[]
    for level in range(1,game.MAX_LEVEL+1):
        for family in balance.PROFILES:
            seq=[by[level,q,family] for q in range(5,11)]
            assert all(a['physical']<b['physical'] and a['magical']<b['magical'] for a,b in zip(seq,seq[1:])), (level,family)
            for kind in ('physical','magical'):
                ratio=seq[-1][kind]/seq[0][kind]
                assert 1.4<=ratio<=2.2, (level,family,kind,ratio)
        for q in range(5,11):
            warrior,wizard,rogue,healer,lucky,hybrid=(by[level,q,f] for f in ('warrior','wizard','rogue','healer','lucky','hybrid'))
            assert rogue['physical']>=warrior['physical']*.90
            assert healer['magical']>=wizard['magical']*.90
            assert lucky['physical']>=warrior['physical']*.75 and lucky['magical']>=wizard['magical']*.75
            assert hybrid['hp']>warrior['hp']*1.5 and hybrid['physical']<warrior['physical']
    checks.extend(['Mean physical/magical damage improves at every rarity for every family and live level.',
                   'Mythic/Common expected damage ratio stays between 1.4 and 2.2.',
                   'DEX and WIS specialist expected damage stays within 10% of STR and INT counterparts.',
                   'Lucky retains at least 75% of each specialist damage channel.',
                   'Hybrid trades physical damage for at least 50% more HP than Warrior.'])
    result={'balance_version':balance.VERSION,'seed':20260911,'samples_per_build':samples,
            'levels':list(range(1,game.MAX_LEVEL+1)),'checks':checks,'rows':rows,
            'limits':'Analytical unmodified hit expectations using current stats. No class rotations, enemy resistance, PvP or client playtest.'}
    (ROOT/'docs/GEM_BALANCE_SIMULATION.json').write_text(json.dumps(result,indent=2)+'\n')
    selected=[by[game.MAX_LEVEL,q,f] for f in ('warrior','wizard','rogue','healer','lucky','hybrid') for q in (5,10)]
    lines=['# Gem balance simulation', '',f'Level {game.MAX_LEVEL}; seed 20260911; {samples} full sets per family/tier. Expected unmodified damage per hit, not class DPS.', '',
           '| Family | Rarity | Stat budget | HP | Physical hit | Magical hit | Crit chance |',
           '|---|---|---:|---:|---:|---:|---:|']
    for r in selected:
        lines.append(f"| {r['family'].title()} | {balance.QUALITY_NAME[r['quality']]} | {r['power']:.0f} | {r['hp']:.0f} | {r['physical']:.2f} | {r['magical']:.2f} | {r['crit_chance']*100:.2f}% |")
    lines += ['',*['- '+c for c in checks],'',result['limits']]
    (ROOT/'docs/GEM_BALANCE_SIMULATION.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'builds':len(rows),'full_sets':len(rows)*samples,'checks':checks,'current_level_cap':game.MAX_LEVEL}))


if __name__=='__main__':run()
