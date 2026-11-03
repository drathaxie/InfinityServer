using System;
using System.Collections.Generic;
using System.Text;
using Newtonsoft.Json;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

// Declarative metadata travels in the native AnimatorController's name. Slot
// reparenting retains it; bundles need no new MonoBehaviour serialization type.
// Only the selector changes. Animator layers and the character's bones keep time.
internal sealed class FlashRigMorph : MonoBehaviour
{
    internal const string Prefix = "FlashMorph:v1:";
    [Serializable] internal sealed class Polygon { public float[] xy = Array.Empty<float>(); }
    [Serializable] internal sealed class HitFrame { public Polygon[] polygons = Array.Empty<Polygon>(); }
    [Serializable] internal sealed class Program
    {
        public int frame_count = 0;
        public float fps = 0;
        public int[] stops = Array.Empty<int>();
        public bool initial_playing = false;
        public bool random_initial = false;
        public HitFrame[] hit_frames = Array.Empty<HitFrame>();
    }

    internal sealed class Clock
    {
        internal readonly Program Program;
        internal int Frame { get; private set; }
        internal bool Playing { get; private set; }
        private double accumulated;

        internal Clock(Program program)
        {
            if (program == null || program.frame_count < 2 || program.frame_count > 256 ||
                float.IsNaN(program.fps) || program.fps < 1 || program.fps > 120 || program.stops == null ||
                program.stops.Length == 0 || program.initial_playing != (Array.IndexOf(program.stops, 0) < 0) || program.hit_frames == null ||
                program.hit_frames.Length != program.frame_count)
                throw new ArgumentException("Invalid Flash morph program: frames=" + program?.frame_count +
                    ", fps=" + program?.fps + ", stops=" + (program?.stops == null ? -1 : program.stops.Length) +
                    ", hitFrames=" + (program?.hit_frames == null ? -1 : program.hit_frames.Length));
            foreach (int stop in program.stops)
                if (stop < 0 || stop >= program.frame_count) throw new ArgumentException("Invalid stop frame");
            foreach (var hit in program.hit_frames)
            {
                if (hit == null || hit.polygons == null) throw new ArgumentException("Missing hit frame");
                foreach (var poly in hit.polygons)
                {
                    if (poly == null || poly.xy == null || poly.xy.Length < 6 || poly.xy.Length % 2 != 0)
                        throw new ArgumentException("Invalid hit polygon");
                    foreach (float value in poly.xy)
                        if (float.IsNaN(value) || float.IsInfinity(value)) throw new ArgumentException("Invalid hit point");
                }
            }
            if (program.random_initial)
            {
                if (program.initial_playing || program.stops.Length != program.frame_count)
                    throw new ArgumentException("Random variant must remain stopped");
                for (int frame = 0; frame < program.frame_count; frame++)
                    if (Array.IndexOf(program.stops, frame) < 0 || program.hit_frames[frame].polygons.Length != 0)
                        throw new ArgumentException("Random variant cannot have click controls or moving parent frames");
                Frame = RoundedRandomFrame(program.frame_count, InitialRandom.NextDouble());
            }
            Program = program;
            Playing = program.initial_playing;
        }

        internal void Click() { Playing = true; }

        internal bool Advance(double seconds)
        {
            if (seconds < 0 || double.IsNaN(seconds) || double.IsInfinity(seconds))
                throw new ArgumentOutOfRangeException(nameof(seconds));
            // The source clock is global: clicking does not restart its frame phase.
            accumulated += seconds * Program.fps;
            int before = Frame;
            while (accumulated >= 1)
            {
                accumulated -= 1;
                if (!Playing) { accumulated %= 1; break; }
                Frame = (Frame + 1) % Program.frame_count;
                if (Array.IndexOf(Program.stops, Frame) >= 0) Playing = false;
            }
            return Frame != before;
        }

        internal bool Contains(Vector2 local)
        {
            foreach (var poly in Program.hit_frames[Frame].polygons)
            {
                bool inside = false;
                var points = poly.xy;
                for (int i = 0, j = points.Length - 2; i < points.Length; j = i, i += 2)
                {
                    float xi = points[i], yi = points[i + 1], xj = points[j], yj = points[j + 1];
                    if ((yi > local.y) != (yj > local.y) &&
                        local.x < (xj - xi) * (local.y - yi) / (yj - yi) + xi) inside = !inside;
                }
                if (inside) return true;
            }
            return false;
        }
    }

    private static readonly List<FlashRigMorph> Active = new List<FlashRigMorph>();
    private static readonly System.Random InitialRandom = new System.Random();
    private static readonly HashSet<string> RejectedControllers = new HashSet<string>();
    private static readonly List<RaycastResult> PointerHits = new List<RaycastResult>();
    private static int clickFrame = -1;
    private SpriteRenderer artwork;
    private Clock clock;
    private MaterialPropertyBlock block;

    // Player builds can omit Unity's native serialization metadata for types
    // loaded by Doorstop. Use the game's managed JSON reader for mod-owned DTOs.
    internal static Program ParseProgram(string json) => JsonConvert.DeserializeObject<Program>(json);

    // Flash: Math.round(Math.random() * (totalFrames - 1) + 1),
    // converted from its one-based frame number to our zero-based selector.
    internal static int RoundedRandomFrame(int count, double sample)
    {
        if (count < 2 || count > 256 || sample < 0 || sample >= 1 || double.IsNaN(sample))
            throw new ArgumentOutOfRangeException(nameof(sample));
        return (int)Math.Floor(sample * (count - 1) + .5);
    }

    internal static void Attach(CustomizableCharacter character)
    {
        if (character != null) Attach(character.gameObject);
    }

    internal static void Attach(GameObject root)
    {
        if (root == null) return;
        foreach (var animator in root.GetComponentsInChildren<Animator>(true))
        {
            var control = animator.runtimeAnimatorController;
            if (control == null || !control.name.StartsWith(Prefix, StringComparison.Ordinal) ||
                RejectedControllers.Contains(control.name) ||
                animator.GetComponent<FlashRigMorph>() != null) continue;
            var renderer = animator.GetComponent<SpriteRenderer>();
            if (renderer == null || renderer.sharedMaterial == null || renderer.sharedMaterial.shader == null ||
                renderer.sharedMaterial.shader.name != "SwordShine/FlashRig" ||
                !renderer.sharedMaterial.HasProperty("_MorphFrame")) continue;
            try
            {
                string json = Encoding.UTF8.GetString(Convert.FromBase64String(control.name.Substring(Prefix.Length)));
                var parsed = new Clock(ParseProgram(json));
                var morph = animator.gameObject.AddComponent<FlashRigMorph>();
                morph.artwork = renderer;
                morph.clock = parsed;
                if (morph.isActiveAndEnabled && !Active.Contains(morph)) Active.Add(morph);
                morph.ApplyFrame();
                Debug.Log("[InfinityLoader] Flash morph attached: " + animator.gameObject.name +
                    ", frames=" + parsed.Program.frame_count + ", stops=" + string.Join(",", parsed.Program.stops));
            }
            catch (Exception error)
            {
                if (RejectedControllers.Count >= 256) RejectedControllers.Clear();
                RejectedControllers.Add(control.name);
                Debug.LogError("[InfinityLoader] Invalid Flash morph: " + error.Message);
            }
        }
    }

    private void OnEnable() { if (!Active.Contains(this)) Active.Add(this); }
    private void OnDisable() { Active.Remove(this); }

    private void LateUpdate()
    {
        if (clock == null || artwork == null) return;
        Tick(Time.deltaTime);
        if (clickFrame == Time.frameCount || !Input.GetMouseButtonDown(0)) return;
        clickFrame = Time.frameCount;
        if (IsBlockedByUI(Input.mousePosition)) return;
        var camera = Camera.main;
        if (camera != null) TryClick(Input.mousePosition, camera);
    }

    internal int SelectedFrame => clock.Frame;
    internal void Tick(double seconds)
    {
        if (clock.Advance(seconds)) ApplyFrame();
    }

    internal static bool IsBlockedByUI(Vector2 screen)
    {
        var events = EventSystem.current;
        if (events == null) return false;
        PointerHits.Clear();
        events.RaycastAll(new PointerEventData(events) { position = screen }, PointerHits);
        // Physics raycasters also report the equipped character as a pointer
        // target. They must not block the source button on that character.
        foreach (var hit in PointerHits)
            if (hit.module is GraphicRaycaster) return true;
        return false;
    }

    internal static bool TryClick(Vector2 screen, Camera camera)
    {
        FlashRigMorph selected = null;
        var ray = camera.ScreenPointToRay(screen);
        foreach (var candidate in Active)
        {
            var renderer = candidate.artwork;
            if (candidate.clock == null || renderer == null || !renderer.enabled ||
                !renderer.gameObject.activeInHierarchy || (camera.cullingMask & (1 << renderer.gameObject.layer)) == 0)
                continue;
            var plane = new Plane(renderer.transform.forward, renderer.transform.position);
            if (!plane.Raycast(ray, out float distance)) continue;
            var point = renderer.transform.InverseTransformPoint(ray.GetPoint(distance));
            if (renderer.flipX) point.x = -point.x;
            if (renderer.flipY) point.y = -point.y;
            if (!candidate.clock.Contains(point)) continue;
            if (selected == null || Above(renderer, selected.artwork)) selected = candidate;
        }
        if (selected == null) return false;
        selected.clock.Click();
        return true;
    }

    private static bool Above(SpriteRenderer a, SpriteRenderer b)
    {
        int aLayer = SortingLayer.GetLayerValueFromID(a.sortingLayerID);
        int bLayer = SortingLayer.GetLayerValueFromID(b.sortingLayerID);
        return aLayer != bLayer ? aLayer > bLayer : a.sortingOrder > b.sortingOrder;
    }

    private void ApplyFrame()
    {
        if (block == null) block = new MaterialPropertyBlock();
        artwork.GetPropertyBlock(block);
        block.SetFloat("_MorphFrame", clock.Frame);
        artwork.SetPropertyBlock(block);
    }
}
