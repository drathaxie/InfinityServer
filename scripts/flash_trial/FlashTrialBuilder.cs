// Editor-only builder for bounded, ordered raster trials.
// Its prefab contains only native Unity components; this code is not shipped.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.Rendering;

public static class FlashTrialBuilder
{
    [Serializable] public sealed class Layer
    {
        public string name;
        public int sortingOrder, width, height, uniqueStates, textureWidth, textureHeight, spriteX, spriteY;
        public bool hasGutter;
        public float xPixels, yPixels;
        public string[] frames;
    }
    [Serializable] public sealed class Config
    {
        public string prefabName, bundleName, assetPrefix, previewName, outputDirectory;
        public int schemaVersion, frameCount, width, height;
        public float fps;
        public float originX, originY, pixelsPerUnit;
        public Layer[] layers;
        public string[] referenceFrames;
    }
    [Serializable] public sealed class SeamCheck
    {
        public int frame, width, height, maxChannelError;
        public string background;
        public float rotationDegrees, offsetXPixels, offsetYPixels;
        public double meanAbsoluteChannelError;
    }
    [Serializable] public sealed class Validation
    {
        public string status, unityVersion, graphicsDevice, colorSpace, bundle, prefabName;
        public string[] dependencies, assetNames, componentTypes, materialShaders;
        public int framesChecked, renderers, uniqueSprites, restartChecks, animatedLayers, staticLayers;
        public bool loopRestoresFirstState, loopRestoresFirstPixels, customRuntimeRequired;
        public bool seamChecksPassed;
        public SeamCheck[] seamChecks;
        public float animationLength, pixelsPerUnit;
    }

    public static void Build()
    {
        Config config = null;
        try
        {
            string project = Directory.GetParent(Application.dataPath).FullName;
            config = JsonUtility.FromJson<Config>(File.ReadAllText(Path.Combine(project, "trial_config.json")));
            if (string.IsNullOrEmpty(config.assetPrefix)) config.assetPrefix = "OmegaRapier";
            if (config.frameCount < 1 || config.fps <= 0 || config.pixelsPerUnit <= 0 ||
                config.layers == null || config.layers.Length == 0 ||
                config.layers.Any(layer => layer.frames == null || layer.frames.Length != config.frameCount || layer.uniqueStates < 1))
                throw new InvalidOperationException("Invalid captured timeline or layer configuration");
            Directory.CreateDirectory(config.outputDirectory);
            PlayerSettings.colorSpace = ColorSpace.Gamma;
            QualitySettings.antiAliasing = 0;
            GraphicsSettings.defaultRenderPipeline = null;
            QualitySettings.renderPipeline = null;
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            string clipPath = "Assets/Trial/" + config.assetPrefix + "_Loop.anim";
            string controllerPath = "Assets/Trial/" + config.assetPrefix + ".controller";
            foreach (string generated in new[] { clipPath, controllerPath, "Assets/Trial/" + config.prefabName + ".prefab" })
                if (AssetDatabase.LoadMainAssetAtPath(generated) != null) AssetDatabase.DeleteAsset(generated);
            string staleFailure = Path.Combine(config.outputDirectory, "build_failure.txt");
            if (File.Exists(staleFailure)) File.Delete(staleFailure);
            var sprites = ImportSprites(config);
            var root = new GameObject(config.prefabName);
            bool animated = config.layers.Any(layer => layer.uniqueStates > 1);
            var clip = animated ? new AnimationClip { name = config.assetPrefix + "_Loop", frameRate = config.fps } : null;
            foreach (Layer layer in config.layers)
            {
                var child = new GameObject(layer.name);
                child.transform.SetParent(root.transform, false);
                child.transform.localPosition = new Vector3(layer.xPixels / config.pixelsPerUnit,
                                                           layer.yPixels / config.pixelsPerUnit, 0);
                var renderer = child.AddComponent<SpriteRenderer>();
                renderer.sprite = sprites[layer.frames[0]];
                renderer.sortingOrder = layer.sortingOrder;
                renderer.color = Color.white;
                // Native weapons create SpriteRenderer with this same default material.
                if (renderer.sharedMaterial == null || renderer.sharedMaterial.shader == null)
                    throw new InvalidOperationException("Default sprite material unavailable");
                if (layer.uniqueStates > 1)
                {
                    // Unity holds a sprite curve's last key for one more frame (clip length = last key + 1/fps).
                    // A closing key at frameCount/fps therefore made the clip frameCount+1 frames long: frame 0
                    // showed twice per loop and sampling drifted 1/frameCount of a frame per frame (Caladbolg
                    // failed validation at frame 2109 of 2220). With the last key at (frameCount-1)/fps the
                    // clip is exactly frameCount/fps and loopTime wraps straight back to frame 0.
                    var keys = new ObjectReferenceKeyframe[config.frameCount];
                    for (int frame = 0; frame < config.frameCount; frame++)
                        keys[frame] = new ObjectReferenceKeyframe { time = (float)frame / config.fps,
                                                                  value = sprites[layer.frames[frame]] };
                    var binding = new EditorCurveBinding { path = layer.name, type = typeof(SpriteRenderer), propertyName = "m_Sprite" };
                    AnimationUtility.SetObjectReferenceCurve(clip, binding, keys);
                }
            }
            if (animated)
            {
                var clipSettings = AnimationUtility.GetAnimationClipSettings(clip);
                clipSettings.loopTime = true;
                AnimationUtility.SetAnimationClipSettings(clip, clipSettings);
                AssetDatabase.CreateAsset(clip, clipPath);
                var controller = AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
                var state = controller.layers[0].stateMachine.AddState("Loop");
                state.motion = clip;
                state.writeDefaultValues = true;
                controller.layers[0].stateMachine.defaultState = state;
                var animator = root.AddComponent<Animator>();
                animator.runtimeAnimatorController = controller;
                animator.applyRootMotion = false;
                animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            }
            string prefabPath = "Assets/Trial/" + config.prefabName + ".prefab";
            PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
            UnityEngine.Object.DestroyImmediate(root);
            AssetDatabase.SaveAssets();
            var map = new AssetBundleBuild
            {
                assetBundleName = config.bundleName,
                assetNames = new[] { prefabPath },
                addressableNames = new[] { config.prefabName }
            };
            var manifest = BuildPipeline.BuildAssetBundles(config.outputDirectory, new[] { map },
                BuildAssetBundleOptions.ChunkBasedCompression | BuildAssetBundleOptions.ForceRebuildAssetBundle,
                BuildTarget.StandaloneWindows64);
            if (manifest == null) throw new InvalidOperationException("Bundle build returned no manifest");
            Validate(config, manifest.GetAllDependencies(config.bundleName));
            Debug.Log("FLASH_TRIAL_BUILD_SUCCESS " + config.outputDirectory);
            EditorApplication.Exit(0);
        }
        catch (Exception exception)
        {
            Debug.LogException(exception);
            if (config != null)
                File.WriteAllText(Path.Combine(config.outputDirectory, "build_failure.txt"), exception.ToString());
            EditorApplication.Exit(1);
        }
    }

    static Dictionary<string, Sprite> ImportSprites(Config config)
    {
        var result = new Dictionary<string, Sprite>();
        foreach (Layer layer in config.layers)
        foreach (string path in layer.frames.Distinct())
        {
            var importer = (TextureImporter)AssetImporter.GetAtPath(path);
            importer.textureType = layer.hasGutter ? TextureImporterType.Default : TextureImporterType.Sprite;
            importer.spriteImportMode = SpriteImportMode.Single;
            importer.spritePixelsPerUnit = config.pixelsPerUnit;
            importer.spritePivot = new Vector2(0.5f, 0.5f);
            var settings = new TextureImporterSettings();
            importer.ReadTextureSettings(settings);
            settings.spriteAlignment = (int)SpriteAlignment.Center;
            settings.spriteMeshType = SpriteMeshType.FullRect;
            settings.spriteGenerateFallbackPhysicsShape = false;
            importer.SetTextureSettings(settings);
            importer.npotScale = TextureImporterNPOTScale.None;
            importer.maxTextureSize = 2048;
            importer.textureCompression = TextureImporterCompression.Uncompressed;
            importer.mipmapEnabled = false;
            importer.alphaSource = TextureImporterAlphaSource.FromInput;
            importer.alphaIsTransparency = false;
            importer.sRGBTexture = true;
            importer.wrapMode = TextureWrapMode.Clamp;
            importer.filterMode = FilterMode.Bilinear;
            importer.SaveAndReimport();
            if (layer.hasGutter)
            {
                var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
                if (texture == null || texture.width != layer.textureWidth || texture.height != layer.textureHeight)
                    throw new InvalidOperationException("Tile texture size differs: " + path);
                // Keep real neighboring texels outside the sprite's visible rect.
                // Bilinear filtering can sample them without drawing overlapping quads.
                var sprite = Sprite.Create(texture,
                    new Rect(layer.spriteX, layer.spriteY, layer.width, layer.height),
                    new Vector2(0.5f, 0.5f), config.pixelsPerUnit, 0, SpriteMeshType.FullRect);
                sprite.name = Path.GetFileNameWithoutExtension(path);
                string spritePath = path + ".sprite.asset";
                if (AssetDatabase.LoadMainAssetAtPath(spritePath) != null) AssetDatabase.DeleteAsset(spritePath);
                AssetDatabase.CreateAsset(sprite, spritePath);
                result[path] = sprite;
            }
            else result[path] = AssetDatabase.LoadAssetAtPath<Sprite>(path);
            if (result[path] == null) throw new InvalidOperationException("Missing imported sprite: " + path);
        }
        return result;
    }

    static string[] SpriteState(GameObject root)
    {
        return root.GetComponentsInChildren<SpriteRenderer>().OrderBy(r => r.sortingOrder)
                   .Select(r => r.sprite == null ? "<null>" : r.sprite.name).ToArray();
    }

    static Color32[] RenderFrame(Camera camera, RenderTexture texture, Config config, string destination)
    {
        camera.Render();
        RenderTexture.active = texture;
        var pixels = new Texture2D(config.width, config.height, TextureFormat.RGBA32, false, false);
        pixels.ReadPixels(new Rect(0, 0, config.width, config.height), 0, 0);
        pixels.Apply();
        // Camera composition over transparent black stores premultiplied RGB.
        // PNG viewers expect straight RGBA; undo that representation change.
        Color32[] straight = pixels.GetPixels32();
        for (int pixel = 0; pixel < straight.Length; pixel++)
        {
            Color32 value = straight[pixel];
            if (value.a != 0)
            {
                value.r = (byte)Math.Min(255, (value.r * 255 + value.a / 2) / value.a);
                value.g = (byte)Math.Min(255, (value.g * 255 + value.a / 2) / value.a);
                value.b = (byte)Math.Min(255, (value.b * 255 + value.a / 2) / value.a);
            }
            else value.r = value.g = value.b = 0;
            straight[pixel] = value;
        }
        pixels.SetPixels32(straight);
        pixels.Apply();
        File.WriteAllBytes(destination, pixels.EncodeToPNG());
        UnityEngine.Object.DestroyImmediate(pixels);
        return straight;
    }

    static SeamCheck[] ValidateSeams(Config config, GameObject instance, Animator animator)
    {
        if (!config.layers.Any(layer => layer.hasGutter)) return new SeamCheck[0];
        if (config.referenceFrames == null || config.referenceFrames.Length != config.frameCount)
            throw new InvalidOperationException("Tiled capture needs whole reference frames for GPU seam validation");
        const float angle = 17.5f, offsetX = 0.37f, offsetY = -0.29f;
        float radians = angle * Mathf.Deg2Rad;
        int width = Mathf.CeilToInt(Mathf.Abs(config.width * Mathf.Cos(radians)) + Mathf.Abs(config.height * Mathf.Sin(radians))) + 8;
        int height = Mathf.CeilToInt(Mathf.Abs(config.width * Mathf.Sin(radians)) + Mathf.Abs(config.height * Mathf.Cos(radians))) + 8;
        var renderConfig = new Config { width = width, height = height };
        Quaternion rotation = Quaternion.Euler(0, 0, angle);
        Vector3 offset = new Vector3(offsetX / config.pixelsPerUnit, offsetY / config.pixelsPerUnit, 0);
        Vector3 center = rotation * new Vector3((config.width / 2f - config.originX) / config.pixelsPerUnit,
                                                (config.originY - config.height / 2f) / config.pixelsPerUnit, 0) + offset;
        instance.transform.SetPositionAndRotation(offset, rotation);
        var cameraObject = new GameObject("TrialSeamCamera");
        var camera = cameraObject.AddComponent<Camera>();
        camera.orthographic = true;
        camera.orthographicSize = height / (2 * config.pixelsPerUnit);
        camera.transform.position = center + new Vector3(0, 0, -10);
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.allowHDR = false;
        camera.allowMSAA = false;
        camera.nearClipPlane = 0.1f;
        camera.farClipPlane = 20;
        var texture = new RenderTexture(width, height, 0, RenderTextureFormat.ARGB32,
                                        RenderTextureReadWrite.sRGB) { antiAliasing = 1 };
        texture.Create();
        camera.targetTexture = texture;
        var uniqueFrames = Enumerable.Range(0, config.frameCount).GroupBy(frame => config.referenceFrames[frame])
                                     .Select(group => group.First()).ToArray();
        var checkedFrames = new[] { uniqueFrames[0], uniqueFrames[uniqueFrames.Length / 2], uniqueFrames[uniqueFrames.Length - 1] }.Distinct();
        var backgrounds = new Dictionary<string, Color32>
        {
            { "black", new Color32(0, 0, 0, 255) }, { "white", new Color32(255, 255, 255, 255) },
            { "dark", new Color32(35, 39, 51, 255) }
        };
        string directory = Path.Combine(config.outputDirectory, "seam_checks");
        Directory.CreateDirectory(directory);
        var checks = new List<SeamCheck>();
        var tiledRenderers = instance.GetComponentsInChildren<SpriteRenderer>();
        foreach (int frame in checkedFrames)
        {
            if (animator != null)
            {
                animator.Play("Loop", 0, (frame + 0.05f) / config.frameCount);
                animator.Update(0);
            }
            var referenceTexture = new Texture2D(2, 2, TextureFormat.RGBA32, false, false)
            { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp };
            if (!referenceTexture.LoadImage(File.ReadAllBytes(config.referenceFrames[frame])))
                throw new InvalidOperationException("Cannot decode whole seam reference");
            if (referenceTexture.width != config.width || referenceTexture.height != config.height)
                throw new InvalidOperationException("Whole seam reference has wrong size");
            var referenceSprite = Sprite.Create(referenceTexture, new Rect(0, 0, config.width, config.height),
                                                new Vector2(0.5f, 0.5f), config.pixelsPerUnit, 0, SpriteMeshType.FullRect);
            var referenceObject = new GameObject("TrialWholeReference");
            referenceObject.transform.SetPositionAndRotation(center, rotation);
            referenceObject.AddComponent<SpriteRenderer>().sprite = referenceSprite;
            foreach (var background in backgrounds)
            {
                camera.backgroundColor = background.Value;
                referenceObject.SetActive(false);
                foreach (var renderer in tiledRenderers) renderer.enabled = true;
                string stem = "frame_" + (frame + 1).ToString("00") + "_" + background.Key;
                Color32[] tiled = RenderFrame(camera, texture, renderConfig, Path.Combine(directory, stem + "_tiled.png"));
                foreach (var renderer in tiledRenderers) renderer.enabled = false;
                referenceObject.SetActive(true);
                Color32[] whole = RenderFrame(camera, texture, renderConfig, Path.Combine(directory, stem + "_whole.png"));
                int maximum = 0;
                long total = 0;
                for (int pixel = 0; pixel < whole.Length; pixel++)
                {
                    int red = Math.Abs(tiled[pixel].r - whole[pixel].r);
                    int green = Math.Abs(tiled[pixel].g - whole[pixel].g);
                    int blue = Math.Abs(tiled[pixel].b - whole[pixel].b);
                    maximum = Math.Max(maximum, Math.Max(red, Math.Max(green, blue)));
                    total += red + green + blue;
                }
                checks.Add(new SeamCheck { frame = frame + 1, width = width, height = height,
                    background = background.Key, rotationDegrees = angle, offsetXPixels = offsetX, offsetYPixels = offsetY,
                    maxChannelError = maximum, meanAbsoluteChannelError = (double)total / (whole.Length * 3) });
                // Tolerance 6/255: the rotated, sub-pixel-offset composite rounds bilinear edge samples slightly
                // differently per tile. Caladbolg (35 gutter tiles, 2026-10-11) measured max 4 on black and a
                // single pixel of 1.2M at 5 on white (dark sword pixel); 4 rejected that invisible difference.
                if (maximum > 6) throw new InvalidOperationException("Rotated bilinear tile seam check differs: " + stem + " maximum " + maximum);
            }
            UnityEngine.Object.DestroyImmediate(referenceObject);
            UnityEngine.Object.DestroyImmediate(referenceSprite);
            UnityEngine.Object.DestroyImmediate(referenceTexture);
        }
        foreach (var renderer in tiledRenderers) renderer.enabled = true;
        instance.transform.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
        RenderTexture.active = null;
        camera.targetTexture = null;
        texture.Release();
        UnityEngine.Object.DestroyImmediate(texture);
        UnityEngine.Object.DestroyImmediate(cameraObject);
        return checks.ToArray();
    }

    static void Validate(Config config, string[] dependencies)
    {
        string bundlePath = Path.Combine(config.outputDirectory, config.bundleName);
        var bundle = AssetBundle.LoadFromFile(bundlePath);
        if (bundle == null) throw new InvalidOperationException("Built bundle could not be loaded");
        var prefab = bundle.LoadAsset<GameObject>(config.prefabName);
        if (prefab == null) throw new InvalidOperationException("Exact prefab name did not resolve");
        var instance = UnityEngine.Object.Instantiate(prefab);
        instance.transform.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
        instance.transform.localScale = Vector3.one;
        var components = instance.GetComponentsInChildren<Component>(true);
        if (components.Any(component => component == null || component is MonoBehaviour))
            throw new InvalidOperationException("Prefab has a missing or custom runtime component");
        var renderers = instance.GetComponentsInChildren<SpriteRenderer>();
        if (renderers.Length != config.layers.Length) throw new InvalidOperationException("Renderer count differs");
        var animator = instance.GetComponent<Animator>();
        bool animated = config.layers.Any(layer => layer.uniqueStates > 1);
        if ((animator != null) != animated) throw new InvalidOperationException("Animator count differs from animated layers");
        if (animator != null) { animator.Rebind(); animator.Update(0); }
        string renderDirectory = Path.Combine(config.outputDirectory, "unity_frames");
        Directory.CreateDirectory(renderDirectory);
        var cameraObject = new GameObject("TrialValidationCamera");
        var camera = cameraObject.AddComponent<Camera>();
        camera.orthographic = true;
        camera.orthographicSize = config.height / (2 * config.pixelsPerUnit);
        camera.transform.position = new Vector3((config.width / 2f - config.originX) / config.pixelsPerUnit,
                                                (config.originY - config.height / 2f) / config.pixelsPerUnit, -10);
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = Color.clear;
        camera.allowHDR = false;
        camera.allowMSAA = false;
        camera.nearClipPlane = 0.1f;
        camera.farClipPlane = 20;
        var texture = new RenderTexture(config.width, config.height, 0, RenderTextureFormat.ARGB32,
                                        RenderTextureReadWrite.sRGB) { antiAliasing = 1 };
        texture.Create();
        camera.targetTexture = texture;
        // Gutter tiles are saved by ImportSprites as "<png>.sprite.asset"; AssetDatabase.CreateAsset renames
        // the sprite to its asset file name, so the bundled sprite is "state_00.png.sprite", not "state_00".
        var expectedSprites = config.layers.Select(layer => layer.frames.Select(
            path => layer.hasGutter ? Path.GetFileName(path) + ".sprite" : Path.GetFileNameWithoutExtension(path)).ToArray()).ToArray();
        var recorded = new List<string[]>();
        Color32[] firstPixels = null;
        for (int frame = 0; frame < config.frameCount; frame++)
        {
            // A small within-frame offset avoids floating point ambiguity at key boundaries.
            if (animator != null)
            {
                animator.Play("Loop", 0, (frame + 0.05f) / config.frameCount);
                animator.Update(0);
            }
            string[] actual = SpriteState(instance);
            for (int layer = 0; layer < config.layers.Length; layer++)
                if (actual[layer] != expectedSprites[layer][frame])
                    throw new InvalidOperationException("Animator sprite mismatch at frame " + frame + " layer " + layer);
            recorded.Add(actual);
            Color32[] rendered = RenderFrame(camera, texture, config,
                Path.Combine(renderDirectory, "frame_" + (frame + 1).ToString("00") + ".png"));
            if (frame == 0) firstPixels = rendered;
        }
        if (animator != null)
        {
            animator.Play("Loop", 0, 1 + 0.05f / config.frameCount);
            animator.Update(0);
        }
        bool loop = SpriteState(instance).SequenceEqual(recorded[0]);
        if (!loop) throw new InvalidOperationException("Animation loop did not restore first state");
        Color32[] loopPixels = RenderFrame(camera, texture, config, Path.Combine(renderDirectory, "loop.png"));
        bool pixelLoop = firstPixels.SequenceEqual(loopPixels);
        if (!pixelLoop) throw new InvalidOperationException("Animation loop pixels differ from first frame");
        for (int restart = 0; restart < 3; restart++)
        {
            if (animator != null) { animator.Rebind(); animator.Update(0); }
            if (!SpriteState(instance).SequenceEqual(recorded[0]))
                throw new InvalidOperationException("Animator restart did not restore first state");
        }
        camera.enabled = false;
        SeamCheck[] seamChecks = ValidateSeams(config, instance, animator);
        var result = new Validation
        {
            status = "passed", unityVersion = Application.unityVersion,
            graphicsDevice = SystemInfo.graphicsDeviceName, colorSpace = QualitySettings.activeColorSpace.ToString(),
            bundle = bundlePath, prefabName = config.prefabName, dependencies = dependencies,
            assetNames = bundle.GetAllAssetNames(), componentTypes = components.Select(c => c.GetType().FullName).Distinct().ToArray(),
            materialShaders = renderers.Select(r => r.sharedMaterial.shader.name).Distinct().ToArray(),
            framesChecked = config.frameCount, renderers = renderers.Length,
            uniqueSprites = config.layers.Sum(layer => layer.uniqueStates), restartChecks = 3,
            animatedLayers = config.layers.Count(layer => layer.uniqueStates > 1),
            staticLayers = config.layers.Count(layer => layer.uniqueStates == 1),
            loopRestoresFirstState = loop, loopRestoresFirstPixels = pixelLoop, customRuntimeRequired = false,
            seamChecks = seamChecks, seamChecksPassed = true,
            animationLength = (float)config.frameCount / config.fps, pixelsPerUnit = config.pixelsPerUnit
        };
        File.WriteAllText(Path.Combine(config.outputDirectory, "unity_validation.json"), JsonUtility.ToJson(result, true));
        RenderTexture.active = null;
        camera.targetTexture = null;
        texture.Release();
        UnityEngine.Object.DestroyImmediate(texture);
        UnityEngine.Object.DestroyImmediate(cameraObject);
        UnityEngine.Object.DestroyImmediate(instance);
        bundle.Unload(true);
    }
}
