using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

// Exercises the same per-instance property block used by FlashRigWeaponColors.
// The shipped loader is compiled against the real client; this editor only
// validates its rendering operation, isolation, and preservation of animation.
public static class SpearPaletteValidation
{
    [Serializable] class Input { public string bundle, output; public int frame, fps; public Palette[] palettes; }
    [Serializable] class Palette { public string name; public float[] eye, trim, accessory, baseColor, skin, hair; }
    [Serializable] class Result { public string status; public int captures; public bool independentInstances, materialsPreserved, animationClocksPreserved, colorsSurviveAnimationUpdate; }

    public static void Run()
    {
        try
        {
            var args = Environment.GetCommandLineArgs();
            var input = JsonUtility.FromJson<Input>(File.ReadAllText(args[Array.IndexOf(args, "-paletteInput") + 1]));
            Directory.CreateDirectory(input.output);
            var bundle = AssetBundle.LoadFromFile(input.bundle);
            if (bundle == null) throw new Exception("Cannot load spear bundle");
            var prefab = bundle.LoadAsset<GameObject>(bundle.GetAllAssetNames().Single());
            var clone = UnityEngine.Object.Instantiate(prefab);
            var other = UnityEngine.Object.Instantiate(prefab);
            other.SetActive(false);
            var renderer = clone.GetComponentsInChildren<SpriteRenderer>(true).Single(r => r.sharedMaterial != null && r.sharedMaterial.shader.name == "SwordShine/FlashRig");
            var otherRenderer = other.GetComponentsInChildren<SpriteRenderer>(true).Single(r => r.sharedMaterial != null && r.sharedMaterial.shader.name == "SwordShine/FlashRig");
            renderer.transform.SetParent(null, false);
            renderer.transform.position = Vector3.zero;
            var animator = renderer.GetComponent<Animator>();
            if (renderer == null || animator == null || renderer.GetComponent<NoFlashWhenHit>() == null)
                throw new Exception("Missing native spear rendering contract");
            var material = renderer.sharedMaterial;
            if (otherRenderer.sharedMaterial != material || material.GetFloat("_CCDirectPalette") < 0.5f)
                throw new Exception("Expected shared direct-palette material");
            animator.Rebind(); animator.Update(0); animator.Update((float)input.frame / input.fps);
            float[] clocks = Enumerable.Range(0, animator.layerCount).Select(i => animator.GetCurrentAnimatorStateInfo(i).normalizedTime).ToArray();
            var cameraGO = new GameObject("PaletteValidationCamera");
            var camera = cameraGO.AddComponent<Camera>();
            camera.orthographic = true; camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.clear;
            int w = (int)renderer.sprite.rect.width, h = (int)renderer.sprite.rect.height;
            var target = new RenderTexture(w, h, 0, RenderTextureFormat.ARGB32);
            camera.targetTexture = target;
            camera.orthographicSize = h / renderer.sprite.pixelsPerUnit / 2f;
            var bounds = renderer.sprite.bounds;
            cameraGO.transform.position = new Vector3(bounds.center.x, bounds.center.y, -10);
            var check = new MaterialPropertyBlock();
            foreach (var palette in input.palettes)
            {
                var block = new MaterialPropertyBlock();
                renderer.GetPropertyBlock(block);
                block.SetColor("_CCDirectBase", SourceColor(palette, material, "Base", 5));
                block.SetColor("_CCDirectTrim", SourceColor(palette, material, "Trim", 1));
                block.SetColor("_CCDirectAccessory", SourceColor(palette, material, "Accessory", 2));
                if (material.HasProperty("_CCExtraPalette") && material.GetFloat("_CCExtraPalette") > 0.5f)
                {
                    block.SetColor("_CCDirectSkin", ColorOf(palette.skin));
                    block.SetColor("_CCDirectHair", ColorOf(palette.hair));
                    block.SetColor("_CCDirectEye", ColorOf(palette.eye));
                }
                renderer.SetPropertyBlock(block);
                otherRenderer.GetPropertyBlock(check);
                if (check.HasColor("_CCDirectBase")) throw new Exception("Palette leaked between instances");
                if (renderer.sharedMaterial != material) throw new Exception("Palette replaced animation material");
                for (int i = 0; i < clocks.Length; i++)
                    if (animator.GetCurrentAnimatorStateInfo(i).normalizedTime != clocks[i])
                        throw new Exception("Palette restarted an animation clock");
                camera.Render(); RenderTexture.active = target;
                var image = new Texture2D(w, h, TextureFormat.RGBA32, false);
                image.ReadPixels(new Rect(0, 0, w, h), 0, 0); image.Apply();
                RenderTexture.active = null;
                File.WriteAllBytes(Path.Combine(input.output, palette.name + ".png"), image.EncodeToPNG());
                UnityEngine.Object.DestroyImmediate(image);
            }
            animator.Update(1f / input.fps);
            renderer.GetPropertyBlock(check);
            var last = input.palettes.Last();
            if (check.GetColor("_CCDirectBase") != SourceColor(last, material, "Base", 5) ||
                check.GetColor("_CCDirectTrim") != SourceColor(last, material, "Trim", 1) ||
                check.GetColor("_CCDirectAccessory") != SourceColor(last, material, "Accessory", 2))
                throw new Exception("Animation update erased character palette");
            if (material.HasProperty("_CCExtraPalette") && material.GetFloat("_CCExtraPalette") > 0.5f &&
                (check.GetColor("_CCDirectSkin") != ColorOf(last.skin) ||
                 check.GetColor("_CCDirectHair") != ColorOf(last.hair) ||
                 check.GetColor("_CCDirectEye") != ColorOf(last.eye)))
                throw new Exception("Animation update erased extra character palette locations");
            var result = new Result { status = "passed", captures = input.palettes.Length,
                independentInstances = true, materialsPreserved = true, animationClocksPreserved = true, colorsSurviveAnimationUpdate = true };
            File.WriteAllText(Path.Combine(input.output, "runtime_contract.json"), JsonUtility.ToJson(result, true));
            camera.targetTexture = null; target.Release();
            UnityEngine.Object.DestroyImmediate(cameraGO); UnityEngine.Object.DestroyImmediate(renderer.gameObject); UnityEngine.Object.DestroyImmediate(clone); UnityEngine.Object.DestroyImmediate(other);
            bundle.Unload(true);
            Debug.Log("SPEAR_PALETTE_VALIDATION_SUCCESS"); EditorApplication.Exit(0);
        }
        catch (Exception e) { Debug.LogError("SPEAR_PALETTE_VALIDATION_FAILED " + e); EditorApplication.Exit(1); }
    }

    static Color ColorOf(float[] rgb) { return new Color(rgb[0] / 255f, rgb[1] / 255f, rgb[2] / 255f, 1); }
    static Color SourceColor(Palette palette, Material material, string channel, int fallback)
    {
        int source = material.HasProperty("_CCSource" + channel) ? Mathf.RoundToInt(material.GetFloat("_CCSource" + channel)) : fallback;
        var colors = new[] { palette.baseColor, palette.trim, palette.accessory, palette.skin, palette.hair, palette.eye };
        if (source < 0 || source >= colors.Length || colors[source] == null || colors[source].Length != 3)
            throw new Exception("Missing source palette location " + source);
        return ColorOf(colors[source]);
    }
}
