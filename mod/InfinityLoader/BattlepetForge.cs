using System;
using HarmonyLib;
using Newtonsoft.Json.Linq;
using UnityEngine;

// Opt-in prototype: Forge commands the owner's existing follower. This never
// creates combat entities, rolls damage or changes equipment/visibility settings.
internal sealed class BattlepetForge : MonoBehaviour
{
    private static bool installed;
    private Entity owner;
    private FollowerGameObject follower;
    private Animator animator;
    private float oldSpeed;
    private float remaining;
    private bool playing;
    private string ownerFrame;

    internal static void Install(Harmony harmony)
    {
        var walk = AccessTools.DeclaredMethod(typeof(FollowerGameObject), "UpdateWalkAnimation");
        if (walk == null) throw new MissingMemberException("Native pet animation contract changed");
        // Node's initializer constructs a managed dispatch dictionary. Do not
        // instantiate Unity objects at the Doorstop entrypoint.
        if (Node.Execute.ContainsKey("PetAnimation"))
            throw new InvalidOperationException("PetAnimation is already registered");
        harmony.Patch(walk, prefix: new HarmonyMethod(typeof(BattlepetForge), nameof(AllowFollowAnimation)));
        Node.Execute.Add("PetAnimation", Execute);
        installed = true;
    }
    internal static void IngestPacket(string raw)
    {
        if (!installed || raw == null || raw.IndexOf("\"initPlayer\"", StringComparison.Ordinal) < 0) return;
        try
        {
            if ((string)JObject.Parse(raw)["Cmd"] == "initPlayer")
                AEC.Instance.sendRequest(new RequestCmd("bphello", new[] { "bphello", "1" }));
        }
        catch (Exception error) { Debug.LogWarning("[battlepet] Capability handshake failed: " + error.Message); }
    }

    private static bool AllowFollowAnimation(FollowerGameObject __instance)
    {
        var bridge = __instance.GetComponent<BattlepetForge>();
        return bridge == null || !bridge.playing;
    }

    internal static bool ValidCommand(string state, float duration, float speed)
    {
        return (state == "Attack1" || state == "Attack2" || state == "Buff" || state == "PowerUp") &&
            !float.IsNaN(duration) && !float.IsInfinity(duration) && duration >= .05f && duration <= 10f &&
            !float.IsNaN(speed) && !float.IsInfinity(speed) && speed >= .1f && speed <= 4f;
    }

    internal static void Execute(Entity caster, int instanceID, JObject props)
    {
        // Damage nodes for the first class fixture are Immediate: this visual
        // command must not strand tickets on a player animation that never runs.
        if (caster == null || caster.hasDied || !caster.inSameFrame() || caster.petGO == null ||
            !caster.petGO.activeInHierarchy) return;
        string state = (string)props["Animation"];
        float duration = (float?)props["Duration"] ?? 0;
        float speed = (float?)props["Speed"] ?? 1;
        if (!ValidCommand(state, duration, speed)) return;
        var follow = caster.petGO.GetComponent<FollowerGameObject>();
        var anim = caster.petGO.GetComponentInChildren<Animator>();
        if (follow == null || anim == null || anim.runtimeAnimatorController == null ||
            !anim.HasState(0, Animator.StringToHash(state))) return;
        var bridge = caster.petGO.GetComponent<BattlepetForge>() ?? caster.petGO.AddComponent<BattlepetForge>();
        bridge.Begin(caster, follow, anim, state, duration, speed);
    }

    private void Begin(Entity caster, FollowerGameObject follow, Animator anim,
                       string state, float duration, float speed)
    {
        Finish();
        owner = caster;
        ownerFrame = caster.Frame;
        follower = follow;
        animator = anim;
        oldSpeed = anim.speed;
        remaining = duration / speed;
        playing = true;
        anim.speed = speed;
        anim.Play(state, 0, 0f);
        anim.gameObject.GetComponent<FlashBattlePet>()?.Command(state, speed);
    }

    private void Update()
    {
        if (!playing) return;
        if (owner == null || owner.hasDied || owner.petGO != gameObject ||
            owner.Frame != ownerFrame || !owner.inSameFrame()) { Finish(); return; }
        remaining -= Time.deltaTime;
        if (remaining <= 0) Finish();
    }

    private void Finish()
    {
        if (!playing) return;
        playing = false;
        if (animator != null)
        {
            animator.speed = oldSpeed;
            bool walking = follower != null && follower.walk != null && follower.walk.isWalking;
            string state = walking ? "Walk" : "Idle";
            if (animator.HasState(0, Animator.StringToHash(state))) animator.Play(state, 0, 0f);
            animator.gameObject.GetComponent<FlashBattlePet>()?.Follow();
            if (follower != null) follower.isWalking = walking;
        }
        owner = null;
    }

    private void OnDisable() => Finish();
}
