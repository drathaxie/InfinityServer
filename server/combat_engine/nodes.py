"""
Render-node renderers — one per AE `Node*` type, mirrored EXACTLY.

Each renderer turns one AUTHORED node (the props a skill graph stores) into the
RESOLVED node an Attack packet carries, which the client's matching Node*.Execute
renders (decompiled bodies: docs/combat-engine/ae_node_semantics.cs). The wire
key sets come from the 549 captured AE packets (docs/combat-engine/fixtures) —
required keys are always emitted (with AE's defaults when unauthored), optional
keys ONLY when authored, because the capture shows AE omits them.

Renderer contract: fn(ctx, props) -> resolved node dict (or None to skip).
Authored values pass through verbatim; only genuinely server-computed fields
(Damages/TargetHPs, timestamps, resolved Targets, the cast Slot) come from the
RenderContext, so replaying a captured cast reproduces it byte-for-byte while a
live cast computes fresh numbers through the same code path.
"""

RENDERERS = {}              # node Name -> renderer fn


def renderer(name):
    def deco(fn):
        RENDERERS[name] = fn
        return fn
    return deco


def render_node(ctx, props):
    """Render one authored node (None if the type is unknown or it resolves to
    nothing — e.g. a Damage with no living targets)."""
    fn = RENDERERS.get(props.get("Name"))
    return fn(ctx, props) if fn else None


def _opt(out, props, *keys):
    """Copy optional wire keys through only when authored (AE omits them)."""
    for k in keys:
        if k in props:
            out[k] = props[k]
    return out


# --- damage ------------------------------------------------------------------

@renderer("Damage")
def _damage(ctx, props):
    """NodeDamage: queued damage tickets (dispensed at the animation's hit frame
    or by DispenseDamage). DamageTypes is the POPUP kind (0 Normal, 1 Crit,
    2 Dodge, 3 Miss, 5 DoT), TargetHPs the server-authoritative remaining HP."""
    resolved = ctx.source.damage(ctx, props)
    if resolved is None:
        return None
    dtypes, damages, targets, hps = resolved
    _record_hits(ctx, damages, targets, hps)
    out = {"Name": "Damage", "DamageTypes": dtypes, "Damages": damages,
           "Targets": targets, "TargetHPs": hps}
    return _opt(out, props, "Immediate")


def _record_hits(ctx, damages, targets, hps):
    """Remember which enemies this cast actually struck (and survived), so a
    later rule node can target them with "@hits" — that is how the branch
    debuffs land on "what the swing hit" rather than the single cast target."""
    hits = ctx.vars.setdefault("_hits", [])
    for i, ts in enumerate(targets):
        if not (isinstance(ts, str) and ts.startswith("m:")):
            continue
        if i < len(damages) and damages[i] < 0:
            continue                                   # a heal, not a hit
        if i < len(hps) and hps[i] <= 0:
            continue                                   # died to this hit
        if ts not in hits:
            hits.append(ts)


@renderer("InstantDamage")
def _instant_damage(ctx, props):
    """NodeInstantDamage: dispenses immediately (no ticket queue) — the shape
    the InfinityHero's stream hits use."""
    resolved = ctx.source.damage(ctx, props)
    if resolved is None:
        return None
    dtypes, damages, targets, hps = resolved
    out = {"Name": "InstantDamage", "DamageTypes": dtypes, "Damages": damages,
           "Targets": targets, "TargetHPs": hps}
    return _opt(out, props, "ImpactSound")


@renderer("DispenseDamage")
def _dispense(ctx, props):
    """NodeDispenseDamage: flush the caster's queued damage tickets now."""
    return {"Name": "DispenseDamage"}


# --- skill bar ---------------------------------------------------------------

@renderer("Cooldown")
def _cooldown(ctx, props):
    """NodeCooldown: start a slot's cooldown ring, optionally deferred to an
    Animation cue. (Two captured variants exist: the normal one, and a bare
    {Slot,CD,success} ack with no Animation — preserve whichever is authored.)"""
    out = {"Name": "Cooldown", "Slot": props.get("Slot", ctx.slot),
           "CD": ctx.source.cooldown_ms(ctx, props)}
    if "success" in props:
        _opt(out, props, "success", "Animation")
    else:
        out["Animation"] = props.get("Animation") or ""
    return out


@renderer("SetSkillIndex")
def _set_skill_index(ctx, props):
    """NodeSetSkillIndex: swap a slot's icon (the combo rebind). The client
    only reads Slot+Icon; Index/hide ride along in every captured packet."""
    return {"Name": "SetSkillIndex", "Slot": props.get("Slot", ctx.slot),
            "Index": int(props.get("Index") or 0),
            "Icon": props.get("Icon") or "",
            "hide": bool(props.get("hide"))}


@renderer("IndexReset")
def _index_reset(ctx, props):
    """NodeIndexReset: arm the combo reset ring on a slot — after Time ms the
    icon reverts (Icon), optionally sharing the timer across slots (Shared).
    TS is the server's send-time (ms) so the client can skew-correct."""
    ts = props["TS"] if "TS" in props else ctx.source.timestamp_ms("IndexReset")
    return {"Name": "IndexReset", "Slot": props.get("Slot", ctx.slot),
            "Index": int(props.get("Index") or 0),
            "Time": int(props.get("Time") or 0),
            "Icon": props.get("Icon") or "",
            "CD": int(props.get("CD") or 0),
            "Shared": bool(props.get("Shared")),
            "Stay": bool(props.get("Stay")),
            "TS": ts}


@renderer("Resource")
def _resource(ctx, props):
    """NodeResource: set the caster's RP bar to Amount (absolute, not a delta —
    the server owns the pool; this reports the post-cast total)."""
    return {"Name": "Resource", "Amount": ctx.source.resource_total(ctx, props)}


# --- targeting / spatial -----------------------------------------------------

@renderer("Range")
def _range(ctx, props):
    """NodeRange (Execute side): assert the cast target + facing."""
    return {"Name": "Range", "HRange": props.get("HRange", 5.0),
            "VRange": props.get("VRange", 1.0),
            "Target": props.get("Target", ctx.target or ""),
            "Charge": bool(props.get("Charge")),
            "HoldAtRange": bool(props.get("HoldAtRange"))}


@renderer("RangeMulti")
def _range_multi(ctx, props):
    """NodeRangeMulti (Execute side): multi-target acquisition result. Target
    is the MODE string ("Self"/"Hostile"), Targets the resolved entity list."""
    return {"Name": "RangeMulti", "HRange": props.get("HRange", 5.0),
            "VRange": props.get("VRange", 1.0),
            "Target": props.get("Target", "Self"),
            "Targets": ctx.resolve_targets(props)}


@renderer("AnimationHitbox")
def _animation_hitbox(ctx, props):
    """NodeAnimationHitbox: the swing's spatial box, registered at Time into
    Animation; Targets = who the box already resolved to (server echo)."""
    return {"Name": "AnimationHitbox",
            "X": props.get("X", 0.0), "Y": props.get("Y", 0.0),
            "Width": props.get("Width", 1), "Height": props.get("Height", 1),
            "Animation": props.get("Animation") or "",
            "Speed": props.get("Speed", 1.0),
            "Time": props.get("Time", 0.0),
            "Targets": ctx.resolve_targets(props)}


@renderer("DashToTarget")
def _dash_to_target(ctx, props):
    """NodeDashToTarget: close to a target over Duration ms. AE emits BOTH
    ForceMovement and forceMovement (a server-side casing wart) — mirror it."""
    fm = props.get("ForceMovement", props.get("forceMovement", False))
    out = {"Name": "DashToTarget", "Target": props.get("Target", ctx.target or ""),
           "Face": bool(props.get("Face")),
           "OffsetX": props.get("OffsetX", 0.0),
           "Duration": int(props.get("Duration") or 400),
           "Async": bool(props.get("Async")),
           "Animation": props.get("Animation") or "None",
           "ForceMovement": bool(fm), "forceMovement": bool(fm)}
    return out


@renderer("PlayerHitStream")
def _player_hit_stream(ctx, props):
    """NodePlayerHitStream: a player-owned damage-over-area tile (PlayerHotTile)
    ticking every Interval ms for Duration ms. Time is the server epoch-ms."""
    ts = props["Time"] if "Time" in props else ctx.source.timestamp_ms("PlayerHitStream")
    out = {"Name": "PlayerHitStream",
           "X": props.get("X", 0.0), "Y": props.get("Y", 0.0),
           "Width": props.get("Width", 1.0), "Height": props.get("Height", 1.0),
           "Duration": int(props.get("Duration") or 0),
           "Interval": int(props.get("Interval") or 1000),
           "Origin": props.get("Origin") or "Target",
           "Slot": props.get("Slot", ctx.slot),
           "Time": ts}
    if out["Origin"] == "Target" and "OriginTarget" not in props and ctx.target:
        out["OriginTarget"] = ctx.target
    _opt(out, props, "OriginTarget", "VFX")
    return out


# --- animation / presentation ------------------------------------------------

@renderer("PlayerAnimation")
def _player_animation(ctx, props):
    """NodePlayerAnimation: play a caster animation; a comma list is a random
    variant pick client-side. Speed is optional on the wire."""
    out = {"Name": "PlayerAnimation", "Animation": props.get("Animation") or "",
           "Priority": props.get("Priority") or "Attack"}
    _opt(out, props, "Speed")
    # Targets is a COUNT on the wire (how many actors play it) — a captured
    # packet already carries the number; an authored ref resolves to its size.
    t = props.get("Targets", 1)
    out["Targets"] = t if isinstance(t, int) else len(ctx.resolve_targets(props))
    return out


@renderer("UpdateAnimation")
def _update_animation(ctx, props):
    """NodeUpdateAnimation: retag the caster's combatIdle/walk/idle base state."""
    return {"Name": "UpdateAnimation", "Tag": props.get("Tag") or "combatIdle",
            "Value": props.get("Value") or ""}


@renderer("Restrict")
def _restrict(ctx, props):
    """NodeRestrict: lock the caster's direction/movement/skills while
    Animation plays (Slot = exempted slots, a comma STRING on the wire)."""
    return {"Name": "Restrict", "Direction": bool(props.get("Direction", True)),
            "Movement": bool(props.get("Movement")),
            "Skills": bool(props.get("Skills")),
            "Slot": str(props.get("Slot") or ""),
            "Animation": props.get("Animation") or "",
            "ReleaseMode": props.get("ReleaseMode") or "AtTime",
            "Time": props.get("Time", 0.0)}


@renderer("Interruptable")
def _interruptable(ctx, props):
    """NodeInterruptable: the window (Time s into Animation) where a hit
    cancels the cast."""
    return {"Name": "Interruptable", "Animation": props.get("Animation") or "",
            "Time": props.get("Time", 0.0)}


@renderer("SoundFX")
def _sound_fx(ctx, props):
    """NodeSoundFX: cue Sound at Time s into Animation (comma list = random)."""
    return {"Name": "SoundFX", "Animation": props.get("Animation") or "",
            "Sound": props.get("Sound") or "",
            "Time": props.get("Time", 0.0),
            "MinPitch": props.get("MinPitch", 0.0),
            "MaxPitch": props.get("MaxPitch", 0.0)}


@renderer("ImpactSoundFX")
def _impact_sound_fx(ctx, props):
    """NodeImpactSoundFX: the hit-impact sound keyed to an animation/FX name."""
    return {"Name": "ImpactSoundFX", "Animation": props.get("Animation") or "",
            "Sound": props.get("Sound") or "",
            "MinPitch": props.get("MinPitch", 0.0),
            "MaxPitch": props.get("MaxPitch", 0.0)}


@renderer("Particle")
def _particle(ctx, props):
    """NodeParticle: spawn a class particle on Targets, cued to Animation@Time.
    Time is a STRING on the wire (AE server quirk); AnimSpeed/Lifetime only
    when authored; Animation omitted for uncued (immediate) spawns."""
    out = {"Name": "Particle", "Follow": props.get("Follow") or "No Follow",
           "X": props.get("X", 0.0), "Y": props.get("Y", 0.0),
           "Particle": props.get("Particle") or ""}
    _opt(out, props, "Animation")
    out["Time"] = props["Time"] if isinstance(props.get("Time"), str) \
        else str(props.get("Time") if props.get("Time") is not None else 0)
    _opt(out, props, "AnimSpeed", "Lifetime")
    out["Targets"] = ctx.resolve_targets(props, default=[ctx.caster])
    return out


@renderer("SpellAnimation")
def _spell_animation(ctx, props):
    """NodeSpellAnimation: projectile/meteor/wrapper spell FX. X/Y, Ease,
    ProjSpeed and impactId are optional on the wire (variant-dependent)."""
    out = {"Name": "SpellAnimation", "Animation": props.get("Animation") or "",
           "FX": props.get("FX") or "ORIGIN",
           "SpellGraphic": props.get("SpellGraphic") or "",
           "SpellImpact": props.get("SpellImpact") or "",
           "AttachInit": props.get("AttachInit") or "CastAttach",
           "Attach": props.get("Attach") or "Cast",
           "AttachImpact": props.get("AttachImpact") or "Origin",
           "Follow": bool(props.get("Follow"))}
    _opt(out, props, "X", "Y", "Ease", "ProjSpeed", "impactId")
    out["Targets"] = props.get("Targets", 1)
    out["target"] = props.get("target", ctx.target or "")
    return out


@renderer("Aura")
def _aura(ctx, props):
    """NodeAura: add AuraName to Targets (Hide=no popup — AE's aspect markers
    are hidden auras). The server-side effect (stat mods, duration) is the rule
    layer's job; this node is the client notification."""
    tgts = ctx.resolve_targets(props, default=[ctx.caster])
    if props.get("MaxTargets") is not None:     # authored cap (party buffs); the
        tgts = tgts[:int(props["MaxTargets"])]  # key itself never hits the wire
    # NB: an EMPTY Targets list still renders — real AE casts carry
    # {"Targets": []} Aura nodes (Concealed Blade, Heroic Empowerment).
    return {"Name": "Aura", "Hide": bool(props.get("Hide")),
            "Animation": props.get("Animation") or "",
            "AuraName": props.get("AuraName") or "",
            "Targets": tgts,
            "casterTS": props.get("casterTS", ctx.caster),
            "uniquenessType": props.get("uniquenessType", 1)}


# --- the remaining AE vocabulary (no fixture coverage — schemas read straight
# --- from each Node*'s decompiled Execute/Input body) -------------------------

@renderer("AnimationCancel")
def _animation_cancel(ctx, props):
    """NodeAnimationCancel: force-cancel the caster's current animation."""
    return {"Name": "AnimationCancel"}


@renderer("AuraVFX")
def _aura_vfx(ctx, props):
    """NodeAuraVFX: bind a persistent <VFX>_Appear/_Exit particle pair to an
    aura's lifetime on the caster."""
    return {"Name": "AuraVFX", "AuraName": props.get("AuraName") or "",
            "VFX": props.get("VFX") or ""}


@renderer("ImpactAura")
def _impact_aura(ctx, props):
    """NodeImpactAura: queue SpellImpact to fire when AuraName lands."""
    return {"Name": "ImpactAura", "AuraName": props.get("AuraName") or "",
            "SpellImpact": props.get("SpellImpact") or ""}


@renderer("Channel")
def _channel(ctx, props):
    """NodeChannel: start the client's skill-stream (channelled cast) loop."""
    return {"Name": "Channel"}


@renderer("StopChannel")
def _stop_channel(ctx, props):
    """NodeStopChannel: end the skill-stream loop."""
    return {"Name": "StopChannel"}


@renderer("Hit")
def _hit_cue(ctx, props):
    """NodeHit: register a hit cue at Time s into Animation (drives impact
    sounds/damage-ticket dispensing on that frame)."""
    return {"Name": "Hit", "Animation": props.get("Animation") or "",
            "Time": props.get("Time", 0.0)}


@renderer("Hitbox")
def _hitbox(ctx, props):
    """NodeHitbox: an immediate spatial box (Input-resolved client-side; the
    Execute body is empty). Emitted with its box geometry for the igai path."""
    out = {"Name": "Hitbox", "X": props.get("X", 0.0), "Y": props.get("Y", 0.0),
           "Width": props.get("Width", 1.0), "Height": props.get("Height", 1.0)}
    return _opt(out, props, "OriginTarget")


@renderer("Dash")
def _dash(ctx, props):
    """NodeDash: dash the caster OffsetX over Duration ms (Animation-gated on
    the input side)."""
    out = {"Name": "Dash", "Duration": int(props.get("Duration") or 400),
           "OffsetX": props.get("OffsetX", 0.0)}
    return _opt(out, props, "Animation")


@renderer("MoveTargets")
def _move_targets(ctx, props):
    """NodeMoveTargets: group-move entities toward the caster. Targets is a
    COMMA-JOINED STRING on this node (unlike every list-shaped node)."""
    t = props.get("Targets")
    if not isinstance(t, str):
        t = ",".join(ctx.resolve_targets(props))
    return {"Name": "MoveTargets", "Targets": t,
            "OffsetX": props.get("OffsetX", 0.0),
            "Duration": int(props.get("Duration") or 0)}


@renderer("DisableSkill")
def _disable_skill(ctx, props):
    """NodeDisableSkill: grey out / re-enable a skill slot."""
    return {"Name": "DisableSkill", "Slot": props.get("Slot", ctx.slot),
            "Disabled": bool(props.get("Disabled", True))}


@renderer("SkillGlow")
def _skill_glow(ctx, props):
    """NodeSkillGlow: toggle a slot's ready-glow."""
    return {"Name": "SkillGlow", "Slot": props.get("Slot", ctx.slot),
            "Active": bool(props.get("Active", True))}


@renderer("UpdateIcon")
def _update_icon(ctx, props):
    """NodeUpdateIcon: permanently swap a slot's icon (no reset ring)."""
    return {"Name": "UpdateIcon", "Slot": props.get("Slot", ctx.slot),
            "Icons": props.get("Icons") or ""}


@renderer("SwapSkill")
def _swap_skill(ctx, props):
    """NodeSwapSkill: replace the SKILL in a slot (a full SkillData object, or
    a string to clear). The InfinityHero-style combo uses SetSkillIndex (icon
    only); SwapSkill rebinds what the slot casts."""
    return {"Name": "SwapSkill", "Slot": props.get("Slot", ctx.slot),
            "Skill": props.get("Skill", "")}


@renderer("MaxSkillHold")
def _max_skill_hold(ctx, props):
    """NodeMaxSkillHold: show the hold-to-release bar on a slot for Time ms."""
    return {"Name": "MaxSkillHold", "Slot": props.get("Slot", ctx.slot),
            "Time": int(props.get("Time") or 0)}


@renderer("GlobalCooldown")
def _global_cooldown(ctx, props):
    """NodeGlobalCooldown: per-slot cooldown list (index = slot; -1 skips)."""
    return {"Name": "GlobalCooldown", "CD": list(props.get("CD") or [])}


@renderer("Message")
def _message(ctx, props):
    """NodeMessage: modal message box."""
    return {"Name": "Message", "Title": props.get("Title") or "",
            "Text": props.get("Text") or ""}


@renderer("MonTransform")
def _mon_transform(ctx, props):
    """NodeMonTransform: morph the caster into a monster prefab
    (Bundle+Linkage+Scale), or detransform:true to revert."""
    if props.get("detransform"):
        return {"Name": "MonTransform", "detransform": True}
    out = {"Name": "MonTransform", "Bundle": props.get("Bundle"),
           "Linkage": props.get("Linkage") or ""}
    return _opt(out, props, "Scale")


@renderer("MonsterMove")
def _monster_move(ctx, props):
    """NodeMonsterMove: reposition a monster to (destX, destY) — Mode "Teleport"
    snaps (TeleportApply), anything else walks at speed (WalkApply)."""
    out = {"Name": "MonsterMove", "destX": props.get("destX", 0.0),
           "destY": props.get("destY", 0.0)}
    return _opt(out, props, "speed", "Mode")


@renderer("SpawnPickup")
def _spawn_pickup(ctx, props):
    """NodeSpawnPickup: drop a walk-over pickup near the caster/OriginTarget."""
    out = {"Name": "SpawnPickup", "PickupId": int(props.get("PickupId") or 0),
           "SpawnOffsetX": props.get("SpawnOffsetX", 0.0),
           "SpawnOffsetY": props.get("SpawnOffsetY", 0.0)}
    return _opt(out, props, "OriginTarget", "Prefab", "CollisionWidth",
                "CollisionHeight", "IAcceptNextQuest")


@renderer("ConditionalRange")
def _conditional_range(ctx, props):
    """NodeConditionalRange (Input-only): range gate on the current target.
    Lowercase keys — this node rides the igai Response, not Attack Nodes."""
    return {"Name": "ConditionalRange", "hrange": props.get("hrange", 5.0),
            "vrange": props.get("vrange", 1.0),
            "type": props.get("type") or "Hostile"}


@renderer("RestrictRelease")
def _restrict_release(ctx, props):
    """NodeRestrictRelease: lift a Restrict early (all locks, or only the one
    keyed to Animation)."""
    out = {"Name": "RestrictRelease"}
    return _opt(out, props, "Animation")


# --- monster tile telegraphs (MonsterInput-side: these ride a MonReq packet's
# --- Response, keyed by Name — the client renders the telegraph and reports
# --- hits back via gmah/RequestMonHit; see forge.monster_skills) --------------

def _tile(ctx, props, required, optional):
    out = {"Name": props["Name"]}
    for k, d in required:
        out[k] = props.get(k, d)
    return _opt(out, props, *optional)


@renderer("HitTiles")
def _hit_tiles(ctx, props):
    """NodeHitTiles.MonsterInput: one filled telegraph tile under the player
    (Shape: Circle | Rectangle | VerticalRectangle)."""
    return _tile(ctx, props,
                 [("Shape", "Circle"), ("Speed", 1.0),
                  ("ScaleX", 1.0), ("ScaleY", 1.0)],
                 ["CastAnimation", "VFX", "FinishAnimation"])


@renderer("TileWave")
def _tile_wave(ctx, props):
    """NodeTileWave.MonsterInput: a wave sweeping the frame (WaveTile prefab);
    hits report immediately (OnHit), a survived finish reports success."""
    return _tile(ctx, props, [("Speed", 1.0)],
                 ["CastAnimation", "DuringAnimation", "FinishAnimation",
                  "ImpactSound"])


@renderer("TileCluster")
def _tile_cluster(ctx, props):
    """NodeTileCluster.MonsterInput: a scatter of tiles; ClusterOffsets (a flat
    [x1,y1,x2,y2,...] of >=8 pairs) pins the pattern server-side."""
    return _tile(ctx, props,
                 [("Speed", 1.0), ("ScaleX", 1.0), ("ScaleY", 1.0)],
                 ["CastAnimation", "VFX", "DuringAnimation", "FinishAnimation",
                  "ImpactSound", "ClusterOffsets"])


@renderer("TileMove")
def _tile_move(ctx, props):
    """NodeTileMove.MonsterInput: a MOVE-HERE tile the player must reach."""
    return _tile(ctx, props, [("Speed", 1.0)],
                 ["CastAnimation", "FinishAnimation"])


@renderer("TileSafe")
def _tile_safe(ctx, props):
    """NodeTileSafe.MonsterInput: the inverse telegraph — stand IN the tile to
    be safe; being caught outside reports the hit."""
    return _tile(ctx, props,
                 [("Speed", 1.0), ("ScaleX", 1.0), ("ScaleY", 1.0)],
                 ["CastAnimation", "VFX", "DuringAnimation", "FinishAnimation",
                  "ImpactSound", "DelayedAnimation", "DelayedAnimationTime",
                  "SafeOffsetX", "SafeOffsetY"])


@renderer("TileTrack")
def _tile_track(ctx, props):
    """NodeTileTrack.MonsterInput: a tile that TRACKS the player (Track:
    Sides | Center) before locking and detonating."""
    return _tile(ctx, props,
                 [("Track", "Sides"), ("Shape", "Circle"), ("Speed", 1.0),
                  ("ScaleX", 1.0), ("ScaleY", 1.0)],
                 ["CastAnimation", "VFX", "FinishAnimation",
                  "DelayedAnimation", "DelayedAnimationTime"])


@renderer("HitStream")
def _hit_stream(ctx, props):
    """NodeHitStream.Execute: a lingering damage strip (HotTile) at PosX/PosY —
    Ragnafluff's firewalls. Time is the server epoch-ms the zone armed."""
    ts = props["Time"] if "Time" in props else ctx.source.timestamp_ms()
    out = {"Name": "HitStream",
           "PosX": props.get("PosX", 0.0), "PosY": props.get("PosY", 0.0),
           "Speed": props.get("Speed", 1.0),
           "ScaleX": props.get("ScaleX", 1.0), "ScaleY": props.get("ScaleY", 1.0),
           "Time": ts, "Duration": int(props.get("Duration") or 0)}
    return _opt(out, props, "CastAnimation", "VFX", "DuringAnimation",
                "CompletedAnimation", "FinishAnimation")
