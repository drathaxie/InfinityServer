using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using UnityEngine;

/// <summary>
/// Mirrors server-owned time-control auras on monster Animators. Packet ingestion runs on the
/// networking thread and stores plain data only; Tick is called by NameplateTicker on Unity's
/// main thread, where entity lookup and Animator mutation are safe.
/// </summary>
public static class TemporalMonsterEffects
{
    private sealed class Effect
    {
        public string Target;
        public string Aura;
        public float Speed;
        public DateTime EndsUtc;
    }

    private static readonly object Sync = new object();
    private static readonly Dictionary<string, Effect> Effects =
        new Dictionary<string, Effect>(StringComparer.Ordinal);
    private static readonly Dictionary<Animator, float> Originals =
        new Dictionary<Animator, float>();
    private static readonly Dictionary<string, HashSet<Animator>> Applied =
        new Dictionary<string, HashSet<Animator>>(StringComparer.Ordinal);
    private static volatile bool clearRequested;

    private static bool IsTemporal(string aura)
    {
        return aura == "Time Dilation" || aura == "Temporal Stasis";
    }

    private static string Key(string target, string aura) { return target + "\n" + aura; }

    public static void IngestPacket(string raw)
    {
        if (string.IsNullOrEmpty(raw)) return;
        if (raw.IndexOf("Time Dilation", StringComparison.Ordinal) < 0 &&
            raw.IndexOf("Temporal Stasis", StringComparison.Ordinal) < 0 &&
            raw.IndexOf("\"Cmd\":\"AreaJoin\"", StringComparison.Ordinal) < 0 &&
            raw.IndexOf("\"Cmd\": \"AreaJoin\"", StringComparison.Ordinal) < 0) return;
        JObject pkt = JObject.Parse(raw);
        string cmd = (string)pkt["Cmd"];
        if (cmd == "AreaJoin")
        {
            lock (Sync) Effects.Clear();
            clearRequested = true;
            return;
        }
        if (cmd == "AuraChange" && (int?)pkt["auraCmd"] == 1)
        {
            string aura = (string)pkt["nam"];
            string target = (string)pkt["Target"];
            if (IsTemporal(aura) && !string.IsNullOrEmpty(target))
                lock (Sync) Effects.Remove(Key(target, aura));
            return;
        }
        if (cmd != "Attack") return;
        JArray nodes = pkt["Nodes"] as JArray;
        if (nodes == null) return;
        foreach (JToken node in nodes)
        {
            if ((string)node["Name"] != "Aura") continue;
            string aura = (string)node["AuraName"];
            if (!IsTemporal(aura)) continue;
            float duration = (float?)node["Duration"] ?? (aura == "Temporal Stasis" ? 2.5f : 6f);
            float speed = Mathf.Clamp01((float?)node["AnimationSpeed"] ??
                                        (aura == "Temporal Stasis" ? 0f : 0.35f));
            JArray targets = node["Targets"] as JArray;
            if (targets == null) continue;
            foreach (JToken tok in targets)
            {
                string target = (string)tok;
                if (string.IsNullOrEmpty(target) || !target.StartsWith("m:", StringComparison.Ordinal))
                    continue;
                var effect = new Effect { Target = target, Aura = aura, Speed = speed,
                    EndsUtc = DateTime.UtcNow.AddSeconds(Math.Max(0.05f, duration)) };
                lock (Sync) Effects[Key(target, aura)] = effect;
            }
        }
    }

    public static void Tick()
    {
        if (clearRequested)
        {
            clearRequested = false;
            RestoreAll();
        }
        var desired = new Dictionary<string, float>(StringComparer.Ordinal);
        DateTime now = DateTime.UtcNow;
        lock (Sync)
        {
            var expired = new List<string>();
            foreach (var pair in Effects)
            {
                Effect fx = pair.Value;
                if (now >= fx.EndsUtc) { expired.Add(pair.Key); continue; }
                float current;
                if (!desired.TryGetValue(fx.Target, out current) || fx.Speed < current)
                    desired[fx.Target] = fx.Speed;
            }
            foreach (string key in expired) Effects.Remove(key);
        }

        foreach (string target in new List<string>(Applied.Keys))
            if (!desired.ContainsKey(target)) RestoreTarget(target);

        if (Area.currentArea == null) return;
        foreach (var pair in desired)
        {
            Entity entity = Area.currentArea.GetEntityByTargetString(pair.Key);
            GameObject go = entity == null ? null : entity.getGameObject();
            if (go == null) continue;
            Animator[] animators = go.GetComponentsInChildren<Animator>(true);
            HashSet<Animator> owned;
            if (!Applied.TryGetValue(pair.Key, out owned))
            {
                owned = new HashSet<Animator>();
                Applied[pair.Key] = owned;
            }
            foreach (Animator animator in animators)
            {
                if (animator == null) continue;
                if (!Originals.ContainsKey(animator)) Originals[animator] = animator.speed;
                owned.Add(animator);
                animator.speed = pair.Value;
                if (pair.Value <= 0f) animator.Update(0f);
            }
        }
    }

    private static void RestoreTarget(string target)
    {
        HashSet<Animator> owned;
        if (!Applied.TryGetValue(target, out owned)) return;
        foreach (Animator animator in owned)
        {
            if (animator == null) continue;
            float speed;
            if (Originals.TryGetValue(animator, out speed)) animator.speed = speed;
            Originals.Remove(animator);
        }
        Applied.Remove(target);
    }

    private static void RestoreAll()
    {
        foreach (string target in new List<string>(Applied.Keys)) RestoreTarget(target);
        Originals.Clear();
    }
}
