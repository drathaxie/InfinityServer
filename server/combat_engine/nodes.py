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
    out = {"Name": "Damage", "DamageTypes": dtypes, "Damages": damages,
           "Targets": targets, "TargetHPs": hps}
    return _opt(out, props, "Immediate")


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
           "CD": int(props.get("CD") or 0)}
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
    ts = props["TS"] if "TS" in props else ctx.source.timestamp_ms()
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
    ts = props["Time"] if "Time" in props else ctx.source.timestamp_ms()
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
    out["Targets"] = props.get("Targets", 1)
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
    return {"Name": "Aura", "Hide": bool(props.get("Hide")),
            "Animation": props.get("Animation") or "",
            "AuraName": props.get("AuraName") or "",
            "Targets": ctx.resolve_targets(props, default=[ctx.caster]),
            "casterTS": props.get("casterTS", ctx.caster),
            "uniquenessType": props.get("uniquenessType", 1)}
