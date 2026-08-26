"""Regression checks for targetless combat casts, including mobile auto-target selection."""
import combat


AREA = "auto-target-test"
UID = 7171


def reset_world():
    for state in (combat._mon, combat._maxhp, combat._moninfo, combat._aggro, combat._auto):
        state.clear()


def add(target, *, frame="Enter", x=0, y=0, hp=100):
    combat.register_monster(AREA, target, max(1, hp), mon_id=1, frame=frame, x=x, y=y)
    if hp <= 0:
        combat._mon[(AREA, target)] = hp


def test_targetless_cast_selects_nearest_living_monster_in_current_cell():
    reset_world()
    add("m:far", x=10, y=0)
    add("m:near", x=2, y=1)
    add("m:other-cell", frame="Room2", x=0, y=0)
    add("m:dead", x=0, y=0, hp=0)

    assert combat.resolve_combat_target(AREA, UID, frame="Enter", x=0, y=0) == "m:near"


def test_explicit_and_active_targets_take_priority_over_nearest_fallback():
    reset_world()
    add("m:nearest", x=1, y=0)
    add("m:explicit", x=20, y=0)
    assert combat.resolve_combat_target(
        AREA, UID, requested="m:explicit", frame="Enter", x=0, y=0) == "m:explicit"

    combat.engage(AREA, "m:explicit", UID)
    assert combat.resolve_combat_target(AREA, UID, frame="Enter", x=0, y=0) == "m:explicit"


def test_targetless_cast_in_an_empty_current_cell_still_fails_cleanly():
    reset_world()
    add("m:other-cell", frame="Room2", x=0, y=0)
    assert combat.resolve_combat_target(AREA, UID, frame="Enter", x=0, y=0) is None


def main():
    test_targetless_cast_selects_nearest_living_monster_in_current_cell()
    test_explicit_and_active_targets_take_priority_over_nearest_fallback()
    test_targetless_cast_in_an_empty_current_cell_still_fails_cleanly()
    print("auto target tests passed")


if __name__ == "__main__":
    main()
