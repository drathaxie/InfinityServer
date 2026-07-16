using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.UI;

// Runtime half of the custom-NPC bundle pipeline.
//
// Unity players cannot build new AssetBundles directly; that API lives in UnityEditor. This
// controller captures the thing the player build can see: the fully assembled humanoid NPC after
// the client has dressed it with armor/head/weapon/back assets. The JSON manifest it writes is the
// contract for the editor-side baker that will recreate the prefab and emit one self-contained
// bundle/linkage for Dialogger, apops, and the dialogue-cell avatar path.
public sealed class NpcBakerController : MonoBehaviour
{
    public static NpcBakerController Instance;
    private const KeyCode ToggleKey = KeyCode.F9;
    private const float PanelWidth = 360f;

    private bool _open;
    private string _npcIdText = "361";
    private string _status = "F9 toggles NPC Baker. Use F8 Cutscene Editor to add NPCs; this panel only checks humanoid metadata.";
    private GameObject _preview;
    private bool _loading;
    private GameObject _blocker;
    private Vector2 _scroll;
    private Manifest _last;
    private string _outDir;

    private bool _stylesReady;
    private GUIStyle _panel, _header, _label, _muted, _field, _button, _primary;
    private Texture2D _txPanel, _txField, _txButton, _txPrimary;

    public static void Spawn()
    {
        if (Instance != null) return;
        try
        {
            var go = new GameObject("InfinityNpcBaker");
            DontDestroyOnLoad(go);
            Instance = go.AddComponent<NpcBakerController>();
            InfinityLoaderMod.SafeLog("[npcbake] controller spawned");
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[npcbake] spawn FAILED " + ex); }
    }

    private void Awake()
    {
        string root = AppContext.BaseDirectory;
        if (string.IsNullOrEmpty(root)) root = Directory.GetCurrentDirectory();
        _outDir = Path.Combine(root, "UserData", "Beyond", "npc_baker");
        try { Directory.CreateDirectory(_outDir); } catch { }
    }

    private static bool HasStaffAccess()
    {
        try { return Entity.mainPlayer != null && Entity.mainPlayer.hasAccess(100); }
        catch { return false; }
    }
    private void Update()
    {
        try
        {
            if (Input.GetKeyDown(ToggleKey))
            {
                if (HasStaffAccess()) _open = !_open;
                else { _open = false; InfinityLoaderMod.SafeLog("[npcbake] F9 denied: staff access 100 required"); }
            }
        }
        catch { }
        try { UpdateBlocker(); } catch { }
        if (_preview != null)
        {
            try
            {
                _preview.transform.position = new Vector3(0f, 0f, 0f);
                _preview.transform.localScale = Vector3.one;
            }
            catch { }
        }
    }

    private static Texture2D Tex(Color c)
    {
        var t = new Texture2D(1, 1, TextureFormat.RGBA32, false);
        t.SetPixel(0, 0, c);
        t.Apply();
        t.hideFlags = HideFlags.HideAndDontSave;
        return t;
    }

    private void EnsureStyles()
    {
        if (_stylesReady) return;
        _txPanel = Tex(new Color(0.10f, 0.11f, 0.13f, 0.97f));
        _txField = Tex(new Color(0.18f, 0.19f, 0.23f, 1f));
        _txButton = Tex(new Color(0.25f, 0.27f, 0.33f, 1f));
        _txPrimary = Tex(new Color(0.20f, 0.44f, 0.82f, 1f));
        _panel = new GUIStyle(GUI.skin.box) { padding = new RectOffset(8, 8, 8, 8) };
        _panel.normal.background = _txPanel;
        _header = new GUIStyle(GUI.skin.label) { fontSize = 14, fontStyle = FontStyle.Bold };
        _header.normal.textColor = Color.white;
        _label = new GUIStyle(GUI.skin.label) { fontSize = 12 };
        _label.normal.textColor = new Color(0.86f, 0.89f, 0.94f);
        _muted = new GUIStyle(GUI.skin.label) { fontSize = 11, wordWrap = true };
        _muted.normal.textColor = new Color(0.62f, 0.68f, 0.76f);
        _field = new GUIStyle(GUI.skin.textField) { fontSize = 12, padding = new RectOffset(5, 5, 3, 3) };
        _field.normal.background = _txField;
        _field.normal.textColor = Color.white;
        _field.focused.background = _txField;
        _field.focused.textColor = Color.white;
        _button = ButtonStyle(_txButton, new Color(0.92f, 0.94f, 0.98f));
        _primary = ButtonStyle(_txPrimary, Color.white);
        _stylesReady = true;
    }

    private static GUIStyle ButtonStyle(Texture2D bg, Color text)
    {
        var s = new GUIStyle(GUI.skin.button);
        s.normal.background = bg;
        s.hover.background = bg;
        s.active.background = bg;
        s.normal.textColor = text;
        s.hover.textColor = Color.white;
        s.active.textColor = Color.white;
        s.padding = new RectOffset(7, 7, 4, 4);
        s.fontSize = 12;
        return s;
    }

    private void OnGUI()
    {
        if (!_open || !HasStaffAccess()) return;
        try
        {
            EnsureStyles();
            GUILayout.BeginArea(PanelRect(), _panel);
            GUILayout.Label("NPC Baker", _header);
            GUILayout.Label("Humanoid metadata check only. Add NPCs from the F8 Cutscene Editor; baking will be editor-side.", _muted);
            GUILayout.Space(4);

            GUILayout.BeginHorizontal();
            GUILayout.Label("NPC", _label, GUILayout.Width(32));
            _npcIdText = GUILayout.TextField(_npcIdText ?? "", _field, GUILayout.Width(64));
            if (GUILayout.Button("Check", _primary, GUILayout.Width(64))) LoadNpc();
            if (GUILayout.Button("Clear", _button, GUILayout.Width(64))) ClearPreview();
            GUILayout.EndHorizontal();

            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Capture Manifest", _primary)) CaptureManifest();
            if (GUILayout.Button("Open Log Note", _button, GUILayout.Width(104))) WriteReadme();
            GUILayout.EndHorizontal();

            GUILayout.Label(_status ?? "", _muted);
            GUILayout.Space(6);
            _scroll = GUILayout.BeginScrollView(_scroll);
            DrawLastSummary();
            GUILayout.EndScrollView();
            GUILayout.EndArea();

            var e = Event.current;
            if (e != null && PanelRect().Contains(e.mousePosition) &&
                (e.isMouse || e.type == EventType.ScrollWheel))
                e.Use();
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[npcbake] OnGUI " + ex.Message); }
    }

    private void LoadNpc()
    {
        int id;
        if (!int.TryParse(_npcIdText, out id)) { _status = "NPC id must be numeric."; return; }
        if (_loading) { _status = "already loading..."; return; }
        ClearPreview();
        _status = "loading NPC " + id + "...";
        StartCoroutine(LoadNpcCoroutine(id));
        InfinityLoaderMod.SafeLog("[npcbake] load npc " + id);
    }

    private IEnumerator LoadNpcCoroutine(int id)
    {
        _loading = true;
        string json = null;
        Exception fetchError = null;
        yield return null;
        try { json = FetchMonsterJson(id); }
        catch (Exception ex) { fetchError = ex; }
        if (fetchError != null)
        {
            OnFailed("monster data fetch failed: " + fetchError.Message);
            _loading = false;
            yield break;
        }

        Monbranch mb = null;
        try { mb = ParseFirstMonster(json); }
        catch (Exception ex)
        {
            OnFailed("monster data parse failed: " + ex.Message);
            _loading = false;
            yield break;
        }
        if (mb == null)
        {
            OnFailed("monster " + id + " was not returned by data/GetMonsterData");
            _loading = false;
            yield break;
        }
        if (mb.equippedItems == null || mb.equippedItems.Count == 0)
        {
            OnFailed("monster " + id + " is not an equipped humanoid; this baker captures custom humanoid NPCs only");
            _loading = false;
            yield break;
        }

        _last = new Manifest
        {
            schema = 1,
            npcId = mb.ID,
            rootName = mb.strLinkage ?? "",
            capturedUtc = DateTime.UtcNow.ToString("o"),
            notes = "Metadata check only. Runtime preview loading was disabled because it instantiates into the live game; use the F8 Cutscene Editor NPC path."
        };
        _status = "NPC " + mb.ID + " is an equipped humanoid. Use F8 Cutscene Editor > Add Object > npc to load it.";
        InfinityLoaderMod.SafeLog("[npcbake] checked humanoid npc " + mb.ID + " equips=" + mb.equippedItems.Count);
        _loading = false;
    }

    private static Monbranch ParseFirstMonster(string json)
    {
        var arr = JArray.Parse(json);
        if (arr.Count == 0) return null;
        var obj = arr[0] as JObject;
        if (obj == null) return null;
        NormalizeEquippedItems(obj);
        return obj.ToObject<Monbranch>();
    }

    private static void NormalizeEquippedItems(JObject obj)
    {
        var eqArray = obj["equippedItems"] as JArray;
        if (eqArray == null) return;
        var eqObj = new JObject();
        foreach (var tok in eqArray)
        {
            var item = tok as JObject;
            if (item == null) continue;
            string spot = Convert.ToString(item["EquipSpot"]);
            if (!string.IsNullOrEmpty(spot)) eqObj[spot] = item;
        }
        obj["equippedItems"] = eqObj;
    }
    private static string FetchMonsterJson(int id)
    {
        string baseUrl = Main.WebApiURL;
        if (string.IsNullOrEmpty(baseUrl)) baseUrl = "https://130-162-189-229.sslip.io/";
        if (!baseUrl.EndsWith("/")) baseUrl += "/";
        using (var wc = new WebClient())
            return wc.DownloadString(baseUrl + "data/GetMonsterData?ids=" + id);
    }

    private void OnLoaded(GameObject asset)
    {
        try
        {
            _preview = asset;
            if (_preview != null)
            {
                _preview.name = "NpcBakerPreview_" + (_npcIdText ?? "");
                DontDestroyOnLoad(_preview);
                EnsureCameraFocus(_preview);
                StripRuntimeInteraction(_preview);
            }
            _status = "loaded. Capture Manifest will snapshot the dressed hierarchy.";
            InfinityLoaderMod.SafeLog("[npcbake] loaded preview " + SafeName(_preview));
        }
        catch (Exception ex) { _status = "load post-step failed: " + ex.Message; }
    }

    private void OnFailed(string error)
    {
        _status = "load failed: " + error;
        InfinityLoaderMod.SafeLog("[npcbake] load failed " + error);
    }

    private void CaptureManifest()
    {
        if (_preview == null) { _status = "F9 no longer spawns NPCs into the game. Use F8 Cutscene Editor Add Object > npc."; return; }
        int id;
        int.TryParse(_npcIdText, out id);
        try
        {
            var m = BuildManifest(_preview, id);
            string stamp = DateTime.UtcNow.ToString("yyyyMMdd_HHmmss");
            string file = Path.Combine(_outDir, "npc_" + id + "_" + stamp + ".json");
            File.WriteAllText(file, JsonConvert.SerializeObject(m, Formatting.Indented));
            _last = m;
            _status = "captured " + m.objects.Count + " object(s), " + m.renderers.Count
                + " renderer(s), " + m.animators.Count + " animator(s): " + file;
            InfinityLoaderMod.SafeLog("[npcbake] wrote " + file);
        }
        catch (Exception ex)
        {
            _status = "capture failed: " + ex.Message;
            InfinityLoaderMod.SafeLog("[npcbake] capture failed " + ex);
        }
    }

    private Manifest BuildManifest(GameObject root, int npcId)
    {
        var m = new Manifest
        {
            schema = 1,
            npcId = npcId,
            rootName = SafeName(root),
            capturedUtc = DateTime.UtcNow.ToString("o"),
            notes = "Runtime capture of a dressed NPC. Build the final .unity3d in a UnityEditor baker; player builds cannot call BuildPipeline.",
        };

        var transforms = root.GetComponentsInChildren<Transform>(true);
        foreach (var t in transforms)
        {
            m.objects.Add(new ObjectEntry
            {
                path = PathOf(root.transform, t),
                active = t.gameObject.activeSelf,
                layer = t.gameObject.layer,
                localPosition = Vec(t.localPosition),
                localRotation = Vec(t.localEulerAngles),
                localScale = Vec(t.localScale),
                components = ComponentNames(t.gameObject),
            });
        }

        var renderers = root.GetComponentsInChildren<Renderer>(true);
        foreach (var r in renderers)
        {
            var e = new RendererEntry
            {
                path = PathOf(root.transform, r.transform),
                type = r.GetType().FullName,
                enabled = r.enabled,
                sortingLayer = r.sortingLayerName,
                sortingOrder = r.sortingOrder,
                materialNames = MaterialNames(r),
                shaderNames = ShaderNames(r),
            };
            var sr = r as SpriteRenderer;
            if (sr != null && sr.sprite != null)
            {
                e.sprite = sr.sprite.name;
                e.texture = sr.sprite.texture != null ? sr.sprite.texture.name : "";
                e.color = ColorUtility.ToHtmlStringRGBA(sr.color);
            }
            m.renderers.Add(e);
        }

        var animators = root.GetComponentsInChildren<Animator>(true);
        foreach (var a in animators)
        {
            m.animators.Add(new AnimatorEntry
            {
                path = PathOf(root.transform, a.transform),
                enabled = a.enabled,
                controller = a.runtimeAnimatorController != null ? a.runtimeAnimatorController.name : "",
                avatar = a.avatar != null ? a.avatar.name : "",
                applyRootMotion = a.applyRootMotion,
            });
        }

        var clips = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var a in animators)
        {
            var c = a.runtimeAnimatorController;
            if (c == null || c.animationClips == null) continue;
            foreach (var clip in c.animationClips)
                if (clip != null && !string.IsNullOrEmpty(clip.name)) clips.Add(clip.name);
        }
        m.animationClips.AddRange(clips);
        return m;
    }

    private void DrawLastSummary()
    {
        if (_last == null)
        {
            GUILayout.Label("No capture yet.", _muted);
            return;
        }
        GUILayout.Label("Last Capture", _header);
        GUILayout.Label("NPC " + _last.npcId + " / " + _last.rootName, _label);
        GUILayout.Label("Objects: " + _last.objects.Count, _label);
        GUILayout.Label("Renderers: " + _last.renderers.Count, _label);
        GUILayout.Label("Animators: " + _last.animators.Count, _label);
        GUILayout.Label("Clips: " + _last.animationClips.Count, _label);
        GUILayout.Space(4);
        int shown = 0;
        foreach (var r in _last.renderers)
        {
            if (shown++ >= 20) { GUILayout.Label("...", _muted); break; }
            GUILayout.Label(r.path + "  [" + r.type + "]  " + r.sprite, _muted);
        }
    }

    private void WriteReadme()
    {
        try
        {
            string path = Path.Combine(_outDir, "README.txt");
            File.WriteAllText(path,
                "Infinity NPC Baker runtime capture\n\n"
                + "1. F9 in-game, load a humanoid/custom NPC id.\n"
                + "2. Capture Manifest writes npc_<id>_<timestamp>.json.\n"
                + "3. The next pipeline step is a UnityEditor project that reads this manifest,\n"
                + "   reconstructs the dressed prefab from the same source bundles, and emits a\n"
                + "   single AssetBundle with a stable prefab linkage.\n\n"
                + "Runtime Unity players cannot build AssetBundles; BuildPipeline is editor-only.\n");
            _status = "wrote " + path;
        }
        catch (Exception ex) { _status = "README write failed: " + ex.Message; }
    }

    private void ClearPreview()
    {
        try { if (_preview != null) Destroy(_preview); } catch { }
        _preview = null;
    }

    private static Rect PanelRect()
    {
        return new Rect(Screen.width - PanelWidth, 0f, PanelWidth, Screen.height);
    }

    private void UpdateBlocker()
    {
        if (_blocker == null) CreateBlocker();
        if (_blocker != null) _blocker.SetActive(_open);
    }

    private void CreateBlocker()
    {
        var go = new GameObject("InfinityNpcBakerClickBlocker");
        DontDestroyOnLoad(go);
        var canvas = go.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        canvas.sortingOrder = 32760;
        go.AddComponent<GraphicRaycaster>();

        var panel = new GameObject("PanelBlocker");
        panel.transform.SetParent(go.transform, false);
        var rt = panel.AddComponent<RectTransform>();
        rt.anchorMin = new Vector2(1f, 0f);
        rt.anchorMax = new Vector2(1f, 1f);
        rt.pivot = new Vector2(1f, 0.5f);
        rt.sizeDelta = new Vector2(PanelWidth, 0f);
        rt.anchoredPosition = Vector2.zero;
        var img = panel.AddComponent<Image>();
        img.color = new Color(0f, 0f, 0f, 0f);
        img.raycastTarget = true;
        _blocker = go;
        _blocker.SetActive(false);
    }

    private static void EnsureCameraFocus(GameObject asset)
    {
        if (asset != null && asset.transform.Find("CameraFocus") == null)
        {
            var cf = new GameObject("CameraFocus");
            cf.transform.SetParent(asset.transform, false);
            cf.transform.localPosition = Vector3.zero;
        }
    }

    private static void StripRuntimeInteraction(GameObject root)
    {
        if (root == null) return;
        foreach (var c in root.GetComponentsInChildren<Collider2D>(true)) Destroy(c);
        foreach (var z in root.GetComponentsInChildren<ZOffset>(true)) Destroy(z);
        foreach (var w in root.GetComponentsInChildren<Walk>(true)) Destroy(w);
    }

    private static string[] ComponentNames(GameObject go)
    {
        var cs = go.GetComponents<Component>();
        var names = new List<string>();
        foreach (var c in cs) names.Add(c == null ? "(missing)" : c.GetType().FullName);
        return names.ToArray();
    }

    private static string[] MaterialNames(Renderer r)
    {
        var outv = new List<string>();
        try
        {
            foreach (var m in r.sharedMaterials)
                outv.Add(m != null ? m.name : "");
        }
        catch { }
        return outv.ToArray();
    }

    private static string[] ShaderNames(Renderer r)
    {
        var outv = new List<string>();
        try
        {
            foreach (var m in r.sharedMaterials)
                outv.Add(m != null && m.shader != null ? m.shader.name : "");
        }
        catch { }
        return outv.ToArray();
    }

    private static string PathOf(Transform root, Transform t)
    {
        if (root == null || t == null) return "";
        if (t == root) return root.name;
        var parts = new List<string>();
        var cur = t;
        while (cur != null)
        {
            parts.Add(cur.name);
            if (cur == root) break;
            cur = cur.parent;
        }
        parts.Reverse();
        return string.Join("/", parts.ToArray());
    }

    private static float[] Vec(Vector3 v) { return new[] { v.x, v.y, v.z }; }
    private static string SafeName(UnityEngine.Object o) { return o != null ? o.name : ""; }

    private sealed class Manifest
    {
        public int schema;
        public int npcId;
        public string rootName;
        public string capturedUtc;
        public string notes;
        public List<ObjectEntry> objects = new List<ObjectEntry>();
        public List<RendererEntry> renderers = new List<RendererEntry>();
        public List<AnimatorEntry> animators = new List<AnimatorEntry>();
        public List<string> animationClips = new List<string>();
    }

    private sealed class ObjectEntry
    {
        public string path;
        public bool active;
        public int layer;
        public float[] localPosition;
        public float[] localRotation;
        public float[] localScale;
        public string[] components;
    }

    private sealed class RendererEntry
    {
        public string path;
        public string type;
        public bool enabled;
        public string sortingLayer;
        public int sortingOrder;
        public string sprite;
        public string texture;
        public string color;
        public string[] materialNames;
        public string[] shaderNames;
    }

    private sealed class AnimatorEntry
    {
        public string path;
        public bool enabled;
        public string controller;
        public string avatar;
        public bool applyRootMotion;
    }
}
