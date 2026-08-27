using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using Newtonsoft.Json.Linq;
using UnityEngine;

/// <summary>
/// TEMPORARY diagnostic instrumentation for the Infinity Hero sky-blade
/// (classInfinityHero_S1_P4), which spawns and then vanishes within ~1 frame.
///
/// Server-side logging cannot see any of this: by the time the packet leaves the
/// server everything is already correct (verified against the live DB and the
/// emitted Attack). The failure is entirely in the client's particle lifecycle, so
/// the only useful instrumentation lives here.
///
/// What it records, per spawn:
///   * which NodeParticle.Execute branch the cue took (immediate / idle-persistent /
///     queued-for-animation), because that decides whether it spawns at all;
///   * whether ParticlesManager actually returned a prefab;
///   * the spawned GameObject's activeInHierarchy, world position, lossy scale and
///     renderer/particle-system counts ON THE SPAWN FRAME;
///   * then the SAME readings again over the following frames, so we can see WHICH
///     of them changes when it disappears -- destroyed, deactivated, scaled to zero,
///     moved off-camera, or simply finished emitting.
///
/// That last part is the point: "a flash" has several possible causes and they are
/// indistinguishable from the server. This tells us which one it is instead of
/// another guess. Remove once the sky-blade renders.
/// </summary>
public static class ParticleDiagnostics
{
    // Only trace the effect under investigation -- tracing every particle would bury
    // the signal (a normal fight emits hundreds per minute).
    private const string Watch = "classInfinityHero_S1_P4";
    private const int FramesToTrack = 240;      // ~4s at 60fps: past TimedKill's 3s

    private static bool Interesting(string fx)
    {
        return !string.IsNullOrEmpty(fx) &&
               fx.IndexOf(Watch, StringComparison.OrdinalIgnoreCase) >= 0;
    }

    /// <summary>Prefix on NodeParticle.Execute: record which branch the cue will take.</summary>
    public static void OnExecute(object caster, JObject props)
    {
        try
        {
            string fx = props["Particle"] == null ? null : props["Particle"].Value<string>();
            if (!Interesting(fx)) return;

            bool hasAnim = props.SelectToken("Animation") != null;
            bool hasTime = props.SelectToken("Time") != null;
            string anim = hasAnim ? props["Animation"].Value<string>() : "<none>";
            string targets = props["Targets"] == null ? "<none>" : props["Targets"].ToString(Newtonsoft.Json.Formatting.None);

            // Mirror NodeParticle's own branch test so the log states the real path.
            string branch;
            if (!hasAnim || !hasTime)
            {
                branch = "IMMEDIATE (no Animation+Time -> SpawnParticle now)";
            }
            else
            {
                string idle = null, combatIdle = null;
                try
                {
                    var anm = caster == null ? null : caster.GetType().GetField("animation") != null
                        ? caster.GetType().GetField("animation").GetValue(caster) : null;
                    if (anm != null)
                    {
                        var idleF = anm.GetType().GetField("idleAnimation");
                        var ciF = anm.GetType().GetField("combatIdle");
                        var idleO = idleF == null ? null : idleF.GetValue(anm);
                        var ciO = ciF == null ? null : ciF.GetValue(anm);
                        if (idleO != null)
                        {
                            var f = idleO.GetType().GetField("animationState");
                            if (f != null) idle = f.GetValue(idleO) as string;
                        }
                        if (ciO != null)
                        {
                            var f = ciO.GetType().GetField("animationState");
                            if (f != null) combatIdle = f.GetValue(ciO) as string;
                        }
                    }
                }
                catch { }

                bool isIdleState = anim == idle || anim == combatIdle;
                branch = isIdleState
                    ? "IDLE-PERSISTENT (anim matches idle/combatIdle -> persistent bucket)"
                    : "QUEUED (waits for animator to ENTER '" + anim + "')";
                branch += "  [idle=" + (idle ?? "null") + " combatIdle=" + (combatIdle ?? "null") + "]";
            }

            InfinityLoaderMod.SafeLog("[PDIAG] Execute fx=" + fx + " anim=" + anim
                + " targets=" + targets + " branch=" + branch);
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[PDIAG] Execute trace failed: " + ex.Message); }
    }

    /// <summary>Postfix on NodeParticle.SpawnParticle: the object actually created (or null).</summary>
    public static void OnSpawn(string fx, GameObject __result)
    {
        try
        {
            if (!Interesting(fx)) return;
            if (__result == null)
            {
                InfinityLoaderMod.SafeLog("[PDIAG] SpawnParticle fx=" + fx
                    + " -> NULL (prefab missing from the class bundle, or target/mainPlayer was null)");
                return;
            }
            InfinityLoaderMod.SafeLog("[PDIAG] SpawnParticle fx=" + fx + " -> " + Describe(__result, 0));
            var runner = ParticleDiagRunner.Instance;
            if (runner != null) runner.Track(__result, fx);
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[PDIAG] Spawn trace failed: " + ex.Message); }
    }

    internal static string Describe(GameObject go, int frame)
    {
        try
        {
            if (go == null) return "frame=" + frame + " DESTROYED";
            var t = go.transform;
            int renderers = 0, active = 0, systems = 0, emitting = 0;
            var rs = go.GetComponentsInChildren<Renderer>(true);
            for (int i = 0; i < rs.Length; i++) { renderers++; if (rs[i].enabled && rs[i].gameObject.activeInHierarchy) active++; }
            var ps = go.GetComponentsInChildren<ParticleSystem>(true);
            for (int i = 0; i < ps.Length; i++) { systems++; if (ps[i].isEmitting) emitting++; }
            var anims = go.GetComponentsInChildren<Animator>(true);
            string animState = "-";
            if (anims.Length > 0 && anims[0] != null && anims[0].runtimeAnimatorController != null)
            {
                try
                {
                    var si = anims[0].GetCurrentAnimatorStateInfo(0);
                    animState = "spd=" + anims[0].speed.ToString("F2", CultureInfo.InvariantCulture)
                        + " t=" + si.normalizedTime.ToString("F2", CultureInfo.InvariantCulture);
                }
                catch { }
            }
            var kill = go.GetComponent<TimedKill>();
            return "frame=" + frame
                + " active=" + go.activeInHierarchy
                + " pos=" + t.position.x.ToString("F2", CultureInfo.InvariantCulture) + "," + t.position.y.ToString("F2", CultureInfo.InvariantCulture)
                + " scale=" + t.lossyScale.x.ToString("F3", CultureInfo.InvariantCulture) + "," + t.lossyScale.y.ToString("F3", CultureInfo.InvariantCulture)
                + " rend=" + active + "/" + renderers
                + " ps=" + emitting + "/" + systems
                + " anim[" + anims.Length + "]=" + animState
                + " timedKill=" + (kill == null ? "none" : kill.seconds.ToString("F1", CultureInfo.InvariantCulture) + "s");
        }
        catch (Exception ex) { return "frame=" + frame + " describe-failed: " + ex.Message; }
    }
}

/// <summary>
/// Drives the per-frame follow-up sampling. A spawned particle that "flashes" has
/// already changed by the time any single log line is written, so the only way to
/// see what happened is to re-read it on subsequent frames.
/// </summary>
public class ParticleDiagRunner : MonoBehaviour
{
    private static ParticleDiagRunner _instance;

    public static ParticleDiagRunner Instance
    {
        get
        {
            try
            {
                if (_instance == null)
                {
                    var go = new GameObject("InfinityParticleDiag");
                    UnityEngine.Object.DontDestroyOnLoad(go);
                    _instance = go.AddComponent<ParticleDiagRunner>();
                }
                return _instance;
            }
            catch { return null; }
        }
    }

    public void Track(GameObject go, string fx)
    {
        try { StartCoroutine(Sample(go, fx)); } catch { }
    }

    private IEnumerator Sample(GameObject go, string fx)
    {
        // Sample densely at first (the flash is over in a frame or two), then thin out.
        int[] marks = { 1, 2, 3, 5, 10, 20, 40, 60, 120, 180, 240 };
        int frame = 0;
        for (int i = 0; i < marks.Length; i++)
        {
            while (frame < marks[i]) { yield return null; frame++; }
            bool gone = (go == null);
            InfinityLoaderMod.SafeLog("[PDIAG]   " + fx + " " + ParticleDiagnostics.Describe(go, frame));
            if (gone)
            {
                InfinityLoaderMod.SafeLog("[PDIAG]   " + fx + " destroyed by frame " + frame + " -- stop sampling");
                yield break;
            }
        }
    }
}
