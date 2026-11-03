using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

// Batch builder for converted Flash armors (scripts/swf2unity/armor.py -> build_armor.py -> here).
//   Unity.exe -batchmode -quit -projectPath <proj> -executeMethod ArmorBuilder.Build -armorInput <unity_input.json>
// Builds ArmorBones_m / ArmorBones_f prefabs that mirror a native AQW:I armor prefab, the FlashRig materials,
// clips and Animator controllers for animated parts, one AssetBundle, and (optionally) validation renders.
public static class ArmorBuilder
{
    [Serializable] class Input { public string name; public string folder; public string bundleName; public string outputDir; public int fps; public Gender[] genders; public int[] validateFrames; public string existingBundle; public bool expectMissingHelmGO; public long textureBudgetBytes; }
    [Serializable] class Gender { public string g; public string atlas; public int atlasW; public int atlasH; public SpriteDef[] sprites; public SlotEntry[] itemslots; public RendererDef[] renderers; public Node[] nodes; public string destPalette; public Reconstruction reconstruction; public string prefabName; public string atlasFormat; public string meshType; public bool noItemSlots; public bool petLoop; public bool sourceFace; }
    [Serializable] class CoeffTexture { public string path; public float[] decodeScale; public float[] decodeBias; public string format; }
    [Serializable] class Probe { public string location; public float[] rect; public float gain; public float bias; }
    [Serializable] class Reconstruction { public bool enabled; public string model; public float knot; public CoeffTexture constant; public CoeffTexture baseSlope; public CoeffTexture trimSlope; public CoeffTexture accessorySlope; public CoeffTexture baseHighSlope; public CoeffTexture trimHighSlope; public CoeffTexture accessoryHighSlope; public CoeffTexture skinSlope, hairSlope, eyeSlope, skinHighSlope, hairHighSlope, eyeHighSlope; public Probe[] probes; public bool directPalette, extraPalette; public float[] paletteBase, paletteTrim, paletteAccessory, paletteSkin, paletteHair, paletteEye; public int[] paletteSources; public string coefficientFormat; }
    [Serializable] class GenderContract { public string gender, prefabName; public int renderers, itemSlots, customizableSlots, flashRigRenderers, noFlashMarkers, animators; public string[] componentTypes, runtimeScriptClasses, materialShaders; }
    [Serializable] class TextureContract { public string name, format; public int width, height, mipCount; public long textureBytes; public bool readable; }
    [Serializable] class PoseCheck { public string gender, slot, phase; public int frame, scalars; public float maxTranslationError, maxOtherError, maxAffinePixelError; public bool spatialAffineCheck; }
    [Serializable] class ClipCheck { public string clip; public int period, curves, scalarKeys; public float maxKeyError; }
    [Serializable] class PetMovementCheck { public string status, prefabName; public int switches, visualLayers; public bool followerFindsRoot, visualClocksPreserved; }
    [Serializable] class BundleContract { public string status, unityVersion, bundleName; public uint bundleCRC; public string[] dependencies, assetNames; public GenderContract[] genders; public TextureContract[] textures; public long textureBytes; public ClipCheck[] clipChecks; public PoseCheck[] animatorPoseChecks; public string hitFlashBehavior; public int helmAttachmentContract; }
    [Serializable] class SpriteDef { public string name; public float x, y, w, h, px, py, ppu; public string mask; }
    [Serializable] class SlotEntry { public int slot; public string sprite; }
    [Serializable] class RendererDef { public string slot; public string sprite; public bool layered; public float[] tint; public LayerDef[] layers; public string morphJson; }
    [Serializable] class LayerDef { public float[] rect; public int period; public float[] keys; public float blend; public bool stepped; public bool steppedRect; public bool groupStart; public float groupEnd = -1; public int morphFrame = -1; }
    [Serializable] class Node { public string name; public int parent; public bool active; public float[] pos; public float[] rot; public float[] scale; public int slot = -1; public bool hasRenderer; public int order; public int sortingLayer; public bool rendererEnabled; public float[] color; }
    [Serializable] class MorphProgram { public int frame_count = 0; }

    const int KeyWidth = 18; // A.xyzw, T.xy, CM.xyzw, CA.xyzw, Rect.xyzw (flipbooks switch rects)
    const int MaxLayers = 128; // FlashRig.shader (gen_flashrig.py)
    const float StepEpsilon = 1e-4f; // stepped (flipbook) keys are sampled just after their key time
    static readonly int[][] VectorGroups = { new[] { 0, 4 }, new[] { 4, 6 }, new[] { 6, 10 }, new[] { 10, 14 }, new[] { 14, 18 } };
    static readonly string[] Comp = { "_L{0}A.x", "_L{0}A.y", "_L{0}A.z", "_L{0}A.w", "_L{0}T.x", "_L{0}T.y",
        "_L{0}CM.x", "_L{0}CM.y", "_L{0}CM.z", "_L{0}CM.w", "_L{0}CA.x", "_L{0}CA.y", "_L{0}CA.z", "_L{0}CA.w",
        "_L{0}Rect.x", "_L{0}Rect.y", "_L{0}Rect.z", "_L{0}Rect.w" };
    static readonly List<PoseCheck> poseChecks = new List<PoseCheck>();
    static readonly List<ClipCheck> clipChecks = new List<ClipCheck>();
    [Serializable] class BatchInput { public string[] inputs; }

    public static void Build()
    {
        try
        {
            var input = JsonUtility.FromJson<Input>(File.ReadAllText(Arg("-armorInput")));
            BuildOne(input);
            EditorApplication.Exit(0);
        }
        catch (Exception e)
        {
            Debug.LogError("ARMOR_BUILD_FAILED " + e);
            EditorApplication.Exit(1);
        }
    }

    public static void BuildMany()
    {
        int failed = 0;
        var batch = JsonUtility.FromJson<BatchInput>(File.ReadAllText(Arg("-armorBatchInput")));
        var shaderPath = "Assets/Shaders/FlashRig.shader";
        var priorShader = AssetDatabase.LoadAssetAtPath<Shader>(shaderPath);
        if (priorShader != null && ShaderUtil.GetShaderMessages(priorShader).Any(message =>
                message.severity == UnityEditor.Rendering.ShaderCompilerMessageSeverity.Error))
        {
            Debug.Log("FLASHRIG_RETRY previous cached shader compilation failed");
            AssetDatabase.ImportAsset(shaderPath, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
        }
        foreach (string path in batch.inputs)
        {
            var input = JsonUtility.FromJson<Input>(File.ReadAllText(path));
            try { BuildOne(input); }
            catch (Exception e)
            {
                failed++;
                Directory.CreateDirectory(input.outputDir);
                File.WriteAllText(Path.Combine(input.outputDir, "build_failure.txt"), e.ToString());
                Debug.LogError("ARMOR_BUILD_FAILED " + input.bundleName + " " + e);
            }
            finally
            {
                // A pose/render exception can leave its preview clone and camera alive.
                // Destroy the entire batch-owned scene before the next capture.
                RenderTexture.active = null;
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                AssetBundle.UnloadAllAssetBundles(true);
                EditorUtility.UnloadUnusedAssetsImmediate();
                GC.Collect();
            }
        }
        Debug.Log("ARMOR_BATCH_FINISHED failures=" + failed);
        EditorApplication.Exit(failed == 0 ? 0 : 1);
    }

    public static void ValidateExisting()
    {
        int exitCode = 0;
        var input = JsonUtility.FromJson<Input>(File.ReadAllText(Arg("-armorInput")));
        Directory.CreateDirectory(input.outputDir);
        try
        {
            poseChecks.Clear();
            var bundle = AssetBundle.LoadFromFile(Arg("-existingBundle"));
            if (bundle == null) throw new Exception("Existing bundle could not be loaded");
            var report = new List<string>();
            foreach (var gender in input.genders)
            {
                var prefab = bundle.LoadAsset<GameObject>(PrefabName(gender));
                if (prefab == null) throw new Exception("Existing prefab could not be loaded");
                Validate(input, gender, input.folder, prefab, report);
            }
            File.WriteAllLines(Path.Combine(input.outputDir, "existing_validation_passed.txt"), report);
        }
        catch (Exception error)
        {
            exitCode = 1;
            File.WriteAllText(Path.Combine(input.outputDir, "existing_validation_failure.txt"), error.ToString());
            Debug.LogError("EXISTING_VALIDATION_FAILED " + error);
        }
        finally
        {
            RenderTexture.active = null;
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            AssetBundle.UnloadAllAssetBundles(true);
            EditorUtility.UnloadUnusedAssetsImmediate();
        }
        EditorApplication.Exit(exitCode);
    }

    public static void ValidateHelmMany()
    {
        int failed = 0;
        var batch = JsonUtility.FromJson<BatchInput>(File.ReadAllText(Arg("-armorBatchInput")));
        foreach (var path in batch.inputs)
        {
            var input = JsonUtility.FromJson<Input>(File.ReadAllText(path));
            try
            {
                poseChecks.Clear();
                PlayerSettings.colorSpace = ColorSpace.Gamma; QualitySettings.antiAliasing = 0;
                var bundle = AssetBundle.LoadFromFile(input.existingBundle);
                if (bundle == null) throw new Exception("Helm bundle cannot load");
                var canonical = bundle.LoadAsset<GameObject>("HelmGO");
                if (input.expectMissingHelmGO)
                {
                    if (canonical != null) throw new Exception("Negative control unexpectedly contains HelmGO");
                    File.WriteAllText(Path.Combine(input.outputDir,"negative_control.json"), "{\"status\":\"passed\",\"missing_native_prefab_reproduced\":true}");
                    continue;
                }
                var report = new List<string>();
                foreach (var g in input.genders)
                {
                    if (!IsHelm(g) || bundle.LoadAsset<GameObject>(PrefabName(g)) == null)
                        throw new Exception("Metadata preview prefab missing");
                    var attached = NativeHelmContract.Attach(canonical);
                    UnityEngine.Object.DestroyImmediate(attached);
                    Validate(input,g,input.folder,canonical,report);
                }
                var contractPath = Path.Combine(input.outputDir,"bundle_contract.json");
                var contract = JsonUtility.FromJson<BundleContract>(File.ReadAllText(contractPath));
                if (!BuildPipeline.GetCRCForAssetBundle(input.existingBundle,out contract.bundleCRC))
                    throw new Exception("Cannot obtain repaired bundle CRC");
                contract.assetNames = bundle.GetAllAssetNames();
                contract.animatorPoseChecks = poseChecks.ToArray(); contract.helmAttachmentContract = 2;
                contract.status = "passed";
                File.WriteAllText(contractPath,JsonUtility.ToJson(contract,true));
                File.WriteAllLines(Path.Combine(input.outputDir,"native_helm_validation_passed.txt"),report);
                File.Delete(Path.Combine(input.outputDir,"build_failure.txt"));
                Debug.Log("NATIVE_HELM_VALIDATION_SUCCESS " + input.bundleName);
            }
            catch (Exception error)
            {
                failed++; File.WriteAllText(Path.Combine(input.outputDir,"build_failure.txt"),error.ToString());
                Debug.LogError("NATIVE_HELM_VALIDATION_FAILED " + input.bundleName + " " + error);
            }
            finally
            {
                RenderTexture.active = null;
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                AssetBundle.UnloadAllAssetBundles(true); EditorUtility.UnloadUnusedAssetsImmediate(); GC.Collect();
            }
        }
        EditorApplication.Exit(failed==0 ? 0 : 1);
    }

    static void BuildOne(Input input)
    {
            poseChecks.Clear();
            clipChecks.Clear();
            PlayerSettings.colorSpace = ColorSpace.Gamma;
            QualitySettings.antiAliasing = 0;
            string root = input.folder;
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            var prefabs = new List<string>();
            var report = new List<string>();
            foreach (var g in input.genders)
            {
                prefabs.Add(BuildGender(input, g, root, report));
                if (IsHelm(g) && PrefabName(g) != "HelmGO") prefabs.Add(root + "/HelmGO.prefab");
            }
            Directory.CreateDirectory(input.outputDir);
            var map = new AssetBundleBuild { assetBundleName = input.bundleName, assetNames = prefabs.ToArray() };
            var manifest = BuildPipeline.BuildAssetBundles(input.outputDir, new[] { map },
                BuildAssetBundleOptions.ChunkBasedCompression | BuildAssetBundleOptions.ForceRebuildAssetBundle,
                BuildTarget.StandaloneWindows64);
            if (manifest == null) throw new Exception("BuildAssetBundles returned null");
            string[] dependencies = manifest.GetAllDependencies(input.bundleName);
            if (dependencies.Length != 0) throw new Exception("Armor bundle unexpectedly has external dependencies");
            var built = AssetBundle.LoadFromFile(Path.Combine(input.outputDir, input.bundleName));
            if (built == null) throw new Exception("Emitted armor bundle could not be loaded");
            var contracts = new List<GenderContract>();
            var loadedPrefabs = new List<GameObject>();
            foreach (var g in input.genders)
            {
                string prefabName = PrefabName(g);
                var prefab = built.LoadAsset<GameObject>(prefabName);
                if (prefab == null) throw new Exception("Emitted bundle cannot resolve " + prefabName);
                loadedPrefabs.Add(prefab);
                contracts.Add(InspectContract(g.g, prefab, g.sourceFace));
                if (IsHelm(g))
                {
                    var canonical = built.LoadAsset<GameObject>("HelmGO");
                    var attached = NativeHelmContract.Attach(canonical);
                    UnityEngine.Object.DestroyImmediate(attached);
                    prefab = canonical;
                }
                if (g.petLoop) ValidatePetMovement(input, prefab);
                if (input.validateFrames != null && input.validateFrames.Length > 0 && !string.IsNullOrEmpty(g.destPalette))
                    Validate(input, g, root, prefab, report);
            }
            uint crc;
            if (!BuildPipeline.GetCRCForAssetBundle(Path.Combine(input.outputDir, input.bundleName), out crc))
                throw new Exception("Could not obtain built bundle CRC");
            // Dependent textures are not registered main assets, so LoadAllAssets<Texture2D>() omits them.
            var textures = ReferencedTextures(loadedPrefabs).Select(TextureEvidence).OrderBy(t => t.name).ToArray();
            if (textures.Length == 0) throw new Exception("No dependent armor textures were measured");
            CheckTextureBudget(textures, input.textureBudgetBytes > 0 ? input.textureBudgetBytes : MaxBundleTextureBytes);
            var contract = new BundleContract { status = "passed", unityVersion = Application.unityVersion, bundleCRC = crc,
                bundleName = input.bundleName, dependencies = dependencies, assetNames = built.GetAllAssetNames(),
                textures = textures, textureBytes = textures.Sum(t => t.textureBytes),
                clipChecks = clipChecks.ToArray(), animatorPoseChecks = poseChecks.ToArray(),
                genders = contracts.ToArray(), hitFlashBehavior = "FlashRig parts retain appearance during white hit flashes through native NoFlashWhenHit",
                helmAttachmentContract = input.genders.Any(IsHelm) ? 2 : 0 };
            File.WriteAllText(Path.Combine(input.outputDir, "bundle_contract.json"), JsonUtility.ToJson(contract, true));
            built.Unload(true);
            File.WriteAllLines(Path.Combine(input.outputDir, "unity_report.txt"), report);
            File.Delete(Path.Combine(input.outputDir, "build_failure.txt"));
            Debug.Log("ARMOR_BUILD_SUCCESS " + input.bundleName);
    }

    static IEnumerable<Texture2D> ReferencedTextures(IEnumerable<GameObject> prefabs)
    {
        var textures = new HashSet<Texture2D>();
        foreach (var prefab in prefabs)
        {
            var itemSlots = prefab.GetComponent<ItemSlots>();
            if (itemSlots != null)
                foreach (var entry in itemSlots.slots)
                {
                    if (entry.sprite != null) textures.Add(entry.sprite.texture);
                    if (entry.CCMask != null) textures.Add(entry.CCMask);
                }
            foreach (var renderer in prefab.GetComponentsInChildren<SpriteRenderer>(true))
            {
                if (renderer.sprite != null) textures.Add(renderer.sprite.texture);
                var material = renderer.sharedMaterial;
                if (material == null || material.shader.name != "SwordShine/FlashRig") continue;
                foreach (string property in material.GetTexturePropertyNames().Where(p => p.StartsWith("_CC")))
                {
                    var texture = material.GetTexture(property) as Texture2D;
                    if (texture != null) textures.Add(texture);
                }
            }
        }
        return textures;
    }

    static void ValidatePetMovement(Input input, GameObject prefab)
    {
        var instance = UnityEngine.Object.Instantiate(prefab);
        try
        {
            var root = instance.GetComponent<Animator>();
            var visual = instance.GetComponentsInChildren<Animator>().FirstOrDefault(a => a != root);
            if (root == null || visual == null || instance.GetComponentInChildren<Animator>() != root)
                throw new Exception("Native follower must find the independent root Animator first");
            if (!root.HasState(0, Animator.StringToHash("Idle")) || !root.HasState(0, Animator.StringToHash("Walk")))
                throw new Exception("Native follower's Idle/Walk states are missing");
            visual.Rebind(); visual.Update(0f); visual.Update(0.137f);
            var clocks = Enumerable.Range(0, visual.layerCount).Select(i => visual.GetCurrentAnimatorStateInfo(i).normalizedTime).ToArray();
            for (int cycle = 0; cycle < 12; cycle++)
            {
                root.Play(cycle % 2 == 0 ? "Walk" : "Idle"); root.Update(0f);
                for (int i = 0; i < clocks.Length; i++)
                    if (visual.GetCurrentAnimatorStateInfo(i).normalizedTime != clocks[i])
                        throw new Exception("Movement restarted a nested pet animation clock");
            }
            File.WriteAllText(Path.Combine(input.outputDir, "pet_movement_validation.json"), JsonUtility.ToJson(
                new PetMovementCheck { status = "passed", prefabName = prefab.name, switches = 12,
                    visualLayers = visual.layerCount, followerFindsRoot = true, visualClocksPreserved = true }, true));
        }
        finally { UnityEngine.Object.DestroyImmediate(instance); }
    }

    // A 20-player room holds every equipped bundle at once, so one item may not cost more than this
    // (batch_trials.audit applies the same limits independently to the serialized bundle). An input may
    // carry a larger byte budget only for a bundle listed in batch_trials.TEXTURE_BUDGET_WAIVERS.
    const int MaxTextureDimension = 4096;
    const long MaxBundleTextureBytes = 24L * 1024 * 1024;

    static void CheckTextureBudget(TextureContract[] textures, long budgetBytes)
    {
        var readable = textures.Where(t => t.readable).Select(t => t.name).ToArray();
        if (readable.Length > 0) throw new Exception("Bundled textures are CPU-readable: " + string.Join(", ", readable));
        var large = textures.Where(t => t.width > MaxTextureDimension || t.height > MaxTextureDimension).ToArray();
        if (large.Length > 0) throw new Exception("Bundled texture exceeds " + MaxTextureDimension + " px: "
            + string.Join(", ", large.Select(t => t.name + " " + t.width + "x" + t.height)));
        long total = textures.Sum(t => t.textureBytes);
        if (total > budgetBytes) throw new Exception("Bundle texture memory " + total + " bytes exceeds the "
            + budgetBytes + "-byte budget");
    }

    static TextureContract TextureEvidence(Texture2D texture)
    {
        long bytes = 0;
        for (int mip = 0; mip < texture.mipmapCount; mip++)
        {
            int w = Mathf.Max(1, texture.width >> mip), h = Mathf.Max(1, texture.height >> mip);
            switch (texture.format)
            {
                case TextureFormat.RGBA32: case TextureFormat.ARGB32: bytes += (long)w * h * 4; break;
                case TextureFormat.RGB24: bytes += (long)w * h * 3; break;
                case TextureFormat.DXT1: bytes += (long)((w + 3) / 4) * ((h + 3) / 4) * 8; break;
                case TextureFormat.DXT5: case TextureFormat.BC7: bytes += (long)((w + 3) / 4) * ((h + 3) / 4) * 16; break;
                default: throw new Exception("Unaccounted bundled texture format: " + texture.format);
            }
        }
        return new TextureContract { name = texture.name, format = texture.format.ToString(), width = texture.width,
            height = texture.height, mipCount = texture.mipmapCount, textureBytes = bytes, readable = texture.isReadable };
    }

    static GenderContract InspectContract(string gender, GameObject prefab, bool sourceFace = false)
    {
        var instance = UnityEngine.Object.Instantiate(prefab);
        var components = instance.GetComponentsInChildren<Component>(true);
        if (components.Any(c => c == null)) throw new Exception("Emitted prefab has a missing component");
        var scripts = components.OfType<MonoBehaviour>().ToArray();
        string[] native = { "ItemSlots", "CustomizableSlot", "NoFlashWhenHit" };
        if (scripts.Any(script => !native.Contains(script.GetType().FullName) || script.GetType().Assembly.GetName().Name != "Assembly-CSharp"))
            throw new Exception("Emitted prefab contains a novel runtime script");
        var renderers = instance.GetComponentsInChildren<SpriteRenderer>(true);
        var layered = renderers.Where(r => r.sharedMaterial != null && r.sharedMaterial.shader.name == "SwordShine/FlashRig").ToArray();
        var markers = instance.GetComponentsInChildren<NoFlashWhenHit>(true);
        if (layered.Length != markers.Length || layered.Any(r => r.GetComponent<NoFlashWhenHit>() == null)
                || markers.Any(m => m.GetComponent<SpriteRenderer>() == null || !layered.Contains(m.GetComponent<SpriteRenderer>())))
            throw new Exception("Native hit-flash markers differ from FlashRig renderers");
        var slots = instance.GetComponent<ItemSlots>();
        if (slots != null && slots.slots.Any(s => s.sprite == null)) throw new Exception("Invalid native ItemSlots");
        if (!sourceFace && slots != null && slots.slots.Any(s => s.slot == CustomizableCharacter.Slot.head && s.CCMask == null))
            throw new Exception("Armor head has no native skin-color mask");
        if (sourceFace && (slots == null || !slots.slots.Any(s => s.slot == CustomizableCharacter.Slot.head) ||
            instance.GetComponentsInChildren<CustomizableSlot>(true).Count(s => s.slot == CustomizableCharacter.Slot.head) != 1 ||
            renderers.Any(r => r.enabled && r.sprite != null && new[] { "eyes", "eyescolor", "eyeslight", "eyesblink" }.Contains(r.name))))
            throw new Exception("Source morph head must replace the standard face and eye overlays");
        var result = new GenderContract { gender = gender, prefabName = prefab.name, renderers = renderers.Length,
            itemSlots = slots != null ? slots.slots.Count : 0, customizableSlots = instance.GetComponentsInChildren<CustomizableSlot>(true).Length,
            flashRigRenderers = layered.Length, noFlashMarkers = markers.Length,
            animators = instance.GetComponentsInChildren<Animator>(true).Length,
            componentTypes = components.Select(c => c.GetType().FullName).Distinct().OrderBy(n => n).ToArray(),
            runtimeScriptClasses = scripts.Select(c => c.GetType().FullName).Distinct().OrderBy(n => n).ToArray(),
            materialShaders = renderers.Where(r => r.sharedMaterial != null).Select(r => r.sharedMaterial.shader.name).Distinct().OrderBy(n => n).ToArray() };
        UnityEngine.Object.DestroyImmediate(instance);
        return result;
    }

    static string PrefabName(Gender g)
    {
        return string.IsNullOrEmpty(g.prefabName) ? "ArmorBones_" + g.g : g.prefabName;
    }

    static bool IsHelm(Gender g) => g.nodes.Any(n => n.name == "helm" && n.slot == 37) && g.renderers.Any(r => r.slot == "helm");

    static string Arg(string name)
    {
        var args = Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1];
        throw new Exception("missing " + name);
    }

    static string BuildGender(Input input, Gender g, string root, List<string> report)
    {
        string gdir = root + "/" + g.g;
        // atlas: like the native armors (DXT5, sRGB, clamp, no mips) but not readable. The client
        // composites atlases on the GPU (TextureCompositorOrtho) and never reads them on the CPU,
        // so a readable flag would only keep a second, CPU-side copy of every atlas in memory.
        string atlasPath = root + "/" + g.atlas;
        var ti = (TextureImporter)AssetImporter.GetAtPath(atlasPath);
        ti.textureType = TextureImporterType.Default;
        ti.sRGBTexture = true;
        ti.mipmapEnabled = false;
        ti.isReadable = false;
        ti.alphaSource = TextureImporterAlphaSource.FromInput;
        ti.alphaIsTransparency = false;
        ti.npotScale = TextureImporterNPOTScale.None;
        ti.wrapMode = TextureWrapMode.Clamp;
        ti.filterMode = FilterMode.Bilinear;
        ti.maxTextureSize = 8192;
        bool rgbCC = g.reconstruction != null && g.reconstruction.enabled;
        bool directCC = rgbCC && g.reconstruction.directPalette;
        ti.textureCompression = rgbCC && !directCC ? TextureImporterCompression.Uncompressed : TextureImporterCompression.Compressed;
        var atlasFormat = rgbCC && !directCC ? TextureImporterFormat.RGBA32 : (g.atlasFormat == "BC7" ? TextureImporterFormat.BC7 : TextureImporterFormat.DXT5);
        ti.SetPlatformTextureSettings(new TextureImporterPlatformSettings { name = "Standalone", overridden = true, maxTextureSize = 8192, format = atlasFormat });
        ti.SaveAndReimport();
        var atlas = AssetDatabase.LoadAssetAtPath<Texture2D>(atlasPath);
        Directory.CreateDirectory(gdir + "/Sprites");
        var sprites = new Dictionary<string, Sprite>();
        var masks = new Dictionary<string, Texture2D>();
        foreach (var s in g.sprites)
        {
            var sp = Sprite.Create(atlas, new Rect(s.x, s.y, s.w, s.h), new Vector2(s.px, s.py), s.ppu, 0,
                g.meshType == "FullRect" ? SpriteMeshType.FullRect : SpriteMeshType.Tight);
            string path = gdir + "/Sprites/" + s.name + ".asset";
            AssetDatabase.CreateAsset(sp, path);
            sprites[s.name] = AssetDatabase.LoadAssetAtPath<Sprite>(path);
            if (!string.IsNullOrEmpty(s.mask))
            {
                // Direct palettes read their six colors from runtime properties,
                // so native compositor masks can use the native DXT1 format.
                // Probe-decoded palettes retain exact mask channels in RGB24.
                string mpath = root + "/" + s.mask;
                var mi = (TextureImporter)AssetImporter.GetAtPath(mpath);
                mi.textureType = TextureImporterType.Default;
                mi.sRGBTexture = false;
                mi.mipmapEnabled = false;
                mi.isReadable = false;
                mi.alphaSource = TextureImporterAlphaSource.None;
                mi.npotScale = TextureImporterNPOTScale.None;
                mi.wrapMode = TextureWrapMode.Repeat;
                mi.filterMode = FilterMode.Bilinear;
                mi.textureCompression = rgbCC && !directCC ? TextureImporterCompression.Uncompressed : TextureImporterCompression.Compressed;
                mi.SetPlatformTextureSettings(new TextureImporterPlatformSettings { name = "Standalone", overridden = true, maxTextureSize = 2048, format = rgbCC && !directCC ? TextureImporterFormat.RGB24 : TextureImporterFormat.DXT1 });
                mi.SaveAndReimport();
                masks[s.name] = AssetDatabase.LoadAssetAtPath<Texture2D>(mpath);
            }
        }
        // materials + animation for layered renderers
        var shader = Shader.Find("SwordShine/FlashRig");
        if (shader == null) throw new Exception("FlashRig shader missing");
        var spriteDefault = AssetDatabase.GetBuiltinExtraResource<Material>("Sprites-Default.mat");
        Directory.CreateDirectory(gdir + "/Anim");
        var mats = new Dictionary<string, Material>();
        var controllers = new Dictionary<string, RuntimeAnimatorController>();
        var coefficientTextures = new Dictionary<string, Texture2D>();
        if (rgbCC)
        {
            if (g.reconstruction.model != "piecewise_linear" || g.reconstruction.knot <= 0 || g.reconstruction.knot >= 1)
                throw new Exception("Unsupported RGB reconstruction model");
            var definitions = new[] { g.reconstruction.constant, g.reconstruction.baseSlope, g.reconstruction.trimSlope, g.reconstruction.accessorySlope,
                    g.reconstruction.baseHighSlope, g.reconstruction.trimHighSlope, g.reconstruction.accessoryHighSlope }.ToList();
            if (g.reconstruction.extraPalette)
                definitions.AddRange(new[] { g.reconstruction.skinSlope, g.reconstruction.hairSlope, g.reconstruction.eyeSlope,
                    g.reconstruction.skinHighSlope, g.reconstruction.hairHighSlope, g.reconstruction.eyeHighSlope });
            foreach (var definition in definitions)
            {
                string path = root + "/" + definition.path;
                var importer = (TextureImporter)AssetImporter.GetAtPath(path);
                importer.textureType = TextureImporterType.Default;
                importer.sRGBTexture = false;
                importer.mipmapEnabled = false;
                importer.isReadable = false;
                importer.alphaIsTransparency = false;
                importer.npotScale = TextureImporterNPOTScale.None;
                importer.wrapMode = TextureWrapMode.Clamp;
                importer.filterMode = FilterMode.Bilinear;
                string coefficientFormat = string.IsNullOrEmpty(definition.format) ? g.reconstruction.coefficientFormat : definition.format;
                if (!new[] { "BC7", "DXT1", "RGBA32" }.Contains(coefficientFormat)) throw new Exception("Unsupported coefficient format");
                bool compressed = coefficientFormat != "RGBA32";
                importer.textureCompression = compressed ? TextureImporterCompression.Compressed : TextureImporterCompression.Uncompressed;
                importer.SetPlatformTextureSettings(new TextureImporterPlatformSettings { name = "Standalone", overridden = true, maxTextureSize = 8192,
                    format = coefficientFormat == "BC7" ? TextureImporterFormat.BC7 : coefficientFormat == "DXT1" ? TextureImporterFormat.DXT1 : TextureImporterFormat.RGBA32 });
                importer.SaveAndReimport();
                var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
                if ((texture.width != g.atlasW || texture.height != g.atlasH) && !(directCC && texture.width == 4 && texture.height == 4))
                    throw new Exception("Coefficient atlas layout differs");
                coefficientTextures[definition.path] = texture;
            }
        }
        foreach (var r in g.renderers)
        {
            if (!r.layered) continue;
            // Compile only the layer capacity this renderer needs. Small items
            // otherwise pay the optimizer cost of 128 copies of RGB sampling.
            var mat = new Material(shader) { name = input.name + "_" + g.g + "_" + r.slot };
            mat.SetVector("_AtlasSize", new Vector4(g.atlasW, g.atlasH, 0, 0));
            mat.SetVector("_Tint", new Vector4(r.tint[0], r.tint[1], r.tint[2], r.tint[3]));
            if (r.layers.Length > MaxLayers) throw new Exception("More layers than FlashRig supports: " + r.slot);
            mat.SetFloat("_LayerCount", r.layers.Length);
            if (r.layers.Length > 64) mat.EnableKeyword("FLASHRIG_LAYERS_128");
            else if (r.layers.Length > 32) mat.EnableKeyword("FLASHRIG_LAYERS_64");
            else if (r.layers.Length > 16) mat.EnableKeyword("FLASHRIG_LAYERS_32");
            if (rgbCC)
            {
                mat.SetFloat("_CCEnabled", 1);
                if (g.reconstruction.directPalette)
                {
                    mat.SetFloat("_CCDirectPalette", 1);
                    mat.SetColor("_CCDirectBase", new Color(g.reconstruction.paletteBase[0], g.reconstruction.paletteBase[1], g.reconstruction.paletteBase[2]));
                    mat.SetColor("_CCDirectTrim", new Color(g.reconstruction.paletteTrim[0], g.reconstruction.paletteTrim[1], g.reconstruction.paletteTrim[2]));
                    mat.SetColor("_CCDirectAccessory", new Color(g.reconstruction.paletteAccessory[0], g.reconstruction.paletteAccessory[1], g.reconstruction.paletteAccessory[2]));
                    var sources = g.reconstruction.paletteSources ?? new[] { 5, 1, 2 };
                    if (sources.Length != 3) throw new Exception("Invalid direct palette source mapping");
                    string[] labels = { "Base", "Trim", "Accessory" };
                    for (int channel = 0; channel < 3; channel++)
                        mat.SetFloat("_CCSource" + labels[channel], sources[channel]);
                }
                mat.SetFloat("_CCKnot", g.reconstruction.knot);
                ConfigureCoefficient(mat, "Constant", g.reconstruction.constant, coefficientTextures);
                ConfigureCoefficient(mat, "Base", g.reconstruction.baseSlope, coefficientTextures);
                ConfigureCoefficient(mat, "Trim", g.reconstruction.trimSlope, coefficientTextures);
                ConfigureCoefficient(mat, "Accessory", g.reconstruction.accessorySlope, coefficientTextures);
                ConfigureCoefficient(mat, "BaseHigh", g.reconstruction.baseHighSlope, coefficientTextures);
                ConfigureCoefficient(mat, "TrimHigh", g.reconstruction.trimHighSlope, coefficientTextures);
                ConfigureCoefficient(mat, "AccessoryHigh", g.reconstruction.accessoryHighSlope, coefficientTextures);
                if (g.reconstruction.extraPalette)
                {
                    mat.SetFloat("_CCExtraPalette", 1);
                    ConfigureCoefficient(mat, "Skin", g.reconstruction.skinSlope, coefficientTextures);
                    ConfigureCoefficient(mat, "Hair", g.reconstruction.hairSlope, coefficientTextures);
                    ConfigureCoefficient(mat, "Eye", g.reconstruction.eyeSlope, coefficientTextures);
                    ConfigureCoefficient(mat, "SkinHigh", g.reconstruction.skinHighSlope, coefficientTextures);
                    ConfigureCoefficient(mat, "HairHigh", g.reconstruction.hairHighSlope, coefficientTextures);
                    ConfigureCoefficient(mat, "EyeHigh", g.reconstruction.eyeHighSlope, coefficientTextures);
                    mat.SetColor("_CCDirectSkin", new Color(g.reconstruction.paletteSkin[0], g.reconstruction.paletteSkin[1], g.reconstruction.paletteSkin[2]));
                    mat.SetColor("_CCDirectHair", new Color(g.reconstruction.paletteHair[0], g.reconstruction.paletteHair[1], g.reconstruction.paletteHair[2]));
                    mat.SetColor("_CCDirectEye", new Color(g.reconstruction.paletteEye[0], g.reconstruction.paletteEye[1], g.reconstruction.paletteEye[2]));
                }
                foreach (var probe in g.reconstruction.probes)
                {
                    if (probe.gain <= 0) throw new Exception("Invalid player-color probe gain");
                    mat.SetVector("_CCProbe" + probe.location, new Vector4(probe.rect[0] + probe.rect[2] / 2,
                        probe.rect[1] + probe.rect[3] / 2, 1 / probe.gain, -probe.bias / probe.gain));
                }
            }
            for (int i = 0; i < r.layers.Length; i++)
            {
                var L = r.layers[i];
                mat.SetFloat("_L" + i + "Blend", L.blend);
                mat.SetFloat("_L" + i + "Begin", L.groupStart ? 1 : 0);
                mat.SetFloat("_L" + i + "End", L.groupEnd);
                mat.SetFloat("_L" + i + "Morph", L.morphFrame);
                ApplyKey(mat, i, L.keys, 0);
            }
            AssetDatabase.CreateAsset(mat, gdir + "/Anim/" + mat.name + ".mat");
            if (!shader.isSupported || ShaderUtil.GetShaderMessages(shader).Any(message => message.severity == UnityEditor.Rendering.ShaderCompilerMessageSeverity.Error))
                throw new Exception("FlashRig shader failed compilation or is unsupported");
            mats[r.slot] = mat;
            var periods = r.layers.Select(l => l.period).Where(p => p > 1).Distinct().OrderBy(p => p).ToList();
            if (periods.Count == 0 && string.IsNullOrEmpty(r.morphJson)) continue;
            string cpath = gdir + "/Anim/" + mat.name + ".controller";
            var ctrl = AnimatorController.CreateAnimatorControllerAtPath(cpath);
            if (!string.IsNullOrEmpty(r.morphJson))
            {
                ctrl.name = "FlashMorph:v1:" + Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(r.morphJson));
                if (periods.Count == 0)
                    ctrl.layers[0].stateMachine.defaultState = ctrl.layers[0].stateMachine.AddState("Idle");
            }
            for (int pi = 0; pi < periods.Count; pi++)
            {
                int period = periods[pi];
                var clip = new AnimationClip { name = mat.name + "_p" + period, frameRate = input.fps };
                int curves = 0;
                int scalarKeys = 0;
                float maxKeyError = 0;
                for (int i = 0; i < r.layers.Length; i++)
                {
                    var L = r.layers[i];
                    if (L.period != period) continue;
                    // A material vector animated in only some components gets zeros in the others, so a
                    // vector is keyed whole as soon as any of its components changes.
                    var groupVaries = new bool[KeyWidth];
                    foreach (var range in VectorGroups)
                    {
                        bool any = false;
                        for (int c = range[0]; c < range[1] && !any; c++)
                            for (int f = 1; f < period && !any; f++) any = Mathf.Abs(L.keys[f * KeyWidth + c] - L.keys[c]) > 1e-6f;
                        for (int c = range[0]; c < range[1]; c++) groupVaries[c] = any;
                    }
                    for (int c = 0; c < KeyWidth; c++)
                    {
                        if (!groupVaries[c]) continue;
                        bool stepped = L.stepped || (L.steppedRect && c >= 14);
                        bool guardedClock = g.noItemSlots || (g.reconstruction != null && g.reconstruction.directPalette);
                        float guard = guardedClock ? 0.01f / input.fps : StepEpsilon;
                        var keyList = new List<Keyframe>();
                        // Unity needs every component of an animated vector,
                        // including its constant components. Two equal endpoint
                        // keys retain that exact value and clip duration without
                        // repeating it thousands of times in long source loops.
                        bool constantComponent = true;
                        for (int f = 1; f < period && constantComponent; f++)
                            constantComponent = L.keys[f * KeyWidth + c] == L.keys[c];
                        if (constantComponent)
                        {
                            keyList.Add(new Keyframe(0, L.keys[c]));
                            keyList.Add(new Keyframe((float)period / input.fps, L.keys[c]));
                        }
                        for (int f = 0; !constantComponent && f <= period; f++)
                        {
                            float time = (float)f / input.fps;
                            float value = L.keys[(f % period) * KeyWidth + c];
                            // A one-percent-of-a-frame plateau absorbs normalized-clock float
                            // roundoff on long expanded loops. Every authored integer pose and
                            // the exact loop duration remain unchanged; motion between them stays linear.
                            if (guardedClock && !stepped)
                            {
                                if (f > 0) keyList.Add(new Keyframe(time - guard, value));
                                keyList.Add(new Keyframe(time, value));
                                if (f < period) keyList.Add(new Keyframe(time + guard, value));
                            }
                            else
                            {
                                // A short weapon flipbook can land infinitesimally before
                                // its loop boundary after Animator clock normalization.
                                // Keep its exact duration and guard the closing step too.
                                if (stepped && (guardedClock || g.noItemSlots) && f == period)
                                    keyList.Add(new Keyframe(time - guard, value));
                                keyList.Add(new Keyframe(time - (stepped && f > 0 && f < period ? guard : 0), value));
                            }
                        }
                        var keys = keyList.ToArray();
                        for (int f = 0; f < keys.Length; f++)
                        {
                            float vin = f > 0 ? (keys[f].value - keys[f - 1].value) / (keys[f].time - keys[f - 1].time) : 0;
                            float vout = f + 1 < keys.Length ? (keys[f + 1].value - keys[f].value) / (keys[f + 1].time - keys[f].time) : 0;
                            keys[f].inTangent = f > 0 ? vin : vout;
                            keys[f].outTangent = f + 1 < keys.Length ? vout : vin;
                        }
                        var binding = EditorCurveBinding.FloatCurve("", typeof(SpriteRenderer), "material." + string.Format(Comp[c], i));
                        var curve = new AnimationCurve(keys);
                        if (stepped)  // image rects switch, they do not blend
                            for (int f = 0; f < keys.Length; f++)
                            {
                                AnimationUtility.SetKeyLeftTangentMode(curve, f, AnimationUtility.TangentMode.Constant);
                                AnimationUtility.SetKeyRightTangentMode(curve, f, AnimationUtility.TangentMode.Constant);
                            }
                        AnimationUtility.SetEditorCurve(clip, binding, curve);
                        for (int f = 0; f <= period; f++)
                        {
                            float t = (float)f / input.fps;
                            float error = Mathf.Abs(curve.Evaluate(t) - L.keys[(f % period) * KeyWidth + c]);
                            maxKeyError = Mathf.Max(maxKeyError, error);
                            scalarKeys++;
                        }
                        curves++;
                    }
                }
                var settings = AnimationUtility.GetAnimationClipSettings(clip);
                settings.loopTime = true;
                AnimationUtility.SetAnimationClipSettings(clip, settings);
                AssetDatabase.CreateAsset(clip, gdir + "/Anim/" + clip.name + ".anim");
                if (maxKeyError > 0.0001f) throw new Exception("Animation curve key/loop validation failed");
                clipChecks.Add(new ClipCheck { clip = clip.name, period = period, curves = curves,
                    scalarKeys = scalarKeys, maxKeyError = maxKeyError });
                if (pi > 0)
                {
                    ctrl.AddLayer("p" + period);
                    var layers = ctrl.layers;
                    layers[pi].defaultWeight = 1f;
                    layers[pi].blendingMode = AnimatorLayerBlendingMode.Override;
                    ctrl.layers = layers;
                }
                ctrl.AddMotion(clip, pi);
                report.Add(string.Format("{0} {1}: clip p{2} with {3} curves", g.g, r.slot, period, curves));
            }
            controllers[r.slot] = ctrl;
        }
        AssetDatabase.SaveAssets();
        // prefab from the native template
        var rmap = g.renderers.ToDictionary(r => r.slot, r => r);
        var gos = new GameObject[g.nodes.Length];
        for (int i = 0; i < g.nodes.Length; i++)
        {
            var n = g.nodes[i];
            var go = new GameObject(n.name);
            gos[i] = go;
            if (n.parent >= 0) go.transform.SetParent(gos[n.parent].transform, false);
            go.transform.localPosition = new Vector3(n.pos[0], n.pos[1], n.pos[2]);
            go.transform.localRotation = new Quaternion(n.rot[0], n.rot[1], n.rot[2], n.rot[3]);
            go.transform.localScale = new Vector3(n.scale[0], n.scale[1], n.scale[2]);
            if (n.hasRenderer)
            {
                var sr = go.AddComponent<SpriteRenderer>();
                sr.sortingOrder = n.order;
                sr.sortingLayerID = n.sortingLayer;
                sr.enabled = n.rendererEnabled;
                sr.color = new Color(n.color[0], n.color[1], n.color[2], n.color[3]);
                sr.sharedMaterial = spriteDefault;
                if (rmap.TryGetValue(n.name, out var rd) && sprites.TryGetValue(rd.sprite, out var spr))
                {
                    sr.sprite = spr;
                    if (rd.layered)
                    {
                        sr.sharedMaterial = mats[n.name];
                        // Native hit flashes otherwise replace this material permanently.
                        go.AddComponent<NoFlashWhenHit>();
                        if (controllers.TryGetValue(n.name, out var ctrl))
                        {
                            var an = go.AddComponent<Animator>();
                            an.runtimeAnimatorController = ctrl;
                            an.cullingMode = AnimatorCullingMode.AlwaysAnimate;
                            an.applyRootMotion = false;
                        }
                    }
                }
            }
            if (n.slot >= 0)
                go.AddComponent<CustomizableSlot>().slot = (CustomizableCharacter.Slot)n.slot;
            if (i == 0 && !g.noItemSlots)
            {
                var slots = go.AddComponent<ItemSlots>();
                foreach (var e in g.itemslots)
                {
                    var s = new SpriteSlot { slot = (CustomizableCharacter.Slot)e.slot, sprite = sprites[e.sprite] };
                    if (masks.TryGetValue(e.sprite, out var m)) s.CCMask = m;
                    slots.slots.Add(s);
                }
            }
        }
        if (g.petLoop)
        {
            // FollowerGameObject plays Idle/Walk on the first Animator in the hierarchy.
            // These source states share the same visual loop, so use an independent root
            // controller. Movement must never restart the nested artwork's clocks.
            gos[0].AddComponent<SortingGroup>();
            var control = AnimatorController.CreateAnimatorControllerAtPath(gdir + "/Anim/PetMovement.controller");
            var machine = control.layers[0].stateMachine;
            machine.defaultState = machine.AddState("Idle");
            machine.AddState("Walk");
            var movement = gos[0].AddComponent<Animator>();
            movement.runtimeAnimatorController = control;
            movement.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            movement.applyRootMotion = false;
        }
        for (int i = 0; i < g.nodes.Length; i++) gos[i].SetActive(g.nodes[i].active);
        string prefabPath = root + "/" + PrefabName(g) + ".prefab";
        PrefabUtility.SaveAsPrefabAsset(gos[0], prefabPath);
        if (IsHelm(g) && PrefabName(g) != "HelmGO")
        {
            gos[0].name = "HelmGO";
            PrefabUtility.SaveAsPrefabAsset(gos[0], root + "/HelmGO.prefab");
        }
        UnityEngine.Object.DestroyImmediate(gos[0]);
        report.Add(string.Format("{0}: {1} sprites, {2} masks, {3} ItemSlots, {4} layered renderers", g.g, sprites.Count, masks.Count, g.itemslots == null ? 0 : g.itemslots.Length, mats.Count));
        return prefabPath;
    }

    static void ConfigureCoefficient(Material material, string label, CoeffTexture definition, Dictionary<string, Texture2D> textures)
    {
        material.SetTexture("_CC" + label + "Tex", textures[definition.path]);
        material.SetVector("_CC" + label + "Scale", new Vector4(definition.decodeScale[0], definition.decodeScale[1], definition.decodeScale[2], 0));
        material.SetVector("_CC" + label + "Bias", new Vector4(definition.decodeBias[0], definition.decodeBias[1], definition.decodeBias[2], 0));
    }

    static void ApplyKey(Material mat, int i, float[] keys, int f)
    {
        int o = f * KeyWidth;
        mat.SetVector("_L" + i + "A", new Vector4(keys[o], keys[o + 1], keys[o + 2], keys[o + 3]));
        mat.SetVector("_L" + i + "T", new Vector4(keys[o + 4], keys[o + 5], 0, 0));
        mat.SetVector("_L" + i + "CM", new Vector4(keys[o + 6], keys[o + 7], keys[o + 8], keys[o + 9]));
        mat.SetVector("_L" + i + "CA", new Vector4(keys[o + 10], keys[o + 11], keys[o + 12], keys[o + 13]));
        mat.SetVector("_L" + i + "Rect", new Vector4(keys[o + 14], keys[o + 15], keys[o + 16], keys[o + 17]));
    }

    // Render animated parts the way the client draws them in-game: the renderer gets a sprite on the composited
    // texture (here the Python emulation of the client's compositor) and the Animator drives the material.
    static void Validate(Input input, Gender g, string root, GameObject prefab, List<string> report)
    {
        string dpath = root + "/" + g.destPalette;
        var di = (TextureImporter)AssetImporter.GetAtPath(dpath);
        di.textureType = TextureImporterType.Default;
        di.sRGBTexture = true;
        di.mipmapEnabled = false;
        di.isReadable = true;
        di.npotScale = TextureImporterNPOTScale.None;
        di.textureCompression = TextureImporterCompression.Uncompressed;
        di.maxTextureSize = 8192;  // the default 2048 cap would shrink large composited textures
        di.wrapMode = TextureWrapMode.Clamp;
        di.SaveAndReimport();
        var dest = AssetDatabase.LoadAssetAtPath<Texture2D>(dpath);
        string vdir = Path.Combine(input.outputDir, "unity_validation");
        Directory.CreateDirectory(vdir);
        var camGo = new GameObject("cam");
        var cam = camGo.AddComponent<Camera>();
        cam.orthographic = true;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = new Color(0, 0, 0, 0);
        cam.nearClipPlane = 0.01f;
        cam.farClipPlane = 100f;
        var defs = g.sprites.ToDictionary(s => s.name, s => s);
        foreach (var r in g.renderers)
        {
            if (r.slot.StartsWith("eyes", StringComparison.Ordinal)) continue;
            var inst = IsHelm(g) ? NativeHelmContract.Attach(prefab) : UnityEngine.Object.Instantiate(prefab);
            var part = inst.GetComponentsInChildren<SpriteRenderer>(true).First(c => c.gameObject.name.Replace("(Clone)", "") == r.slot).gameObject;
            part.transform.SetParent(null, false);
            part.transform.position = Vector3.zero;
            part.transform.rotation = Quaternion.identity;
            part.transform.localScale = Vector3.one;
            part.SetActive(true);
            if (part != inst) UnityEngine.Object.DestroyImmediate(inst);  // weapons/pets: the root is the part
            var sr = part.GetComponent<SpriteRenderer>();
            // Head owns the eye hierarchy. This capture checks one atlas region,
            // so exclude its child renderers from the per-slot comparison.
            foreach (var childRenderer in part.GetComponentsInChildren<SpriteRenderer>(true))
                if (childRenderer != sr) childRenderer.enabled = false;
            var d = defs[r.sprite];
            sr.sprite = Sprite.Create(dest, new Rect(d.x, d.y, d.w, d.h), new Vector2(d.px, d.py), d.ppu, 0, SpriteMeshType.FullRect);
            var an = part.GetComponent<Animator>();
            int w = (int)d.w, h = (int)d.h;
            var rt = new RenderTexture(w, h, 0, RenderTextureFormat.ARGB32);
            cam.targetTexture = rt;
            cam.orthographicSize = h / d.ppu / 2f;
            var b = sr.sprite.bounds;
            camGo.transform.position = new Vector3(b.center.x, b.center.y, -10);
            // Exercise every integer pose in every individual layer period using the serialized Animator.
            // This does not claim a GPU render of every state of a multi-period joint cycle.
            int individualPeriod = r.layers.Select(layer => layer.period).DefaultIfEmpty(1).Max();
            int poseLimit = individualPeriod > 1 ? Math.Max(individualPeriod, 32) : individualPeriod;
            float sampleEpsilon = 0f; // stepped keys have their own tiny early bias; continuous tracks stay exact
            for (int f = 0; f <= poseLimit; f++)
            {
                if (an != null)
                {
                    an.Rebind();
                    an.Update(0f);
                    an.Update((float)f / input.fps + sampleEpsilon);
                }
                CheckPose(g.g, r, sr, f, "full_individual_period", g.noItemSlots || (g.reconstruction != null && g.reconstruction.directPalette));
            }
            foreach (int f in input.validateFrames)
            {
                if (an != null)
                {
                    an.Rebind();
                    an.Update(0f);
                    an.Update((float)f / input.fps + sampleEpsilon);
                }
                CheckPose(g.g, r, sr, f, "gpu_sample", g.noItemSlots || (g.reconstruction != null && g.reconstruction.directPalette));
                cam.Render();
                RenderTexture.active = rt;
                var tex = new Texture2D(w, h, TextureFormat.RGBA32, false);
                tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                tex.Apply();
                RenderTexture.active = null;
                File.WriteAllBytes(Path.Combine(vdir, string.Format("{0}_{1}_f{2:000}.png", g.g, r.slot, f)), tex.EncodeToPNG());
                UnityEngine.Object.DestroyImmediate(tex);
                if (!string.IsNullOrEmpty(r.morphJson))
                {
                    int count = JsonUtility.FromJson<MorphProgram>(r.morphJson).frame_count;
                    for (int state = 0; state < count; state++)
                    {
                        var morphBlock = new MaterialPropertyBlock();
                        sr.GetPropertyBlock(morphBlock);
                        morphBlock.SetFloat("_MorphFrame", state);
                        sr.SetPropertyBlock(morphBlock);
                        cam.Render();
                        RenderTexture.active = rt;
                        var pose = new Texture2D(w, h, TextureFormat.RGBA32, false);
                        pose.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                        pose.Apply();
                        RenderTexture.active = null;
                        File.WriteAllBytes(Path.Combine(vdir, string.Format("{0}_{1}_m{2:000}_f{3:000}.png", g.g, r.slot, state, f)), pose.EncodeToPNG());
                        UnityEngine.Object.DestroyImmediate(pose);
                    }
                    var resetBlock = new MaterialPropertyBlock();
                    sr.GetPropertyBlock(resetBlock);
                    resetBlock.SetFloat("_MorphFrame", 0);
                    sr.SetPropertyBlock(resetBlock);
                }
            }
            report.Add(string.Format("validated {0} {1} ({2} frames, animator {3})", g.g, r.slot, input.validateFrames.Length, an != null));
            cam.targetTexture = null;
            rt.Release();
            UnityEngine.Object.DestroyImmediate(part);
        }
        UnityEngine.Object.DestroyImmediate(camGo);
    }

    static void CheckPose(string gender, RendererDef renderer, SpriteRenderer sr, int frame, string phase, bool spatialAffineCheck)
    {
        var block = new MaterialPropertyBlock();
        sr.GetPropertyBlock(block);
        var result = new PoseCheck { gender = gender, slot = renderer.slot, frame = frame, phase = phase, spatialAffineCheck = spatialAffineCheck };
        string worst = ""; float worstError = 0;
        string[] suffixes = { "A", "T", "CM", "CA", "Rect" };
        int[] widths = { 4, 2, 4, 4, 4 };
        for (int layer = 0; layer < renderer.layers.Length; layer++)
        {
            var definition = renderer.layers[layer];
            int offset = (frame % definition.period) * KeyWidth;
            int component = 0;
            var sampled = new float[KeyWidth];
            for (int group = 0; group < suffixes.Length; group++)
            {
                string property = "_L" + layer + suffixes[group];
                Vector4 actual = block.HasVector(property) ? block.GetVector(property) : sr.material.GetVector(property);
                for (int axis = 0; axis < widths[group]; axis++, component++)
                {
                    float error = Mathf.Abs(actual[axis] - definition.keys[offset + component]);
                    sampled[component] = actual[axis];
                    if (error > worstError) { worstError = error; worst = property + "[" + axis + "] actual " + actual[axis] + " key " + definition.keys[offset + component]; }
                    if (group == 1) result.maxTranslationError = Mathf.Max(result.maxTranslationError, error);
                    else if (!spatialAffineCheck || group != 0) result.maxOtherError = Mathf.Max(result.maxOtherError, error);
                    result.scalars++;
                }
            }
            // Expanded weapons have inverse UV matrices with offsets above 100,000. Raw UV error
            // is magnified by the inverse scale and is not displacement on the rendered sprite.
            // Compare the four image corners in destination pixels instead, retaining the same
            // 0.1-pixel limit. Opacity and image-rect scalars are still checked independently.
            if (spatialAffineCheck && (definition.keys[offset + 9] > 0 || definition.keys[offset + 13] > 0 || sampled[9] > 0 || sampled[13] > 0))
            {
                double a = definition.keys[offset], b = definition.keys[offset + 1], c = definition.keys[offset + 2], d = definition.keys[offset + 3];
                double det = a * d - b * c;
                double actualDet = (double)sampled[0] * sampled[3] - (double)sampled[1] * sampled[2];
                if (Math.Abs(det) < 1e-20 || Math.Abs(actualDet) < 1e-20) throw new Exception("Singular visible animation matrix");
                foreach (double qx in new[] { 0d, (double)definition.keys[offset + 16] })
                foreach (double qy in new[] { 0d, (double)definition.keys[offset + 17] })
                {
                    double x = qx - definition.keys[offset + 4], y = qy - definition.keys[offset + 5];
                    double px = (d * x - b * y) / det, py = (-c * x + a * y) / det;
                    x = qx - sampled[4]; y = qy - sampled[5];
                    double ax = (sampled[3] * x - sampled[1] * y) / actualDet, ay = (-sampled[2] * x + sampled[0] * y) / actualDet;
                    result.maxAffinePixelError = Mathf.Max(result.maxAffinePixelError, (float)Math.Sqrt((px - ax) * (px - ax) + (py - ay) * (py - ay)));
                }
            }
        }
        poseChecks.Add(result);
        // Long normalized times lose a little float precision; translation is in source pixels.
        if ((spatialAffineCheck ? result.maxAffinePixelError : result.maxTranslationError) > 0.1f || result.maxOtherError > 0.0003f)
            throw new Exception("Loaded-bundle Animator pose differs from modulo-period source keys: " + gender + "/" + renderer.slot + "/" + frame
                + " translation=" + result.maxTranslationError + " affinePixels=" + result.maxAffinePixelError + " other=" + result.maxOtherError + " worst " + worst);
    }
}
