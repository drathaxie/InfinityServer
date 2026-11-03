using System;
using HarmonyLib;
using UnityEngine;

// Converted weapons keep their fixed RGB response atlases. The stock weapon loader
// does not recolor them; pass this character's palette without replacing materials
// or restarting any of the artwork's animation clocks.
internal static class FlashRigWeaponColors
{
    internal static void Install(Harmony harmony)
    {
        var load = AccessTools.DeclaredMethod(typeof(CustomizableCharacter), "LoadWeapon");
        var refresh = AccessTools.DeclaredMethod(typeof(CustomizableCharacter), "RefreshColors");
        if (load == null || refresh == null)
            throw new MissingMemberException("CustomizableCharacter weapon color contract changed");
        var postfix = new HarmonyMethod(typeof(FlashRigWeaponColors), nameof(Apply));
        harmony.Patch(load, postfix: postfix);
        harmony.Patch(refresh, postfix: postfix);
        var helm = AccessTools.DeclaredMethod(typeof(CustomizableCharacter), "setupHelm");
        if (helm == null) throw new MissingMemberException("CustomizableCharacter helm color contract changed");
        harmony.Patch(helm, postfix: postfix);
        var armor = AccessTools.DeclaredMethod(typeof(CustomizableCharacter), "LoadArmorSlots");
        if (armor == null) throw new MissingMemberException("CustomizableCharacter armor color contract changed");
        harmony.Patch(armor, postfix: postfix);
        var preview = AccessTools.DeclaredMethod(typeof(PreviewSpriteImage), "ShowGameObject", new[] { typeof(GameObject), typeof(bool) });
        if (preview == null || AccessTools.Field(typeof(PreviewSpriteImage), "GOPreview") == null)
            throw new MissingMemberException("Native weapon preview contract changed");
        harmony.Patch(preview, postfix: new HarmonyMethod(typeof(FlashRigWeaponColors), nameof(AfterShowPreview)));
    }

    private static void AfterShowPreview(GameObject ___GOPreview)
    {
        FlashRigMorph.Attach(___GOPreview);
        FlashBattlePet.Attach(___GOPreview);
    }

    internal static void Apply(CustomizableCharacter __instance)
    {
        if (__instance == null || __instance.ArmorColor == null) return;
        FlashRigMorph.Attach(__instance);
        foreach (var renderer in __instance.GetComponentsInChildren<SpriteRenderer>(true))
        {
            var material = renderer.sharedMaterial;
            if (material == null || material.shader == null || material.shader.name != "SwordShine/FlashRig" ||
                !material.HasProperty("_CCDirectPalette") || material.GetFloat("_CCDirectPalette") < 0.5f) continue;
            var block = new MaterialPropertyBlock();
            renderer.GetPropertyBlock(block);
            block.SetColor("_CCDirectBase", SourceColor(__instance, material, "Base", 5));
            block.SetColor("_CCDirectTrim", SourceColor(__instance, material, "Trim", 1));
            block.SetColor("_CCDirectAccessory", SourceColor(__instance, material, "Accessory", 2));
            if (material.HasProperty("_CCExtraPalette") && material.GetFloat("_CCExtraPalette") > 0.5f)
            {
                block.SetColor("_CCDirectSkin", __instance.ArmorColor.Skin);
                block.SetColor("_CCDirectHair", __instance.ArmorColor.Hair);
                block.SetColor("_CCDirectEye", __instance.ArmorColor.Eyes);
            }
            renderer.SetPropertyBlock(block);
        }
    }

    private static Color SourceColor(CustomizableCharacter character, Material material, string channel, int fallback)
    {
        string property = "_CCSource" + channel;
        int source = material.HasProperty(property) ? Mathf.RoundToInt(material.GetFloat(property)) : fallback;
        switch (source)
        {
            case 0: return character.ArmorColor.Base;
            case 1: return character.ArmorColor.Trim;
            case 2: return character.ArmorColor.Accessory;
            case 3: return character.ArmorColor.Skin;
            case 4: return character.ArmorColor.Hair;
            case 5: return character.ArmorColor.Eyes;
            default: throw new ArgumentOutOfRangeException(nameof(source), source, "Unknown FlashRig palette location");
        }
    }
}
