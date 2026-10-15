using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

// Preview screens normally replace each CC renderer's material with a static mask shader.
// FlashRig needs its entire atlas recolored: its animated layers sample different regions.
// Use the native compositor for these armors and retain the existing materials/Animators.
internal sealed class FlashRigPreviewColors : MonoBehaviour
{
    private CustomizableCharacter character;
    private ItemSlots slots;
    private Dictionary<CustomizableCharacter.Slot, SpriteRenderer> renderers;
    private readonly List<Sprite> generated = new List<Sprite>();
    private Texture2D atlas;
    private Action ready;
    private bool busy, dirty, disposed;
    private int revision;
    private static MethodInfo clearNativeMaterials;

    internal static void Install(Harmony harmony)
    {
        var setup = AccessTools.DeclaredMethod(typeof(CustomizableCharacter), "setupArmor");
        var refresh = AccessTools.DeclaredMethod(typeof(CustomizableCharacter), "RefreshColors");
        clearNativeMaterials = AccessTools.DeclaredMethod(typeof(CustomizableCharacter), "ClearTrackedMaterials");
        if (setup == null || refresh == null || clearNativeMaterials == null ||
            AccessTools.Field(typeof(CustomizableCharacter), "loadedArmorGOs") == null)
            throw new MissingMemberException("CustomizableCharacter preview compositor contract changed");
        harmony.Patch(setup, prefix: new HarmonyMethod(typeof(FlashRigPreviewColors), nameof(BeforeSetup)));
        harmony.Patch(refresh, postfix: new HarmonyMethod(typeof(FlashRigPreviewColors), nameof(AfterRefresh)));
    }

    internal static bool BeforeSetup(CustomizableCharacter __instance, ItemSlots a,
                                    List<Transform> ___loadedArmorGOs)
    {
        var driver = __instance.GetComponent<FlashRigPreviewColors>();
        bool hasRig = false;
        if (__instance.SkipComposite && a != null && ___loadedArmorGOs != null)
            foreach (var t in ___loadedArmorGOs)
            {
                if (t == null) continue;
                var sr = t.GetComponent<SpriteRenderer>();
                if (IsRig(sr)) { hasRig = true; break; }
            }
        if (!hasRig)
        {
            if (driver != null) driver.ResetArmor();
            return true;
        }
        if (driver == null) driver = __instance.gameObject.AddComponent<FlashRigPreviewColors>();
        // Match setupArmor's cancellation of any older native armor setup.
        __instance.StopAllCoroutines();
        clearNativeMaterials?.Invoke(__instance, null);
        driver.character = __instance;
        driver.slots = a;
        driver.renderers = new Dictionary<CustomizableCharacter.Slot, SpriteRenderer>();
        foreach (var t in ___loadedArmorGOs)
        {
            if (t == null) continue;
            var slot = t.GetComponent<CustomizableSlot>();
            if (slot != null && slot.spriteRenderer != null)
                driver.renderers[slot.slot] = slot.spriteRenderer;
        }
        driver.ready = __instance.OnSetupComplete;
        driver.RequestRefresh();
        return false;
    }

    internal static void AfterRefresh(CustomizableCharacter __instance)
    {
        var driver = __instance.GetComponent<FlashRigPreviewColors>();
        if (driver != null && __instance.SkipComposite && driver.slots != null)
            driver.RequestRefresh();
    }

    private static bool IsRig(SpriteRenderer sr)
    {
        return sr != null && sr.sharedMaterial != null && sr.sharedMaterial.shader != null &&
               sr.sharedMaterial.shader.name == "SwordShine/FlashRig";
    }

    private void RequestRefresh()
    {
        revision++;
        dirty = true;
        // Some screens hide the preview until OnSetupComplete. Submission and
        // queued completion must work even when this GameObject cannot Update.
        Update();
    }

    private void Update()
    {
        if (!dirty || busy || disposed || character == null || slots == null) return;
        dirty = false;
        if (slots.slots.Count == 0 || slots.slots[0].sprite == null) return;
        int jobRevision = revision;
        var source = slots.slots[0].sprite.texture;
        var output = new Texture2D(source.width, source.height, TextureFormat.ARGB32, true, false);
        var request = new CompositorRequest(output);
        var masks = new HashSet<Texture2D>();
        source.filterMode = FilterMode.Point;
        foreach (var entry in slots.slots)
        {
            if (entry.sprite == null) continue;
            if (entry.CCMask != null)
            {
                entry.CCMask.filterMode = FilterMode.Point;
                masks.Add(entry.CCMask);
                request.tasks.Add(new CompositorTaskArmorColorMask(entry.sprite.textureRect,
                    entry.CCMask, entry.sprite.texture, character.ArmorColor));
            }
            else request.tasks.Add(new CompositorTaskSprite(entry.sprite.textureRect, source));
        }
        // A queued compositor job owns its target until Complete, even if this preview is
        // destroyed or another armor/color is selected. Never free a target still in use.
        busy = true;
        request.Complete += delegate
        {
            if (source != null) source.filterMode = FilterMode.Bilinear;
            foreach (var mask in masks) if (mask != null) mask.filterMode = FilterMode.Bilinear;
            busy = false;
            if (this == null || disposed || character == null || slots == null || jobRevision != revision)
            {
                Destroy(output);
                if (this != null && !disposed) Update();
                return;
            }
            var next = new List<Sprite>();
            try
            {
                var assignments = new Dictionary<SpriteRenderer, Sprite>();
                foreach (var entry in slots.slots)
                {
                    SpriteRenderer sr;
                    if (entry.sprite == null || !renderers.TryGetValue(entry.slot, out sr) || sr == null) continue;
                    var original = entry.sprite;
                    // Keep the full animation canvas even where frame zero is transparent.
                    var sprite = Sprite.Create(output, original.rect, original.pivot / original.rect.size,
                        original.pixelsPerUnit, 0, IsRig(sr) ? SpriteMeshType.FullRect : SpriteMeshType.Tight);
                    next.Add(sprite);
                    assignments[sr] = sprite;
                }
                foreach (var assignment in assignments) assignment.Key.sprite = assignment.Value;
                ReleaseOutput();
                atlas = output;
                generated.AddRange(next);
                var onReady = ready;
                ready = null;
                onReady?.Invoke();
            }
            catch (Exception ex)
            {
                if (atlas != output)
                {
                    foreach (var sprite in next) if (sprite != null) Destroy(sprite);
                    Destroy(output);
                }
                InfinityLoaderMod.SafeLog("[flash-rig] preview composite failed: " + ex);
            }
        };
        request.Composite();
    }

    private void ReleaseOutput()
    {
        foreach (var sprite in generated) if (sprite != null) Destroy(sprite);
        generated.Clear();
        if (atlas != null) Destroy(atlas);
        atlas = null;
    }

    private void ResetArmor()
    {
        revision++;
        dirty = false;
        slots = null;
        renderers = null;
        ready = null;
        ReleaseOutput();
    }

    private void OnDestroy()
    {
        disposed = true;
        ResetArmor();
    }
}
