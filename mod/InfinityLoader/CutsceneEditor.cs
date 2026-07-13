using System;
using System.Collections;
using System.Collections.Generic;
using System.Net;
using System.Text;
using Newtonsoft.Json.Linq;
using Pixelplacement;   // Singleton<Game>.Instance (the layer-visibility toggles)
using UnityEngine;
using UnityEngine.Rendering;

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
    // Asset browser (searches the ungated tweak/csnpcs + tweak/csassets endpoints)
    private bool _browseOpen;
    private string _browseMode = "npc";
    private string _browseQuery = "";
    private string _browseStatus = "type a query and Search";
    private Vector2 _browseScroll;
    private readonly List<string[]> _browseResults = new List<string[]>();  // [type, link, name]
    private Rect _browseRect;                          // draggable window rect (0 w => not yet placed)
    private const int BROWSE_WIN_ID = 918273;
    private bool _dragging;                           // drag-to-move on the render
    private float _dragOffX, _dragOffY;               // grab offset so the grab point stays under the cursor

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
        try { if (Input.GetKeyDown(ToggleKey)) _open = !_open; } catch { }
        if (_open && _loaded)
        {
            try { HandleDrag(); } catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] drag ex " + ex.Message); }
            try { HideBoxChrome(); } catch { }
        }
    }

    // Each dialog box carries AE's own editor overlay (the MV/AP/AT buttons + arrow popups) because
    // we run with editor=true. They're wired to Dialogger_EditorManager, which we don't have, so
    // they NRE / don't persist — pure confusion. Deactivate that editor-component object on every
    // box; the box body/nameplate/tail render from Dialogger_BoxController (a separate object), so
    // the bubble itself is untouched. Our inspector's Tail anchor / Arrow style drive it instead.
    private void HideBoxChrome()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.boxList == null) return;
        for (int i = 0; i < dm.boxList.Count; i++)
        {
            var mt = dm.boxList[i];
            if (mt != null && mt.decb != null && mt.decb.decb != null)
            {
                var go = mt.decb.decb.gameObject;
                if (go != null && go.activeSelf) go.SetActive(false);
            }
        }
    }

    // Drag a selected actor/BG on the render: grab near its screen position, then map the cursor
    // through Camera.main into the actor's parent-local space (its Object x/y). Orthographic, so
    // the z we pass to ScreenToWorldPoint doesn't affect x/y. Writes the command + re-renders.
    private Transform SelectedTransform()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || _selKind != "obj" || _selId == null) return null;
        int id; if (!int.TryParse(_selId, out id)) return null;
        var mt = dm.GetActorFromID(id);
        return (mt != null) ? mt.tt : null;
    }

    private void HandleDrag()
    {
        if (_browseOpen || Camera.main == null) return;
        Vector3 mp = Input.mousePosition;
        bool inRender = mp.x >= 280f && mp.y < Screen.height - 82f;

        if (Input.GetMouseButtonDown(0) && inRender)
        {
            // Click-to-select: pick the front-most actor under the cursor (backgrounds excluded).
            var picked = ScreenPick(mp);
            if (picked != null) { _selKind = "obj"; _selId = picked.ID.ToString(); _bufSig = ""; }
            var tt0 = SelectedTransform();
            if (tt0 != null && tt0.parent != null)
            {
                _dragging = true;
                Vector3 cl = tt0.parent.InverseTransformPoint(Camera.main.ScreenToWorldPoint(new Vector3(mp.x, mp.y, 10f)));
                Vector2 cur = CurrentObjPos();
                _dragOffX = cur.x - cl.x; _dragOffY = cur.y - cl.y;   // keep the grabbed point under the cursor
                InfinityLoaderMod.SafeLog("[cutedit] grab #" + _selId + (picked != null ? " (picked on screen)" : ""));
            }
        }
        if (!Input.GetMouseButton(0)) { if (_dragging) { _dragging = false; _bufSig = ""; _status = "moved #" + _selId; } return; }
        if (!_dragging) return;
        var tt = SelectedTransform();
        if (tt == null || tt.parent == null) { _dragging = false; return; }
        Vector3 world = Camera.main.ScreenToWorldPoint(new Vector3(mp.x, mp.y, 10f));
        Vector3 local = tt.parent.InverseTransformPoint(world);
        SetSelectedPos(local.x + _dragOffX, local.y + _dragOffY);
    }

    // Pick the front-most (highest z-order) actor whose sprite bounds contain the cursor. Excludes
    // backgrounds (they cover the whole screen — select those from the tree). World-space AABB test,
    // exact enough for the orthographic cutscene camera.
    private Dialogger_MovementTransform ScreenPick(Vector3 mp)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.actorList == null) return null;
        Vector3 w = Camera.main.ScreenToWorldPoint(new Vector3(mp.x, mp.y, 10f));
        Dialogger_MovementTransform best = null; int bestZ = int.MinValue;
        for (int k = 0; k < dm.actorList.Count; k++)
        {
            var mt = dm.actorList[k];
            if (mt == null || mt.isBG || !mt.isVisible) continue;
            var rs = mt.GetComponentsInChildren<Renderer>();
            if (rs == null || rs.Length == 0) continue;
            Bounds b = default(Bounds); bool any = false;
            foreach (var r in rs)
            {
                if (!(r is SpriteRenderer) && !(r is MeshRenderer)) continue;
                if (!any) { b = r.bounds; any = true; } else b.Encapsulate(r.bounds);
            }
            if (!any) continue;
            if (w.x >= b.min.x && w.x <= b.max.x && w.y >= b.min.y && w.y <= b.max.y && mt.zStore >= bestZ)
            { bestZ = mt.zStore; best = mt; }
        }
        return best;
    }

    private Vector2 CurrentObjPos()
    {
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, SelPrefix());
        if (i < 0) return Vector2.zero;
        var f = Body(dm.dData.frames[_page][i]);
        if (f == null || f.Length < 7) return Vector2.zero;
        float x, y;
        float.TryParse(f[5], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out x);
        float.TryParse(f[6], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out y);
        return new Vector2(x, y);
    }

    private void SetSelectedPos(float x, float y)
    {
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, SelPrefix());
        if (i < 0) return;
        var f = Body(dm.dData.frames[_page][i]);
        if (f == null || f.Length < 7) return;
        f[5] = x.ToString("F2", System.Globalization.CultureInfo.InvariantCulture);
        f[6] = y.ToString("F2", System.Globalization.CultureInfo.InvariantCulture);
        dm.dData.frames[_page][i] = Rebuild("Object", f);
        try { dm.LoadPage(_page); } catch { }
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
            if (_browseOpen) DrawBrowser();
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
        GUILayout.Label("id", _sMuted, GUILayout.Width(12));
        _idText = GUILayout.TextField(_idText ?? "", _sField, GUILayout.Width(38));
        if (GUILayout.Button("Load", _sBtnBlue, GUILayout.Width(44))) StartCoroutine(LoadAndRender());
        if (GUILayout.Button("New", _sBtn, GUILayout.Width(40))) NewScene();
        if (GUILayout.Button("Close", _sBtn, GUILayout.Width(46))) CloseEditor();
        GUILayout.EndHorizontal();

        if (_loaded)
        {
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Save", _sBtnYellow, GUILayout.Width(74))) SaveScene(false);
            if (GUILayout.Button("Save as NEW", _sBtn)) SaveScene(true);
            GUILayout.EndHorizontal();
        }
        GUILayout.Label(_status ?? "", _sMuted);

        var dmN = Dialogger_Manager.instance;
        if (_loaded && dmN != null && dmN.dData != null)
        {
            GUILayout.BeginHorizontal();
            GUILayout.Label("name", _sMuted, GUILayout.Width(34));
            dmN.dData.cutsceneName = GUILayout.TextField(dmN.dData.cutsceneName ?? "", _sField);
            GUILayout.EndHorizontal();
        }

        if (_loaded)
        {
            GUILayout.Space(4);
            float listH = Mathf.Max(120f, (Screen.height - 232f) * 0.45f);
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
            FieldSet("Scale", 4); FieldSet("Z Order", 3); TweenRow(8); FieldSet("Tint", 9);
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
            StepRow("Tail anchor", 16, -1, 24);   // f16 shownLead: -1 = no tail, else which anchor point
            StepRow("Arrow style", 17, 0, 4);      // f17 leadPos: the tail/arrow style
            GUILayout.BeginHorizontal(); ToggleBtn("Visible", 4, "1", "0"); GUILayout.EndHorizontal();
        }
        else // cam
        {
            int len = _buf.ContainsKey("__len") ? int.Parse(_buf["__len"]) : 0;
            if (len >= 7) { FieldSet("Zoom", 0); PosRow(1, 2); FieldSet("Rotation", 4); TweenRow(5); }
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

    // Tween field ("speed easetype [shake…]"): a speed text field + a cycle button that names the
    // ease curve (per Dialogger_MovementTransform.Easinator), preserving any trailing shake tokens.
    private static readonly string[] _easeNames = { "linear*", "easeIn", "linear", "easeOut", "smooth", "bounce", "elastic", "outIn" };
    private void TweenRow(int idx)
    {
        string k = "f" + idx; if (!_buf.ContainsKey(k)) return;
        GUILayout.BeginHorizontal();
        GUILayout.Label("Tween", _sLabel, GUILayout.Width(60));
        _buf[k] = GUILayout.TextField(_buf[k] ?? "", _sField, GUILayout.Width(64));
        var toks = (_buf[k] ?? "").Split(' ');
        int ease = 0; if (toks.Length > 1) int.TryParse(toks[1], out ease);
        string en = (ease >= 0 && ease < _easeNames.Length) ? _easeNames[ease] : ("" + ease);
        if (GUILayout.Button(en, _sBtn, GUILayout.Width(64)))
        {
            int ne = (ease + 1) % _easeNames.Length;
            string speed = toks.Length > 0 ? toks[0] : "-1";
            string rest = toks.Length > 2 ? " " + string.Join(" ", toks, 2, toks.Length - 2) : "";
            _buf[k] = speed + " " + ne + rest;
            ApplyBuf();
        }
        if (GUILayout.Button("Set", _sBtnBlue, GUILayout.Width(40))) ApplyBuf();
        GUILayout.EndHorizontal();
    }

    // Integer stepper (− value +) — for the box tail anchor / arrow style, which are small ints.
    private void StepRow(string label, int idx, int min, int max)
    {
        string k = "f" + idx; if (!_buf.ContainsKey(k)) return;
        int v; int.TryParse(_buf[k], out v);
        GUILayout.BeginHorizontal();
        GUILayout.Label(label, _sLabel, GUILayout.Width(80));
        if (GUILayout.Button("−", _sBtn, GUILayout.Width(28))) { _buf[k] = Mathf.Clamp(v - 1, min, max).ToString(); ApplyBuf(); }
        GUILayout.Label(v.ToString(), _sLabel, GUILayout.Width(34));
        if (GUILayout.Button("+", _sBtn, GUILayout.Width(28))) { _buf[k] = Mathf.Clamp(v + 1, min, max).ToString(); ApplyBuf(); }
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
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("+ Add " + _addType, _sBtnBlue))
        {
            string link = _addType == "player" ? "" : (_addInput ?? "").Trim();
            if (_addType == "player" || link.Length > 0) AddObject(link, _addType);
            else _status = "enter a value first";
        }
        if (GUILayout.Button("Browse…", _sBtn, GUILayout.Width(72)))
        { _browseOpen = true; if (_browseResults.Count == 0) BrowseSearch(); }
        GUILayout.EndHorizontal();
    }

    // ---- asset browser: search NPCs / harvested actor-BG assets, click to add -----------------
    // A draggable GUI.Window (grab the title bar to move it). Content lives in DrawBrowserWindow.
    private void DrawBrowser()
    {
        float w = 400f, h = Mathf.Min(470f, Screen.height - 90f);
        if (_browseRect.width < 1f)      // first open: centre it in the render area, then it persists
            _browseRect = new Rect(276f + Mathf.Max(12f, (Screen.width - 276f - w) * 0.5f), 46f, w, h);
        _browseRect.width = w; _browseRect.height = h;   // keep size stable across resolution changes
        _browseRect = GUI.Window(BROWSE_WIN_ID, _browseRect, DrawBrowserWindow, "", _sPanel);
    }

    private void DrawBrowserWindow(int id)
    {
        GUILayout.BeginHorizontal();
        GUILayout.Label("Asset Browser", _sHeader);
        GUILayout.FlexibleSpace();
        if (GUILayout.Button("✕", _sBtn, GUILayout.Width(30))) _browseOpen = false;
        GUILayout.EndHorizontal();
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("NPCs", _browseMode == "npc" ? _sBtnBlue : _sBtn)) { _browseMode = "npc"; BrowseSearch(); }
        if (GUILayout.Button("Actors / BGs", _browseMode == "asset" ? _sBtnBlue : _sBtn)) { _browseMode = "asset"; BrowseSearch(); }
        GUILayout.EndHorizontal();
        GUILayout.BeginHorizontal();
        _browseQuery = GUILayout.TextField(_browseQuery ?? "", _sField);
        if (GUILayout.Button("Search", _sBtnBlue, GUILayout.Width(64))) BrowseSearch();
        GUILayout.EndHorizontal();
        GUILayout.Label(_browseStatus ?? "", _sMuted);
        _browseScroll = GUILayout.BeginScrollView(_browseScroll);
        foreach (var r in _browseResults)
        {
            // Just the name — no "type ·" prefix or "[link]" suffix. NPCs keep a small #id (names
            // repeat); the link is only needed internally and gets passed to AddObject.
            string name = string.IsNullOrEmpty(r[2]) ? r[1] : r[2];
            string label = r[0] == "npc" ? (name + "   #" + r[1]) : name;
            if (GUILayout.Button(label, _sRow)) { AddObject(r[1], r[0]); _status = "added: " + name; }
        }
        if (_browseResults.Count == 0) GUILayout.Label("no results — try another query", _sMuted);
        GUILayout.EndScrollView();
        GUI.DragWindow(new Rect(0, 0, _browseRect.width, 30f));   // only the title strip drags
    }

    private void BrowseSearch()
    {
        try
        {
            string ep = _browseMode == "npc" ? "tweak/csnpcs" : "tweak/csassets";
            string url = Main.WebApiURL + ep + "?q=" + Uri.EscapeDataString(_browseQuery ?? "");
            string json;
            using (var wc = new WebClient()) json = Encoding.UTF8.GetString(wc.DownloadData(url));
            _browseResults.Clear();
            _browseResults.AddRange(ParseBrowse(json, _browseMode));
            _browseStatus = _browseResults.Count + " result(s)";
        }
        catch (Exception ex) { _browseStatus = "search failed: " + ex.Message; }
    }

    private static List<string[]> ParseBrowse(string json, string mode)
    {
        var list = new List<string[]>();
        var seen = new HashSet<string>();           // dedupe by id/link — endpoints repeat entries
        try
        {
            var arr = Newtonsoft.Json.JsonConvert.DeserializeObject<List<Dictionary<string, object>>>(json);
            if (arr == null) return list;
            foreach (var m in arr)
            {
                if (mode == "npc")
                {
                    string id = m.ContainsKey("id") ? Convert.ToString(m["id"]) : "";
                    string name = m.ContainsKey("name") ? Convert.ToString(m["name"]) : "";
                    if (id.Length > 0 && seen.Add("n:" + id)) list.Add(new[] { "npc", id, name });
                }
                else
                {
                    string link = m.ContainsKey("link") ? Convert.ToString(m["link"]) : "";
                    string type = m.ContainsKey("type") ? Convert.ToString(m["type"]) : "actor";
                    string name = m.ContainsKey("name") ? Convert.ToString(m["name"]) : link;
                    if (link.Length > 0 && seen.Add("a:" + link)) list.Add(new[] { type, link, name });
                }
            }
        }
        catch { }
        return list;
    }

    private void AddObject(string link, string type)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) return;
        if (type == "actor" && !link.Contains(",")) { _status = "actor needs 'bundleId,PrefabName' (e.g. 66131,actor-veddrian)"; return; }
        if (type == "npc") InfinityLoaderMod.EnsureNpcLoaderPatch();
        int id = dm.dData.idCount; dm.dData.idCount = id + 1;
        dm.dData.frames[0].Add("Load{" + id + "|" + link + "|" + type + "}");
        // z-order 20 so it renders in FRONT of the background layers (a fresh object at z 0 hides
        // behind the BG). Placed at origin; the author positions it in the inspector.
        if (_page >= 1) dm.dData.frames[_page].Add("Object{" + id + "|1|1|20|1|0|0|0|-1 0|FFFFFFFF|0|0|1}");
        _selKind = "obj"; _selId = id.ToString(); _bufSig = "";

        _status = "loading " + type + " #" + id + "…";
        InfinityLoaderMod.SafeLog("[cutedit] add " + type + " #" + id + " (" + link + ")");
        if (type == "npc")
        {
            StartCoroutine(LoadNpcObjectDirect(id, link));
            return;
        }

        // Load exactly the way the scene loads its own assets (Dialogger_Manager.ProcessLoadCommands):
        // preload any actor/sfx bundle metadata via AssetBundleDataLoader.Load, THEN ReadCommand_Load
        // in its callback (which only acts while pageNumber==0, so flip it for that call).
        var bundleIds = new List<int>();
        if (type == "actor" || type == "sfx")
        { var p = link.Split(','); int bid; if (p.Length > 0 && int.TryParse(p[0], out bid)) bundleIds.Add(bid); }
        AssetBundleDataLoader.Load(bundleIds, delegate
        {
            int saved = dm.pageNumber; dm.pageNumber = 0;
            try { dm.ReadCommand_Load(id, link, type); }
            catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] add load err " + ex.Message); }
            dm.pageNumber = saved;
        });
        StartCoroutine(WaitThenRender(id));
    }


    private IEnumerator LoadNpcObjectDirect(int id, string link)
    {
        var dm = Dialogger_Manager.instance;
        int npcId;
        if (!int.TryParse(link, out npcId))
        {
            _status = "NPC id is not numeric: " + link;
            yield break;
        }

        Monbranch mb = null;
        try { mb = FetchNpcMonbranch(npcId); }
        catch (Exception ex)
        {
            _status = "NPC fetch failed: " + ex.Message;
            InfinityLoaderMod.SafeLog("[cutedit] direct npc fetch failed #" + id + " npc=" + npcId + " " + ex.Message);
            yield break;
        }
        if (mb == null)
        {
            _status = "NPC " + npcId + " was not returned by GetMonsterData";
            yield break;
        }
        if (mb.equippedItems == null || mb.equippedItems.Count == 0)
        {
            LoadNpcViaDialogger(id, link);
            StartCoroutine(WaitThenRender(id));
            yield break;
        }

        bool complete = false;
        string error = null;
        GameObject asset = null;
        Avatar avt = null;
        try
        {
            var character = new Monster(mb.ID, mb, ig: false);
            character.init();
            LogNpcEquipSummary(id, npcId, character);
            character.AssetUpdated += delegate
            {
                if (asset == null) asset = character.getGameObject();
            };
            character.createAvatar();
            avt = character.GetAvatar();
            if (avt != null)
            {
                avt.hideFlame = true;
                avt.OnSetupComplete = (Action<GameObject>)Delegate.Combine(avt.OnSetupComplete, (Action<GameObject>)delegate(GameObject ready)
                {
                    if (complete) return;
                    asset = ready != null ? ready : character.getGameObject();
                    complete = true;
                });
                avt.OnLoadError = (Action<string>)Delegate.Combine(avt.OnLoadError, (Action<string>)delegate(string e)
                {
                    if (complete) return;
                    error = e;
                    complete = true;
                });
            }
        }
        catch (Exception ex)
        {
            _status = "NPC setup failed: " + ex.Message;
            InfinityLoaderMod.SafeLog("[cutedit] direct npc setup failed #" + id + " npc=" + npcId + " " + ex);
            yield break;
        }

        float t = 0f;
        while (!complete && t < 30f)
        {
            t += Time.deltaTime;
            var ha = avt as HumanoidAvatar;
            if (ha != null && ha.CC != null)
            {
                int renderers = 0;
                try { renderers = ha.CC.gameObject.GetComponentsInChildren<Renderer>(includeInactive: true).Length; } catch { }
                if (ha.allLoaded && t > 0.5f)
                {
                    asset = ha.CC.gameObject;
                    // ShowChar/OnSetupComplete is the real signal, but allLoaded is a useful fallback
                    // if a custom item path never fires that callback.
                    if (t > 2f)
                    {
                        complete = true;
                        break;
                    }
                }
            }
            yield return null;
        }

        if (!string.IsNullOrEmpty(error))
        {
            _status = "NPC load failed: " + error;
            InfinityLoaderMod.SafeLog("[cutedit] direct npc load failed #" + id + " npc=" + npcId + " " + error);
            yield break;
        }
        if (asset == null)
        {
            _status = "NPC " + npcId + " timed out before creating an actor";
            InfinityLoaderMod.SafeLog("[cutedit] direct npc timeout #" + id + " npc=" + npcId);
            yield break;
        }

        try
        {
            var finalHumanoid = avt as HumanoidAvatar;
            if (finalHumanoid != null && finalHumanoid.CC != null) asset = finalHumanoid.CC.gameObject;
            string name = string.IsNullOrEmpty(mb.strMonName) ? ("NPC" + npcId) : mb.strMonName;
            if (!asset.name.EndsWith("(Clone)", StringComparison.Ordinal)) asset.name = name + "(Clone)";
            StripCutsceneRuntimeComponents(asset);
            RevealHumanoidSlots(finalHumanoid);
            int prepared = PrepareCutsceneHumanoid(asset);
            int enabled = CountActiveRenderers(asset);
            dm.BumperJumper(asset, "OBJ " + id + " - " + name, id, frombund: false, "O" + id + " " + name);
            RevealHumanoidSlots(finalHumanoid);
            PrepareCutsceneHumanoid(asset);
            enabled = CountActiveRenderers(asset);
            bool humanoidDone = finalHumanoid != null && finalHumanoid.allLoaded;
            InfinityLoaderMod.SafeLog("[cutedit] direct npc #" + id + " prepared renderers=" + prepared + " enabledRenderers=" + enabled + " allLoaded=" + humanoidDone);
            try { dm.LoadPage(_page); } catch { }
            _bufSig = "";
            bool ok = dm.GetActorFromID(id) != null;
            _status = ok ? ("added #" + id + " - position it in the inspector") : ("#" + id + " assembled but did not register");
            InfinityLoaderMod.SafeLog("[cutedit] direct npc #" + id + " npc=" + npcId + " loaded=" + ok + " actors=" + SafeCount(dm.actorList));
        }
        catch (Exception ex)
        {
            _status = "NPC wrap failed: " + ex.Message;
            InfinityLoaderMod.SafeLog("[cutedit] direct npc wrap failed #" + id + " npc=" + npcId + " " + ex);
        }
    }

    private void LoadNpcViaDialogger(int id, string link)
    {
        var dm = Dialogger_Manager.instance;
        int saved = dm.pageNumber; dm.pageNumber = 0;
        try { dm.ReadCommand_Load(id, link, "npc"); }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] stock npc load err " + ex.Message); }
        dm.pageNumber = saved;
    }

    private static Monbranch FetchNpcMonbranch(int npcId)
    {
        string baseUrl = Main.WebApiURL;
        if (string.IsNullOrEmpty(baseUrl)) baseUrl = "https://130-162-189-229.sslip.io/";
        if (!baseUrl.EndsWith("/")) baseUrl += "/";
        string json;
        using (var wc = new WebClient()) json = wc.DownloadString(baseUrl + "data/GetMonsterData?ids=" + npcId);
        var arr = JArray.Parse(json);
        if (arr.Count == 0) return null;
        var obj = arr[0] as JObject;
        if (obj == null) return null;
        NormalizeNpcEquips(obj);
        return obj.ToObject<Monbranch>();
    }

    private static void NormalizeNpcEquips(JObject obj)
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



    private static void LogNpcEquipSummary(int actorId, int npcId, Entity character)
    {
        if (character == null) return;
        try
        {
            InfinityLoaderMod.SafeLog("[cutedit] npc #" + actorId + " npc=" + npcId
                + " gender=" + character.GetGenderString()
                + " armor=" + DescribeEquip(character.Armor)
                + " class=" + DescribeEquip(character.Class)
                + " helm=" + DescribeEquip(character.Helm)
                + " weapon=" + DescribeEquip(character.Weapon)
                + " back=" + DescribeEquip(character.Back));
        }
        catch (Exception ex)
        {
            InfinityLoaderMod.SafeLog("[cutedit] npc #" + actorId + " equip log failed " + ex.Message);
        }
    }

    private static string DescribeEquip(EquipItem item)
    {
        if (item == null) return "null";
        string bundle = item.Bundle != null ? item.Bundle.Filename : "no-bundle";
        string prefab = string.IsNullOrEmpty(item.PrefabName) ? "no-prefab" : item.PrefabName;
        return item.ID + "/" + item.EquipSpot + "/" + item.ItemType + "/" + bundle + "/" + prefab;
    }

    private static int RevealHumanoidSlots(HumanoidAvatar avatar)
    {
        if (avatar == null || avatar.CC == null) return 0;
        int count = 0;
        try { avatar.CC.setActive(true); } catch { }
        var slots = avatar.CC.GetComponentsInChildren<CustomizableSlot>(includeInactive: true);
        foreach (var slot in slots)
        {
            if (slot == null || slot.spriteRenderer == null || slot.spriteRenderer.sprite == null) continue;
            slot.gameObject.SetActive(true);
            slot.spriteRenderer.enabled = true;
            count++;
        }
        return count;
    }
    private static int PrepareCutsceneHumanoid(GameObject asset)
    {
        if (asset == null) return 0;
        asset.SetActive(true);
        int cutsceneLayer = LayerMask.NameToLayer("Cutscene");
        var transforms = asset.GetComponentsInChildren<Transform>(includeInactive: true);
        foreach (var t in transforms)
        {
            if (t == null) continue;
            if (cutsceneLayer >= 0) t.gameObject.layer = cutsceneLayer;
        }

        int count = 0;
        var renderers = asset.GetComponentsInChildren<Renderer>(includeInactive: true);
        foreach (var r in renderers)
        {
            if (r == null) continue;
            if (cutsceneLayer >= 0) r.gameObject.layer = cutsceneLayer;
            try { r.sortingLayerName = "Cutscene-0"; } catch { }
            count++;
        }

        var groups = asset.GetComponentsInChildren<SortingGroup>(includeInactive: true);
        foreach (var sg in groups)
        {
            if (sg == null) continue;
            if (cutsceneLayer >= 0) sg.gameObject.layer = cutsceneLayer;
            try { sg.sortingLayerName = "Cutscene-0"; } catch { }
        }
        return count;
    }


    private static int CountActiveRenderers(GameObject asset)
    {
        if (asset == null) return 0;
        int count = 0;
        var renderers = asset.GetComponentsInChildren<Renderer>(includeInactive: true);
        foreach (var r in renderers)
        {
            if (r != null && r.enabled && r.gameObject.activeInHierarchy) count++;
        }
        return count;
    }
    private static void StripCutsceneRuntimeComponents(GameObject asset)
    {
        if (asset == null) return;
        foreach (var c in asset.GetComponentsInChildren<Collider2D>(includeInactive: true)) UnityEngine.Object.Destroy(c);
        foreach (var z in asset.GetComponentsInChildren<ZOffset>(includeInactive: true)) UnityEngine.Object.Destroy(z);
        foreach (var w in asset.GetComponentsInChildren<Walk>(includeInactive: true)) UnityEngine.Object.Destroy(w);
    }

    private IEnumerator WaitThenRender(int id)
    {
        var dm = Dialogger_Manager.instance;
        float t = 0f; while (t < 3f) { if (dm.IsAssetLoadInProgress) break; t += Time.deltaTime; yield return null; }
        float t2 = 0f; while (dm.IsAssetLoadInProgress && t2 < 30f) { t2 += Time.deltaTime; yield return null; }
        yield return new WaitForSeconds(0.25f);
        try { dm.LoadPage(_page); } catch { }
        _bufSig = "";
        bool ok = false; try { ok = dm.GetActorFromID(id) != null; } catch { }
        InfinityLoaderMod.SafeLog("[cutedit] add #" + id + " loaded=" + ok + " actors=" + SafeCount(dm.actorList));
        _status = ok ? ("added #" + id + " — position it in the inspector") : ("#" + id + " didn't load — check the id / bundle,prefab");
    }

    // Start a BLANK cutscene from scratch: a setup frame + one content page (fade in + a default
    // camera). Author it with Add Object / Add Bubble / pages, then Save as NEW to mint an id.
    private void NewScene()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null) { _status = "enter a map first"; return; }
        var data = new Dialogger_Data
        {
            ID = "", cutsceneName = "New Cutscene", cutsceneDescription = "",
            idCount = 0, boxCount = 0, trackCount = 0, sfxCount = 0,
            completeActions = new List<string>(),
            frames = new List<List<string>>
            {
                new List<string> { "" },
                new List<string> { "FadeFromBlack", "Camera{1|0|0|1|0|-1 0|0}" }
            }
        };
        string json;
        try { json = Newtonsoft.Json.JsonConvert.SerializeObject(data); }
        catch (Exception ex) { _status = "new err: " + ex.Message; return; }
        _idText = "";
        if (!_driving) { _prevEditorFlag = dm.editor; _savedOrtho = Camera.main != null ? Camera.main.orthographicSize : -1f; _driving = true; }
        dm.editor = true;
        try { dm.LoadJson(json); } catch (Exception ex) { _status = "new load err: " + ex.Message; return; }
        _loaded = true; _selKind = null; _selId = null; _bufSig = "";
        StartCoroutine(NewSceneRender());
    }

    private IEnumerator NewSceneRender()
    {
        yield return new WaitForSeconds(0.3f);
        SetCutsceneView(true);
        Goto(1);
        _status = "new blank scene — add objects, author pages, then Save as NEW";
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
        yield return StartCoroutine(RecoverMissingNpcActors());
        int actors = SafeCount(dm.actorList), boxes = SafeCount(dm.boxList);
        InfinityLoaderMod.SafeLog("[cutedit] loaded id " + _idText + " frames=" + total + " loadStarted=" + started
            + " waitStart=" + tStart.ToString("F1") + "s waitFinish=" + tDone.ToString("F1") + "s actors=" + actors + " boxes=" + boxes);
        _status = "loaded — " + total + " page(s), " + actors + " actor(s). Use the pager.";
    }


    private IEnumerator RecoverMissingNpcActors()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || dm.dData.frames.Count == 0) yield break;
        var setup = dm.dData.frames[0];
        for (int i = 0; i < setup.Count; i++)
        {
            string cmd = setup[i];
            if (string.IsNullOrEmpty(cmd) || !cmd.StartsWith("Load{")) continue;
            var f = Body(cmd);
            if (f == null || f.Length < 3 || f[2] != "npc") continue;
            int id;
            if (!int.TryParse(f[0], out id)) continue;
            try { if (dm.GetActorFromID(id) != null) continue; } catch { }
            InfinityLoaderMod.SafeLog("[cutedit] recover missing npc #" + id + " (" + f[1] + ")");
            yield return StartCoroutine(LoadNpcObjectDirect(id, f[1]));
        }
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
