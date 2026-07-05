using System;
using System.Collections;
using System.Net;
using System.Text;
using Pixelplacement;   // Singleton<Game>.Instance (the layer-visibility toggles)
using UnityEngine;

// ---------------------------------------------------------------------------
// In-client Dialogger editor — Phase 1: the render-drive proof.
//
// AE's own Dialogger EDITOR (Dialogger_EditorManager) is prefab/scene-driven and those
// assets were stripped from the playtest build, so it can't be revived. But the RENDERER
// — Dialogger_Manager — shipped and works (it's what /cutscene plays). This drives that
// shipped renderer to display an arbitrary page of a saved cutscene, under our control.
//
// The trick: set Dialogger_Manager.editor = true before LoadJson. In that mode the manager
//   * skips the player-block / UI-hide that StartCutscene does for playback, and
//   * does NOT arm page Timers (RunCommands: `if (!editor || respectTimer)`), so it renders
//     a page and HOLDS instead of auto-advancing.
// With editor=true and dem==null, the manager's editor hooks are all either guarded by
// `dem != null` or only reached on an asset-load FAILURE, so a good scene renders cleanly.
//
// So the whole preview is: dm.editor=true; dm.LoadJson(raw)  (loads frame-0 assets, holds);
// wait on dm.IsAssetLoadInProgress; dm.LoadPage(n) to render any page. Real bundles, 1=1.
//
// Later phases layer the authoring UI (object list, inspectors, timeline, save) on top of
// this same in-memory dm.dData. Phase 1 is just: load an id, page through it, see it render.
//
// Toggle with F8. IMGUI panel (no prefabs / no shipped bundle needed).
// ---------------------------------------------------------------------------
public class CutsceneEditorController : MonoBehaviour
{
    public static CutsceneEditorController Instance;
    private const KeyCode ToggleKey = KeyCode.F8;

    private bool _open;
    private string _idText = "30";
    private string _status = "F8 toggles this panel. Enter a cutscene id and Load.";
    private int _page;
    private bool _loaded;
    private bool _driving;          // we currently own Dialogger_Manager.instance
    private bool _prevEditorFlag;
    private float _savedOrtho = -1f;

    // Phase 2 — object tree + property inspector
    private string _selKind, _selId;                 // selected tree row: kind in {obj,box,cam}
    private Vector2 _treeScroll, _inspScroll;
    private string _bufSig = "";                      // (kind:id:page) the field buffers were loaded for
    private readonly System.Collections.Generic.Dictionary<string, string> _buf =
        new System.Collections.Generic.Dictionary<string, string>();
    private string _timerSec = "2";                  // Phase 3: add-timer duration field
    private string _frameGoto = "";                  // "Go To" page field
    private string _addType = "npc";                 // Add-object: selected type
    private string _addInput = "";                   // Add-object: id/name entry

    // ---- styling (built once, lazily, inside OnGUI where GUI.skin is valid) ----
    private bool _stylesReady;
    private GUIStyle _sPanel, _sHeader, _sSection, _sLabel, _sMuted, _sField,
                     _sBtn, _sBtnBlue, _sBtnYellow, _sBtnOrange, _sRow, _sRowSel, _sEye, _sBadge;
    private Texture2D _txDark, _txPanel, _txRow, _txRowSel, _txBtn, _txBtnH, _txBlue, _txYellow, _txOrange;

    private static Texture2D Tex(Color c)
    {
        var t = new Texture2D(1, 1, TextureFormat.RGBA32, false);
        t.SetPixel(0, 0, c); t.Apply(); t.hideFlags = HideFlags.HideAndDontSave;
        return t;
    }

    private GUIStyle MkBtn(Texture2D bg, Texture2D hover, Color text, TextAnchor align = TextAnchor.MiddleCenter)
    {
        var s = new GUIStyle(GUI.skin.button);
        s.normal.background = bg; s.normal.textColor = text;
        s.hover.background = hover; s.hover.textColor = Color.white;
        s.active.background = hover; s.active.textColor = Color.white;
        s.focused.background = bg; s.focused.textColor = text;
        s.border = new RectOffset(2, 2, 2, 2); s.margin = new RectOffset(2, 2, 2, 2);
        s.padding = new RectOffset(6, 6, 3, 3); s.fontSize = 12; s.alignment = align;
        return s;
    }

    private void EnsureStyles()
    {
        if (_stylesReady) return;
        _txDark = Tex(new Color(0.09f, 0.09f, 0.11f, 0.97f));
        _txPanel = Tex(new Color(0.14f, 0.14f, 0.17f, 0.99f));
        _txRow = Tex(new Color(0.20f, 0.20f, 0.24f, 1f));
        _txRowSel = Tex(new Color(0.18f, 0.42f, 0.84f, 1f));
        _txBtn = Tex(new Color(0.24f, 0.24f, 0.29f, 1f));
        _txBtnH = Tex(new Color(0.33f, 0.35f, 0.44f, 1f));
        _txBlue = Tex(new Color(0.20f, 0.44f, 0.82f, 1f));
        _txYellow = Tex(new Color(0.86f, 0.72f, 0.12f, 1f));
        _txOrange = Tex(new Color(0.80f, 0.46f, 0.16f, 1f));

        _sPanel = new GUIStyle(GUI.skin.box) { border = new RectOffset(4, 4, 4, 4), padding = new RectOffset(8, 8, 8, 8) };
        _sPanel.normal.background = _txDark;
        _sHeader = new GUIStyle(GUI.skin.label) { fontSize = 14, fontStyle = FontStyle.Bold };
        _sHeader.normal.textColor = Color.white;
        _sSection = new GUIStyle(GUI.skin.label) { fontSize = 11, fontStyle = FontStyle.Bold, padding = new RectOffset(2, 2, 6, 2) };
        _sSection.normal.textColor = new Color(0.55f, 0.72f, 1f);
        _sLabel = new GUIStyle(GUI.skin.label) { fontSize = 12 };
        _sLabel.normal.textColor = new Color(0.82f, 0.86f, 0.92f);
        _sMuted = new GUIStyle(GUI.skin.label) { fontSize = 11, wordWrap = true };
        _sMuted.normal.textColor = new Color(0.60f, 0.65f, 0.72f);
        _sField = new GUIStyle(GUI.skin.textField) { fontSize = 12, padding = new RectOffset(5, 5, 3, 3) };
        _sField.normal.background = _txRow; _sField.normal.textColor = Color.white;
        _sField.focused.background = _txRow; _sField.focused.textColor = Color.white;
        _sBtn = MkBtn(_txBtn, _txBtnH, new Color(0.90f, 0.92f, 0.96f));
        _sBtnBlue = MkBtn(_txBlue, _txBtnH, Color.white);
        _sBtnYellow = MkBtn(_txYellow, _txBtnH, new Color(0.10f, 0.10f, 0.10f));
        _sBtnOrange = MkBtn(_txOrange, _txBtnH, Color.white);
        _sRow = MkBtn(_txRow, _txBtnH, new Color(0.88f, 0.90f, 0.95f), TextAnchor.MiddleLeft);
        _sRow.padding = new RectOffset(8, 4, 4, 4);
        _sRowSel = MkBtn(_txRowSel, _txRowSel, Color.white, TextAnchor.MiddleLeft);
        _sRowSel.padding = new RectOffset(8, 4, 4, 4);
        _sEye = MkBtn(_txBtn, _txBtnH, new Color(0.9f, 0.95f, 1f)); _sEye.fontSize = 13; _sEye.padding = new RectOffset(0, 0, 0, 0);
        _sBadge = new GUIStyle(GUI.skin.label) { fontSize = 9, fontStyle = FontStyle.Bold };
        _sBadge.normal.textColor = new Color(1f, 0.80f, 0.30f);
        _stylesReady = true;
    }

    /// <summary>Create the persistent controller GameObject (idempotent). Called from Boot()
    /// and, as a fallback, once chat is up — whichever fires first while Unity is ready.</summary>
    public static void Spawn()
    {
        if (Instance != null) return;
        try
        {
            var go = new GameObject("InfinityCutsceneEditor");
            DontDestroyOnLoad(go);
            Instance = go.AddComponent<CutsceneEditorController>();
            InfinityLoaderMod.SafeLog("[cutedit] controller spawned");
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] spawn FAILED " + ex); }
    }

    private void Update()
    {
        try { if (Input.GetKeyDown(ToggleKey)) _open = !_open; }
        catch { }
    }

    private static readonly string[] _cats = { "Actors", "BGs", "Boxes" };

    private void OnGUI()
    {
        if (!_open) return;
        try
        {
            EnsureStyles();
            DrawLeftPanel();
            if (_loaded) DrawBottomBar();
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] OnGUI " + ex.Message); }
    }

    // ---- left panel: header + object list + inspector ------------------------
    private void DrawLeftPanel()
    {
        const float w = 276f;
        GUILayout.BeginArea(new Rect(0, 0, w, Screen.height), _sPanel);

        GUILayout.Label("Cutscene Editor", _sHeader);
        GUILayout.BeginHorizontal();
        GUILayout.Label("id", _sMuted, GUILayout.Width(14));
        _idText = GUILayout.TextField(_idText ?? "", _sField, GUILayout.Width(46));
        if (GUILayout.Button("Load", _sBtnBlue, GUILayout.Width(50))) StartCoroutine(LoadAndRender());
        if (GUILayout.Button("Close", _sBtn, GUILayout.Width(50))) CloseEditor();
        GUILayout.EndHorizontal();

        if (_loaded)
        {
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Save", _sBtnYellow, GUILayout.Width(74))) SaveScene(false);
            if (GUILayout.Button("Save as NEW", _sBtn)) SaveScene(true);
            GUILayout.EndHorizontal();
        }
        GUILayout.Label(_status ?? "", _sMuted);

        if (_loaded)
        {
            GUILayout.Space(4);
            float listH = Mathf.Max(120f, (Screen.height - 210f) * 0.45f);
            _treeScroll = GUILayout.BeginScrollView(_treeScroll, GUILayout.Height(listH));
            DrawList();
            GUILayout.EndScrollView();

            GUILayout.Space(4);
            DrawAddObject();

            GUILayout.Space(4);
            _inspScroll = GUILayout.BeginScrollView(_inspScroll);
            DrawInspectorBody();
            GUILayout.EndScrollView();
        }
        GUILayout.EndArea();
    }

    private void DrawList()
    {
        GUILayout.Label("CAMERA", _sSection);
        ListRow("cam", "", "Camera", false);
        var roster = Roster();
        foreach (var cat in _cats)
        {
            bool header = false;
            foreach (var o in roster)
            {
                if (Category(o) != cat) continue;
                if (!header) { GUILayout.Label(cat.ToUpper(), _sSection); header = true; }
                ListRow(o.kind, o.id, o.name, o.kind == "obj" && FindIdx(_page, "Actor{" + o.id + "|") >= 0);
            }
        }
    }

    private void ListRow(string kind, string id, string name, bool animated)
    {
        bool sel = _selKind == kind && _selId == id;
        bool isCam = kind == "cam";
        GUILayout.BeginHorizontal();
        string label = isCam ? "Camera" : ("#" + id + "  " + name);
        if (GUILayout.Button(label, sel ? _sRowSel : _sRow)) { _selKind = kind; _selId = id; _bufSig = ""; }
        if (animated) GUILayout.Label("AN", _sBadge, GUILayout.Width(16));
        if (!isCam)
        {
            bool vis = IsVisibleOnPage(kind, id);
            if (GUILayout.Button(vis ? "◉" : "○", _sEye, GUILayout.Width(24))) ToggleVisible(kind, id);
        }
        GUILayout.EndHorizontal();
    }

    private bool IsVisibleOnPage(string kind, string id)
    {
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, (kind == "box" ? "Box{" : "Object{") + id + "|");
        if (i < 0) return false;
        var f = Body(dm.dData.frames[_page][i]); int vi = kind == "box" ? 4 : 1;
        return f != null && f.Length > vi && f[vi] != "0";
    }

    private void ToggleVisible(string kind, string id)
    {
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, (kind == "box" ? "Box{" : "Object{") + id + "|");
        if (i < 0) { _status = "not on this page — select it and Add"; return; }
        var f = Body(dm.dData.frames[_page][i]); int vi = kind == "box" ? 4 : 1;
        if (f == null || f.Length <= vi) return;
        f[vi] = f[vi] == "0" ? "1" : "0";
        dm.dData.frames[_page][i] = Rebuild(kind == "box" ? "Box" : "Object", f);
        try { dm.LoadPage(_page); } catch { }
        _bufSig = "";
    }

    // ---- inspector (AE-style: Center + nudge arrows, per-field Set, slider) ---
    private void DrawInspectorBody()
    {
        if (_selKind == null) { GUILayout.Label("Select an object above.", _sMuted); return; }
        string sig = _selKind + ":" + _selId + ":" + _page;
        if (sig != _bufSig) { LoadBuf(); _bufSig = sig; }

        string title = _selKind == "cam" ? "CAMERA" : (_selKind == "box" ? "BOX " + _selId : "OBJECT #" + _selId);
        GUILayout.Label(title + "   ·   page " + _page, _sSection);

        if (_buf.ContainsKey("__missing"))
        {
            GUILayout.Label("Not on this page.", _sMuted);
            if (GUILayout.Button("Add to this page", _sBtnBlue)) { AddToPage(); _bufSig = ""; }
            return;
        }

        if (_selKind == "obj")
        {
            NudgeRow(5, 6);
            PosRow(5, 6);
            SliderRow("Rotation", 10, -180f, 180f);
            FieldSet("Scale", 4); FieldSet("Z Order", 3); FieldSet("Tween", 8); FieldSet("Tint", 9);
            GUILayout.BeginHorizontal(); ToggleBtn("Visible", 1, "1", "0"); ToggleBtn("Face L", 2, "-1", "1"); GUILayout.EndHorizontal();
        }
        else if (_selKind == "box")
        {
            if (_buf.ContainsKey("speaker")) { GUILayout.Label("Speaker", _sLabel); _buf["speaker"] = GUILayout.TextField(_buf["speaker"] ?? "", _sField); }
            if (_buf.ContainsKey("f8")) { GUILayout.Label("Dialog text", _sLabel); _buf["f8"] = GUILayout.TextArea(_buf["f8"] ?? "", _sField, GUILayout.Height(52)); }
            if (GUILayout.Button("Apply text", _sBtnBlue)) ApplyBuf();
            GUILayout.Space(3);
            NudgeRow(1, 2);
            PosRow(1, 2);
            FieldSet("Scale", 3); FieldSet("Font size", 11);
            GUILayout.BeginHorizontal(); ToggleBtn("Visible", 4, "1", "0"); GUILayout.EndHorizontal();
        }
        else // cam
        {
            int len = _buf.ContainsKey("__len") ? int.Parse(_buf["__len"]) : 0;
            if (len >= 7) { FieldSet("Zoom", 0); PosRow(1, 2); FieldSet("Rotation", 4); FieldSet("Tween", 5); }
            else { PosRow(0, 1); FieldSet("Scale", 3); FieldSet("Speed", 4); }
        }
    }

    private void NudgeRow(int xi, int yi)
    {
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Center", _sBtn, GUILayout.Width(60))) { _buf["f" + xi] = "0"; _buf["f" + yi] = "0"; ApplyBuf(); }
        if (GUILayout.Button("◀", _sBtn, GUILayout.Width(28))) Nudge(xi, -20);
        if (GUILayout.Button("▶", _sBtn, GUILayout.Width(28))) Nudge(xi, +20);
        if (GUILayout.Button("▲", _sBtn, GUILayout.Width(28))) Nudge(yi, +20);
        if (GUILayout.Button("▼", _sBtn, GUILayout.Width(28))) Nudge(yi, -20);
        GUILayout.EndHorizontal();
    }

    private void Nudge(int idx, float delta)
    {
        string k = "f" + idx; if (!_buf.ContainsKey(k)) return;
        float v; float.TryParse(_buf[k], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out v);
        _buf[k] = (v + delta).ToString(System.Globalization.CultureInfo.InvariantCulture);
        ApplyBuf();
    }

    private void PosRow(int xi, int yi)
    {
        GUILayout.BeginHorizontal();
        GUILayout.Label("Position", _sLabel, GUILayout.Width(60));
        if (_buf.ContainsKey("f" + xi)) _buf["f" + xi] = GUILayout.TextField(_buf["f" + xi], _sField, GUILayout.Width(66));
        if (_buf.ContainsKey("f" + yi)) _buf["f" + yi] = GUILayout.TextField(_buf["f" + yi], _sField, GUILayout.Width(66));
        if (GUILayout.Button("Set", _sBtnBlue, GUILayout.Width(40))) ApplyBuf();
        GUILayout.EndHorizontal();
    }

    private void FieldSet(string label, int idx)
    {
        string k = "f" + idx; if (!_buf.ContainsKey(k)) return;
        GUILayout.BeginHorizontal();
        GUILayout.Label(label, _sLabel, GUILayout.Width(60));
        _buf[k] = GUILayout.TextField(_buf[k] ?? "", _sField, GUILayout.Width(98));
        if (GUILayout.Button("Set", _sBtnBlue, GUILayout.Width(40))) ApplyBuf();
        GUILayout.EndHorizontal();
    }

    private void SliderRow(string label, int idx, float min, float max)
    {
        string k = "f" + idx; if (!_buf.ContainsKey(k)) return;
        float v; float.TryParse(_buf[k], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out v);
        GUILayout.BeginHorizontal();
        GUILayout.Label(label, _sLabel, GUILayout.Width(60));
        float nv = GUILayout.HorizontalSlider(v, min, max, GUILayout.Width(96));
        _buf[k] = GUILayout.TextField(_buf[k] ?? "", _sField, GUILayout.Width(44));
        if (GUILayout.Button("Set", _sBtnBlue, GUILayout.Width(40))) ApplyBuf();
        GUILayout.EndHorizontal();
        if (Mathf.Abs(nv - v) > 0.05f) { _buf[k] = nv.ToString("F1", System.Globalization.CultureInfo.InvariantCulture); ApplyBuf(); }
    }

    private void ToggleBtn(string label, int idx, string onVal, string offVal)
    {
        string k = "f" + idx; if (!_buf.ContainsKey(k)) return;
        bool cur = _buf[k] == onVal;
        if (GUILayout.Button((cur ? "✓ " : "  ") + label, cur ? _sBtnOrange : _sBtn)) { _buf[k] = cur ? offVal : onVal; ApplyBuf(); }
    }

    // ---- add a NEW asset to the scene (Load on frame 0 + Object on this page) --
    private static readonly string[] _addTypes = { "actor", "npc", "player", "bg" };
    private static string AddHint(string t)
    {
        if (t == "actor") return "bundleId,PrefabName  (e.g. 66131,actor-veddrian)";
        if (t == "npc") return "monster / npc id  (e.g. 262)";
        if (t == "bg") return "image filename";
        return "";
    }

    private void DrawAddObject()
    {
        GUILayout.Label("ADD OBJECT", _sSection);
        GUILayout.BeginHorizontal();
        foreach (var t in _addTypes)
            if (GUILayout.Button(t, _addType == t ? _sBtnBlue : _sBtn, GUILayout.Width(58))) _addType = t;
        GUILayout.EndHorizontal();
        if (_addType != "player")
        {
            _addInput = GUILayout.TextField(_addInput ?? "", _sField);
            GUILayout.Label(AddHint(_addType), _sMuted);
        }
        if (GUILayout.Button("+ Add " + _addType, _sBtnBlue))
        {
            string link = _addType == "player" ? "" : (_addInput ?? "").Trim();
            if (_addType == "player" || link.Length > 0) StartCoroutine(AddObject(link, _addType));
            else _status = "enter a value first";
        }
    }

    private IEnumerator AddObject(string link, string type)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) yield break;
        int id = dm.dData.idCount; dm.dData.idCount = id + 1;
        dm.dData.frames[0].Add("Load{" + id + "|" + link + "|" + type + "}");
        // load the asset live: ReadCommand_Load only acts when pageNumber==0, so flip it briefly
        int savedPage = dm.pageNumber;
        dm.pageNumber = 0;
        try { dm.ReadCommand_Load(id, link, type); }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] add load err " + ex.Message); }
        dm.pageNumber = savedPage;
        _status = "loading " + type + " #" + id + "…";
        float t = 0f; while (t < 3f) { if (dm.IsAssetLoadInProgress) break; t += Time.deltaTime; yield return null; }
        float t2 = 0f; while (dm.IsAssetLoadInProgress && t2 < 30f) { t2 += Time.deltaTime; yield return null; }
        yield return new WaitForSeconds(0.3f);
        if (_page >= 1) dm.dData.frames[_page].Add("Object{" + id + "|1|1|0|1|0|0|0|-1 0|FFFFFFFF|0|0|1}");
        try { dm.LoadPage(_page); } catch { }
        _selKind = "obj"; _selId = id.ToString(); _bufSig = "";
        _status = "added " + type + " #" + id + " — position it in the inspector";
        InfinityLoaderMod.SafeLog("[cutedit] added " + type + " #" + id + " (" + link + ")");
    }

    // ---- bottom toolbar: pager + page/authoring actions ----------------------
    private void DrawBottomBar()
    {
        const float x = 276f, h = 78f;
        int total = FrameCount();
        GUILayout.BeginArea(new Rect(x, Screen.height - h, Screen.width - x, h), _sPanel);

        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Setup", _sBtn, GUILayout.Width(52))) Goto(0);
        if (GUILayout.Button("First", _sBtn, GUILayout.Width(48))) Goto(1);
        if (GUILayout.Button("Back", _sBtn, GUILayout.Width(48))) Goto(_page - 1);
        _frameGoto = GUILayout.TextField(_frameGoto ?? "", _sField, GUILayout.Width(38));
        if (GUILayout.Button("Go To", _sBtn, GUILayout.Width(48))) { int g; if (int.TryParse(_frameGoto, out g)) Goto(g); }
        if (GUILayout.Button("Next", _sBtn, GUILayout.Width(48))) Goto(_page + 1);
        if (GUILayout.Button("End", _sBtn, GUILayout.Width(48))) Goto(total - 1);
        if (GUILayout.Button("New page", _sBtnYellow, GUILayout.Width(80))) ClonePage();
        GUILayout.Label("  " + _page + " / " + (total - 1), _sHeader, GUILayout.Width(84));
        GUILayout.FlexibleSpace();
        GUILayout.EndHorizontal();

        GUILayout.Space(3);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Add Bubble", _sBtn, GUILayout.Width(90))) AddBubble();
        if (GUILayout.Button("Blank Page", _sBtn, GUILayout.Width(84))) BlankPage();
        if (GUILayout.Button("Delete Page", _sBtnOrange, GUILayout.Width(92))) DeletePage();
        if (GUILayout.Button("Add Timer", _sBtn, GUILayout.Width(78))) AddCmd("Timer{" + (_timerSec ?? "2") + "}");
        _timerSec = GUILayout.TextField(_timerSec ?? "2", _sField, GUILayout.Width(32));
        GUILayout.Label("s", _sMuted, GUILayout.Width(10));
        if (GUILayout.Button("Fade To", _sBtn, GUILayout.Width(62))) AddCmd("FadeToBlack");
        if (GUILayout.Button("Fade From", _sBtn, GUILayout.Width(72))) AddCmd("FadeFromBlack");
        GUILayout.FlexibleSpace();
        GUILayout.EndHorizontal();

        GUILayout.EndArea();
    }

    private static int FrameCount()
    {
        var dm = Dialogger_Manager.instance;
        if (dm != null && dm.dData != null && dm.dData.frames != null) return dm.dData.frames.Count;
        return 0;
    }

    private IEnumerator LoadAndRender()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null) { _status = "Dialogger_Manager not in the scene yet — enter a map first."; yield break; }

        string json;
        try { json = Fetch(_idText); }
        catch (Exception ex) { _status = "fetch failed: " + ex.Message; yield break; }
        if (string.IsNullOrEmpty(json) || json.IndexOf("\"frames\"", StringComparison.Ordinal) < 0)
        { _status = "cutscene " + _idText + " not found / empty."; yield break; }

        InfinityLoaderMod.SafeLog("[cutedit] loading id " + _idText + " (" + json.Length + " bytes)");
        if (!_driving) { _prevEditorFlag = dm.editor; _savedOrtho = (Camera.main != null) ? Camera.main.orthographicSize : -1f; _driving = true; }
        dm.editor = true;                       // hold pages, don't arm timers, skip player-block
        try { dm.LoadJson(json); }              // deserialize (handles &lt;/&gt;) + kick off frame-0 asset loads
        catch (Exception ex) { _status = "LoadJson error: " + ex.Message; InfinityLoaderMod.SafeLog("[cutedit] LoadJson err " + ex); yield break; }

        int total = FrameCount();
        // The frame-0 loads are async and DON'T register until AssetBundleDataLoader.Load's metadata
        // callback creates the BundleLoaders — so IsAssetLoadInProgress is briefly false right after
        // LoadJson. Wait for loading to START (grace), then to FINISH, else we'd render before any
        // actor exists (only the bundle-less dialog box would show).
        _status = "loading assets…";
        float tStart = 0f; bool started = false;
        while (tStart < 4f) { if (dm.IsAssetLoadInProgress) { started = true; break; } tStart += Time.deltaTime; yield return null; }
        float tDone = 0f;
        while (dm.IsAssetLoadInProgress && tDone < 45f) { tDone += Time.deltaTime; yield return null; }
        yield return new WaitForSeconds(0.35f);  // let Instantiate/BumperJumper settle onto the Cutscene layer

        SetCutsceneView(true);                   // reveal the Cutscene layer (StartCutscene skips this when editor=true)
        _loaded = true;
        Goto(total > 1 ? 1 : 0);                 // page 0 is the (invisible) setup page
        int actors = SafeCount(dm.actorList), boxes = SafeCount(dm.boxList);
        InfinityLoaderMod.SafeLog("[cutedit] loaded id " + _idText + " frames=" + total + " loadStarted=" + started
            + " waitStart=" + tStart.ToString("F1") + "s waitFinish=" + tDone.ToString("F1") + "s actors=" + actors + " boxes=" + boxes);
        _status = "loaded — " + total + " page(s), " + actors + " actor(s). Use the pager.";
    }

    private static int SafeCount(System.Collections.ICollection c) { return c == null ? -1 : c.Count; }

    // Playback's StartCutscene reveals the Cutscene render layer (22) on Camera.main and hides the
    // game world — but only in its `if (!editor)` block, which we skip. So the loaded actors/BGs
    // (tagged layer 22) stay culled and only the dialog box (a normal UI canvas) shows. Do the
    // reveal ourselves: show layer 22, hide the world layers for a clean preview. Input/HUD stay
    // usable (our IMGUI panel is drawn by OnGUI, not a culled layer). Restored on Close.
    private static void SetCutsceneView(bool on)
    {
        Game g;
        try { g = Singleton<Game>.Instance; } catch { g = null; }
        if (g == null) return;
        try
        {
            g.HideCutsceneLayer(!on);            // on -> hide:false -> reveal the actors/BG layer
            g.HideDefaultLayer(on);              // hide the live map/player while previewing
            g.HideFakeUILayer(on);
            g.HideUILayer(on);
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] layer toggle warn " + ex); }
    }

    private void Goto(int p)
    {
        var dm = Dialogger_Manager.instance;
        int total = FrameCount();
        if (dm == null || total == 0) return;
        _page = Mathf.Clamp(p, total > 1 ? 1 : 0, total - 1);
        try { dm.LoadPage(_page); }
        catch (Exception ex) { _status = "render error on page " + _page + ": " + ex.Message; }
    }

    private void CloseEditor()
    {
        var dm = Dialogger_Manager.instance;
        if (dm != null && _driving)
        {
            try
            {
                dm.DestroyAll();
                dm.active = false;
                if (dm.borderContainer != null) dm.borderContainer.SetActive(false);
                dm.CutFromBlack();
                SetCutsceneView(false);          // re-hide layer 22, restore the world/HUD
                if (_savedOrtho > 0f && Camera.main != null) Camera.main.orthographicSize = _savedOrtho;
                dm.editor = _prevEditorFlag;
            }
            catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] close warn " + ex); }
        }
        _driving = false; _loaded = false; _open = false;
        _status = "closed.";
    }

    // GET-or-POST our ungated tweak/DialoggerLoad -> the stored Dialogger_Data JSON (verbatim).
    // Uses .NET WebClient directly (not UnityWebRequest), so Unity 6's cleartext-http block
    // doesn't apply — same approach as the loader's WebCom bypass.
    private static string Fetch(string id)
    {
        string url = Main.WebApiURL + "tweak/DialoggerLoad";
        using (var wc = new WebClient())
        {
            wc.Headers[HttpRequestHeader.ContentType] = "application/x-www-form-urlencoded";
            byte[] resp = wc.UploadData(url, "POST", Encoding.UTF8.GetBytes("id=" + Uri.EscapeDataString(id ?? "")));
            return Encoding.UTF8.GetString(resp);
        }
    }

    // ---- Dialogger_Data command model ---------------------------------------
    // The scene is dm.dData.frames: a list of frames, each a list of "Name{a|b|c}" command
    // strings. We edit those strings in place and LoadPage(n) to re-render. (Save = Phase 3.)

    private struct RObj { public string kind, id, type, name;
        public RObj(string k, string i, string t, string n) { kind = k; id = i; type = t; name = n; } }

    private System.Collections.Generic.List<RObj> Roster()
    {
        var list = new System.Collections.Generic.List<RObj>();
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || dm.dData.frames.Count == 0) return list;
        foreach (var cmd in dm.dData.frames[0])
        {
            if (string.IsNullOrEmpty(cmd)) continue;
            if (cmd.StartsWith("Load{"))
            {
                var f = Body(cmd); if (f == null || f.Length < 3) continue;
                list.Add(new RObj("obj", f[0], f[2], LoadName(f[0], f[1], f[2])));
            }
            else if (cmd.StartsWith("SpawnBox{"))
            {
                var f = Body(cmd); if (f == null || f.Length < 1) continue;
                list.Add(new RObj("box", f[0], "box", "Box " + f[0]));
            }
        }
        return list;
    }

    private static string LoadName(string id, string link, string type)
    {
        if (type == "player") return "Player";
        if (type == "npc") return "NPC " + link;
        if (type == "bg") return string.IsNullOrEmpty(link) ? ("BG " + id) : link;
        if (type == "music") return "Music " + link;
        if ((type == "actor" || type == "sfx") && !string.IsNullOrEmpty(link))
        { int c = link.IndexOf(','); return c >= 0 ? link.Substring(c + 1) : link; }
        return type + " " + id;
    }

    private static string Category(RObj o)
    {
        if (o.kind == "box") return "Boxes";
        if (o.type == "music" || o.type == "sfx") return "Audio";
        if (o.type == "bg") return "BGs";
        if (o.type == "actor" && o.name.IndexOf("bg", StringComparison.OrdinalIgnoreCase) >= 0) return "BGs";
        return "Actors";
    }

    private static string[] Body(string cmd)
    {
        if (string.IsNullOrEmpty(cmd)) return null;
        int b = cmd.IndexOf('{'); int e = cmd.LastIndexOf('}');
        if (b < 0 || e <= b) return null;
        return cmd.Substring(b + 1, e - b - 1).Split('|');
    }
    private static string Rebuild(string name, string[] f) { return name + "{" + string.Join("|", f) + "}"; }

    private string SelPrefix()
    {
        if (_selKind == "obj") return "Object{" + _selId + "|";
        if (_selKind == "box") return "Box{" + _selId + "|";
        if (_selKind == "cam") return "Camera{";
        return null;
    }

    private int FindIdx(int p, string prefix)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || prefix == null
            || p < 0 || p >= dm.dData.frames.Count) return -1;
        var fr = dm.dData.frames[p];
        for (int i = 0; i < fr.Count; i++) if (fr[i] != null && fr[i].StartsWith(prefix)) return i;
        return -1;
    }

    private void LoadBuf()
    {
        _buf.Clear();
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, SelPrefix());
        if (i < 0) { _buf["__missing"] = "1"; return; }
        var f = Body(dm.dData.frames[_page][i]);
        if (f == null) { _buf["__missing"] = "1"; return; }
        for (int k = 0; k < f.Length; k++) _buf["f" + k] = f[k];
        _buf["__len"] = f.Length.ToString();
        if (_selKind == "box" && f.Length > 7) _buf["speaker"] = ExtractSpeaker(f[7]);
    }

    private void ApplyBuf()
    {
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, SelPrefix());
        if (i < 0) return;
        var f = Body(dm.dData.frames[_page][i]);
        if (f == null) return;
        if (_selKind == "box" && _buf.ContainsKey("speaker") && f.Length > 7)
        {
            string sp = _buf["speaker"] ?? "";
            _buf["f7"] = sp.Length == 0 ? "" : "<size=42>" + sp + "</size>\n<size=24></size>";
        }
        for (int k = 0; k < f.Length; k++) if (_buf.ContainsKey("f" + k)) f[k] = _buf["f" + k];
        string name = _selKind == "obj" ? "Object" : (_selKind == "box" ? "Box" : "Camera");
        dm.dData.frames[_page][i] = Rebuild(name, f);
        try { dm.LoadPage(_page); } catch (Exception ex) { _status = "render err: " + ex.Message; }
        _bufSig = "";                                // reload buffers from the now-canonical command
    }

    private void AddToPage()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null) return;
        string prefix = SelPrefix(); string cmd = null;
        for (int k = _page - 1; k >= 1; k--) { int i = FindIdx(k, prefix); if (i >= 0) { cmd = dm.dData.frames[k][i]; break; } }
        if (cmd == null)
        {
            if (_selKind == "obj") cmd = "Object{" + _selId + "|1|1|0|1|0|0|0|-1 0|FFFFFFFF|0|0|1}";
            else if (_selKind == "box") cmd = "Box{" + _selId + "|0|0|1|1|0.5|0.862069|<size=42></size>\n<size=24></size>||1|0|38|000000|FFFFFF|FFFFFF|000000|-1|0|0}";
            else if (_selKind == "cam") cmd = "Camera{1|0|0|1|0|-1 0|0}";
        }
        if (cmd != null) { dm.dData.frames[_page].Add(cmd); try { dm.LoadPage(_page); } catch { } }
    }

    private static string ExtractSpeaker(string nameplate)
    {
        if (string.IsNullOrEmpty(nameplate)) return "";
        var m = System.Text.RegularExpressions.Regex.Match(nameplate, "<size=42>(.*?)</size>",
            System.Text.RegularExpressions.RegexOptions.Singleline);
        return m.Success ? m.Groups[1].Value : nameplate;
    }

    // ---- Phase 3: page management + authoring + save ------------------------
    private void ClonePage()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || _page < 0 || _page >= dm.dData.frames.Count) return;
        dm.dData.frames.Insert(_page + 1, new System.Collections.Generic.List<string>(dm.dData.frames[_page]));
        Goto(_page + 1); _status = "cloned page";
    }

    private void BlankPage()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) return;
        dm.dData.frames.Insert(_page + 1, new System.Collections.Generic.List<string>());
        Goto(_page + 1); _status = "blank page added";
    }

    private void DeletePage()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) return;
        if (_page <= 0) { _status = "page 0 is the required setup page"; return; }
        if (dm.dData.frames.Count <= 2) { _status = "can't delete the only content page"; return; }
        dm.dData.frames.RemoveAt(_page);
        Goto(Mathf.Min(_page, dm.dData.frames.Count - 1)); _status = "page deleted";
    }

    private void AddCmd(string cmd)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) return;
        if (_page < 1) { _status = "go to a page >= 1 first"; return; }
        dm.dData.frames[_page].Add(cmd);
        try { dm.LoadPage(_page); } catch (Exception ex) { _status = "render err: " + ex.Message; }
        _status = "added " + cmd;
    }

    private void AddBubble()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) return;
        if (_page < 1) { _status = "go to a page >= 1 first"; return; }
        int id = dm.dData.boxCount; dm.dData.boxCount = id + 1;
        dm.dData.frames[0].Add("SpawnBox{" + id + "}");        // persist the spawn on the setup frame
        try { dm.ReadCommand_SpawnBox(id, false); } catch { }  // create it live now (no page-0 re-run)
        dm.dData.frames[_page].Add("Box{" + id + "|0|0|1|1|0.5|0.862069|<size=42>Name</size>\n<size=24></size>|New line.|1|0|38|000000|FFFFFF|FFFFFF|000000|-1|0|0}");
        try { dm.LoadPage(_page); } catch { }
        _selKind = "box"; _selId = id.ToString(); _bufSig = "";
        _status = "added bubble #" + id;
    }

    // Serialize the in-memory scene and POST it to tweak/DialoggerSave (ungated, same storage the
    // web editor + AE's native Dialogger use). asNew blanks the id so the server mints the next one.
    // After this, /cutscene <id> plays the edits. WARNING: a plain Save overwrites the loaded id.
    private void SaveScene(bool asNew)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) { _status = "nothing to save"; return; }
        string json;
        try { json = Newtonsoft.Json.JsonConvert.SerializeObject(dm.dData); }
        catch (Exception ex) { _status = "serialize error: " + ex.Message; return; }
        string id = asNew ? "" : (_idText ?? "").Trim();
        try
        {
            using (var wc = new WebClient())
            {
                var form = new System.Collections.Specialized.NameValueCollection();
                form["id"] = id; form["json"] = json;
                byte[] resp = wc.UploadValues(Main.WebApiURL + "tweak/DialoggerSave", form);
                string savedId = Encoding.UTF8.GetString(resp).Trim();
                if (!string.IsNullOrEmpty(savedId)) _idText = savedId;
                _status = "saved cutscene " + savedId + " (" + json.Length + " bytes)";
                InfinityLoaderMod.SafeLog("[cutedit] saved id " + savedId + " (" + json.Length + " bytes)");
            }
        }
        catch (Exception ex) { _status = "save failed: " + ex.Message; InfinityLoaderMod.SafeLog("[cutedit] save fail " + ex.Message); }
    }
}
