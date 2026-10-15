using System.Reflection;
using UnityEngine;

static class Program
{
    static int checks;
    static void Check(bool condition, string name)
    { if (!condition) throw new Exception(name); checks++; }
    static void Tick(FlashRigPreviewColors driver) => typeof(FlashRigPreviewColors)
        .GetMethod("Update", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(driver, null);
    static void Dispose(FlashRigPreviewColors driver) => typeof(FlashRigPreviewColors)
        .GetMethod("OnDestroy", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(driver, null);

    static void Main()
    {
        var go = new GameObject();
        var c = go.AddComponent<CustomizableCharacter>();
        c.SkipComposite = true;
        int callbacks = 0;
        c.OnSetupComplete = () => callbacks++;
        var source = new Texture2D(64, 64, TextureFormat.ARGB32, false, false);
        var mask = new Texture2D(4, 4, TextureFormat.ARGB32, false, false);
        var entries = new GameObject().AddComponent<ItemSlots>();
        var bones = new List<Transform>();
        var material = new Material { shader = new Shader { name = "SwordShine/FlashRig" } };
        foreach (var slotId in Enum.GetValues<CustomizableCharacter.Slot>())
        {
            var bone = new GameObject();
            bones.Add(bone.AddComponent<Transform>());
            bone.AddComponent<CustomizableSlot>().slot = slotId;
            var sr = bone.AddComponent<SpriteRenderer>();
            sr.sharedMaterial = material;
            sr.sprite = Sprite.Create(source, new Rect { size = new Vector2 { x = 24, y = 24 } },
                new Vector2 { x = .5f, y = .5f }, 100, 0, SpriteMeshType.Tight);
            entries.slots.Add(new SpriteSlot { slot = slotId, sprite = sr.sprite, CCMask = mask });
        }
        // Additional atlas regions precede each displayed base; final sprite entry must win.
        var finalBase = Sprite.Create(source, new Rect { size = new Vector2 { x = 32, y = 32 } },
            new Vector2 { x = .5f, y = .5f }, 100, 0, SpriteMeshType.Tight);
        entries.slots.Add(new SpriteSlot { slot = CustomizableCharacter.Slot.chest, sprite = finalBase });
        Check(!FlashRigPreviewColors.BeforeSetup(c, entries, bones), "rig preview bypasses static material replacement");
        var driver = c.GetComponent<FlashRigPreviewColors>();
        Check(c.SkipComposite, "preview's native flag retained");
        Check(callbacks == 0, "setup completion waits for compositor");
        Tick(driver);
        var first = CompositorRequest.pending.Dequeue();
        Check(first.tasks.Count == 3, "whole atlas including animated regions is composited");
        first.Finish();
        var front = bones[0].GetComponent<SpriteRenderer>();
        Check(callbacks == 1, "initial completion exactly once");
        Check(ReferenceEquals(front.sharedMaterial, material), "animated material retained");
        Check(front.sprite.rect.size.x == 32, "last slot entry is displayed base");
        Check(front.sprite.mesh == SpriteMeshType.FullRect, "full animation footprint retained");
        Check(bones[1].GetComponent<SpriteRenderer>().sprite.texture == front.sprite.texture, "back and front share recolored atlas");
        var oldAtlas = front.sprite.texture;
        var oldSprite = front.sprite;
        c.ArmorColor.Base = 11;
        FlashRigPreviewColors.AfterRefresh(c);
        Tick(driver);
        var stale = CompositorRequest.pending.Dequeue();
        c.ArmorColor.Base = 22;
        FlashRigPreviewColors.AfterRefresh(c);
        c.ArmorColor.Base = 33;
        FlashRigPreviewColors.AfterRefresh(c);
        Tick(driver);
        Check(CompositorRequest.pending.Count == 0, "rapid recolors coalesce while queued");
        Check(!stale.target.destroyed, "queued texture stays alive");
        stale.Finish();
        Check(stale.target.destroyed, "superseded output discarded");
        Check(front.sprite.texture == oldAtlas, "stale color never replaces displayed texture");
        Tick(driver);
        var latest = CompositorRequest.pending.Dequeue();
        Check(latest.tasks.OfType<CompositorTaskArmorColorMask>().All(t => t.color == 33), "latest palette captured");
        latest.Finish();
        Check(oldAtlas.destroyed && oldSprite.destroyed, "previous generated resources released");
        Check(!source.destroyed && !mask.destroyed && !finalBase.destroyed, "bundle resources preserved");
        Check(callbacks == 1, "recolor does not repeat setup callback or restart animation");

        FlashRigPreviewColors.AfterRefresh(c);
        Tick(driver);
        var switching = CompositorRequest.pending.Dequeue();
        var nativeBone = new GameObject();
        nativeBone.AddComponent<SpriteRenderer>().sharedMaterial = new Material { shader = new Shader { name = "Sprites/Default" } };
        Check(FlashRigPreviewColors.BeforeSetup(c, entries, new List<Transform> { nativeBone.AddComponent<Transform>() }), "native armor uses original path");
        switching.Finish();
        Check(switching.target.destroyed, "armor switch discards outstanding rig composite");
        Tick(driver);
        Check(CompositorRequest.pending.Count == 0, "native armor receives no stale recolor");
        Check(!FlashRigPreviewColors.BeforeSetup(c, entries, bones), "rig can be reequipped");
        Tick(driver);
        var closing = CompositorRequest.pending.Dequeue();
        Dispose(driver);
        Check(!closing.target.destroyed, "closing preview leaves queued target alive until completion");
        closing.Finish();
        Check(closing.target.destroyed, "closing preview releases queued output after completion");
        c.SkipComposite = false;
        Check(FlashRigPreviewColors.BeforeSetup(c, entries, bones), "gameplay keeps native compositor path");
        Check(FlashRigPreviewColors.BeforeSetup(c, null, null), "missing armor keeps native behavior");
        Console.WriteLine($"PASS: {checks} lifecycle checks (contract doubles; GPU/client visuals separate).");
    }
}
