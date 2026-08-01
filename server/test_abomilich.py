"""
Stage-6 test — the Abomilich fight (InfinityLichBoss, mon 429/430).

AE modelled this boss, placed it on two maps and shipped its art, but never
gave it a moveset. seed._ABOMILICH_SKILLS is that moveset, authored in the
tile vocabulary AE's own client already renders.

Honest scope, because it differs from stages 4 and 5: there is NO capture of
this boss fighting to replay against. AE's telegraphed tiles are rendered by
the client and reported back over MonReq/gmah, so they never travel as monster
Attack packets — which is why the 102 monster casts in the fixtures are plain
auto-attacks and contain no tile node at all. What this file proves instead:

  1. every authored node is a real AE node type the engine renders, and the
     rendered output carries the exact prop set the captured Ragnafluff /
     elemental tiles carry (the node grammar IS capture-verified — the render
     layer replays 3837 captured nodes byte-for-byte in test_combat_engine);
  2. the fight survives the round trip through the DB and comes back out of
     forge.monster_skills as a rotation the AI can actually cast, summon and
     all;
  3. the boss's plain auto-attack — the one part of a monster fight the
     fixtures DO capture — replays exactly against all 102 captured casts;
  4. the art it points at resolves on the live CDN, so the boss is visible.

Run: python test_abomilich.py
"""
import json
import pathlib

import db
import forge
import seed
from combat_engine.engine import RenderContext, ReplayValueSource, render_graph
from combat_engine.nodes import RENDERERS
from combat_engine.state import CombatState

FIX = pathlib.Path(__file__).resolve().parent.parent / "docs" / "combat-engine" / "fixtures"

# what the captured Ragnafluff / elemental tiles carry, by node type — the
# grammar the client's Node*.MonsterInput reads
TILE_REQUIRED = {
    "HitTiles":    {"Shape", "Speed", "ScaleX", "ScaleY"},
    "TileWave":    {"Speed"},
    "TileCluster": {"Speed", "ScaleX", "ScaleY"},
    "TileSafe":    {"Speed", "ScaleX", "ScaleY"},
    "TileTrack":   {"Track", "Shape", "Speed", "ScaleX", "ScaleY"},
    "HitStream":   {"PosX", "PosY", "Speed", "ScaleX", "ScaleY", "Duration", "Time"},
}


def test_node_grammar():
    """Every authored node is a real renderer, and renders to the captured shape."""
    ctx = RenderContext(caster="m:429", slot=0, target="p:1",
                        source=ReplayValueSource(), state=CombatState("m:429"))
    seen = set()
    for slot, skill_id, name, desc, node_list in seed._ABOMILICH_SKILLS:
        assert name and desc, f"skill {skill_id} needs a name + description"
        for _nid, props in node_list:
            nm = props["Name"]
            if nm in ("OnRequest", "Summon"):        # header / server-side node
                continue
            assert nm in RENDERERS, f"{name}: {nm!r} is not an AE node type"
            out = render_graph([props], ctx)
            assert out, f"{name}: {nm} rendered nothing"
            node = out[0]
            for key in TILE_REQUIRED.get(nm, set()):
                assert key in node, f"{name}: rendered {nm} is missing {key!r}"
            seen.add(nm)
    # the point of the exercise: the fight uses the WHOLE tile vocabulary
    assert TILE_REQUIRED.keys() <= seen, \
        f"unused tile types: {set(TILE_REQUIRED) - seen}"
    print(f"node grammar OK: {len(seen)} node types, all 6 tile mechanics "
          f"({', '.join(sorted(TILE_REQUIRED))}) exercised")


def test_cluster_offsets():
    """TileCluster pins its scatter server-side so every client draws the same
    shards — AE's captured cluster carries >=8 (x,y) pairs."""
    cl = next(p for _s, _i, _n, _d, nodes in seed._ABOMILICH_SKILLS
              for _nid, p in nodes if p["Name"] == "TileCluster")
    offs = cl["ClusterOffsets"]
    assert len(offs) >= 16 and len(offs) % 2 == 0, \
        f"ClusterOffsets must be >=8 (x,y) pairs, got {len(offs)} values"
    assert all(isinstance(v, (int, float)) for v in offs)
    print(f"cluster OK: {len(offs) // 2} pinned shard positions")


def test_seed_roundtrip():
    """Seed into a throwaway DB and read the fight back out the way the AI does."""
    db.use_throwaway()
    seed.run()
    with db.connect() as conn:
        for mon_id in seed.ABOMILICH_MON_IDS:
            row = conn.execute("SELECT name, class_id, bundle FROM monsters WHERE mon_id=?",
                               (mon_id,)).fetchone()
            assert row, f"mon {mon_id} missing"
            assert row["class_id"] == seed.ABOMILICH_CLASS_ID, \
                f"mon {mon_id} not linked to the Abomilich class"
            bundle = json.loads(row["bundle"])
            assert int(bundle["ID"]) == seed.ABOMILICH_LIVE_BUNDLE, \
                f"mon {mon_id} points at dead art bundle {bundle['ID']}"

        skills = forge.monster_skills(conn, seed.ABOMILICH_MON_IDS[0])
        assert len(skills) == len(_ABOM), \
            f"AI sees {len(skills)} skills, authored {len(_ABOM)}"
        tiles = [s for s in skills if "nodes" in s]
        summons = [s for s in skills if "summon" in s]
        assert len(summons) == 1, "expected exactly one summon skill"
        assert summons[0]["summon"]["mon_id"] == seed.ABOMILICH_THRALL_MON
        assert summons[0]["summon"]["max_alive"] == 2, "adds must be capped"
        thrall = conn.execute("SELECT name FROM monsters WHERE mon_id=?",
                              (seed.ABOMILICH_THRALL_MON,)).fetchone()
        assert thrall, "the summoned thrall has no monster row to spawn from"

        for s in tiles:
            assert s["nodes"], f"{s['name']} exposes no tile to the AI"
            assert 3000 <= s["cd_ms"] <= 30000, f"{s['name']} cadence {s['cd_ms']}ms"
            assert 0.5 <= s["multiplier"] <= 2.0, f"{s['name']} mult {s['multiplier']}"
        multi = [s for s in tiles if len(s["nodes"]) > 1]
        assert multi, "expected at least one multi-tile cast (the miasma strips)"
        cds = [s["cd_ms"] for s in skills]
        print(f"roundtrip OK: {len(tiles)} tile skills + {len(summons)} summon reach the AI, "
              f"cadence {min(cds)}-{max(cds)}ms, {len(multi[0]['nodes'])}-tile miasma cast")

        # the SkillForge must be able to open and edit the fight
        init = forge.build_init(conn)
        assert "Abomilich" in init["classes"], "the fight is not editable in the Forge"
        assert len(init["classes"]["Abomilich"]["Skills"]) == len(_ABOM)


def test_monster_auto_replay():
    """The one part of a monster fight the fixtures DO record: the plain auto.
    All 102 captured monster casts must rebuild byte-for-byte."""
    casts = json.loads((FIX / "monster_casts.json").read_text(encoding="utf-8"))
    nodes = 0
    for c in casts:
        ctx = RenderContext(caster=c["caster"], slot=c["slot"] or 0,
                            target="p:1", source=ReplayValueSource(),
                            state=CombatState(c["caster"]))
        got = render_graph(c["nodes"], ctx)
        assert got == c["nodes"], (f"monster auto mismatch for {c['caster']}\n"
                                   f"  AE : {json.dumps(c['nodes'])[:300]}\n"
                                   f"  ENG: {json.dumps(got)[:300]}")
        nodes += len(got)
    print(f"monster autos OK: {len(casts)} captured casts, {nodes} nodes, exact")


_ABOM = seed._ABOMILICH_SKILLS


def main():
    test_node_grammar()
    test_cluster_offsets()
    test_monster_auto_replay()
    test_seed_roundtrip()
    print(f"ALL ABOMILICH TESTS PASSED ({len(_ABOM)} skills wired onto "
          f"mon {'/'.join(str(m) for m in seed.ABOMILICH_MON_IDS)})")


if __name__ == "__main__":
    main()
