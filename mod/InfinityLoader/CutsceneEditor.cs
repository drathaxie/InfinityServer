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
public class CutsceneActorAnimationLock : MonoBehaviour
{
    public Animator animator;
    public string clip;
    public float speed = 1f;
    public float normalizedStart;
    private float _startedAt;
    private float _duration = 1f;
    private bool _loops = true;

    public void Hold(Animator target, string state, float playbackSpeed, float start)
    {
        animator = target; clip = state; speed = playbackSpeed; normalizedStart = start;
        _startedAt = Time.unscaledTime; _duration = 1f; _loops = true;
        if (animator != null && animator.runtimeAnimatorController != null)
        {
            foreach (var candidate in animator.runtimeAnimatorController.animationClips)
            {
                if (candidate == null || !string.Equals(candidate.name, clip, StringComparison.OrdinalIgnoreCase)) continue;
                _duration = Mathf.Max(0.01f, candidate.length); _loops = candidate.isLooping; break;
            }
        }
        EvaluatePose();
    }

    private void LateUpdate() { EvaluatePose(); }

    private void EvaluatePose()
    {
        if (animator == null || string.IsNullOrEmpty(clip)) return;
        float elapsed = Mathf.Max(0f, Time.unscaledTime - _startedAt) * Mathf.Abs(speed);
        float normalized = normalizedStart + elapsed / Mathf.Max(0.01f, _duration);
        normalized = _loops ? Mathf.Repeat(normalized, 1f) : Mathf.Min(normalized, 0.999f);
        // The live Entity writes Idle during Update. Sample our commanded pose after that write and
        // evaluate it immediately before rendering, while keeping the controller itself paused.
        animator.Play(clip, 0, normalized);
        animator.speed = 0f;
        animator.Update(0f);
    }
}
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
    private string _moveSec = "1";                   // selected actor: page-to-page move duration
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

    // ---- AE-parity timeline state ----
    private bool _playing;                            // full-sequence playback in progress
    private bool _autoKey = true;                     // AE "Auto-key Active": edits auto-create this page's key
    private bool _autoAnimKey;                        // AE "Auto-anim key": auto keys also carry the Actor animation
    private bool _muteSfx;                            // AE "SFX = Off": block Sound/Music during preview/playback
    private readonly List<string>[] _copySlots = new List<string>[4];   // AE C1-C4 whole-page snapshots
    private bool _pasteCam = true, _pasteObj = true, _pasteAnim = true, _pasteSound = true, _pasteText = true;
    private string _animFilter = "";                  // animation-discovery clip filter
    private string _animClipSig = "";                 // selection the clip cache was built for
    private string[] _animClips = new string[0];      // discovered primary-Animator clip names
    private string[] _animClips2 = new string[0];     // discovered secondary-Animator clip names
    private string[] _animTags = new string[0];       // derived name-prefix groups (see DeriveTags)
    private bool _showGuides;                         // AE "Guides" overlay: rule-of-thirds + center cross
    private Texture2D _txGuide;
    private string _engineSfxName = "";               // global fire-and-forget SFX (no Load needed)
    private string _audioBufSig = "";                  // sfx cue-field reset tracker (see DrawAudioInspector)

    // ---- AE editor aids: Invisibox / Clickbar / Timer / Hide Fader (see Button_Mute* in the
    // decompiled Dialogger_EditorManager — these are real AE controls, not our own inventions).
    private bool _invisoboxOn;         // real scene command (Invisibox on page 0) + dm.invisoboxGlobal
    private bool _clickBarOn = true;   // AE's "click bar" = the on-render click-to-drag/select gizmo
    private bool _respectTimerOn;      // dm.respectTimer — whether manual Next-page waits out an active Timer

    // ---- branching (Label/Redirect/Buttons) floating window ----
    private bool _branchOpen;
    private Rect _branchRect;
    private const int BRANCH_WIN_ID = 918274;
    private Vector2 _branchScroll;
    private string _labelName = "";
    private string _redirectName = "";
    private readonly string[] _btnText = new string[3] { "", "", "" };
    private readonly string[] _btnTarget = new string[3] { "", "", "" };
    private readonly int[] _btnStyle = new int[3];
    private bool _btnVertical = true;
    private string _btnDesc = "";

    // Shared layout so the render-area hit-test (HandleDrag) and the guide overlay agree with the panels.
    private const float LeftPanelW = 276f;
    private const float BottomBarH = 172f;

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
                if (HasStaffAccess()) { _open = !_open; _playing = false; }
                else { _open = false; InfinityLoaderMod.SafeLog("[cutedit] F8 denied: staff access 100 required"); }
            }
            if (_playing && Input.GetKeyDown(KeyCode.Escape)) _playing = false;
        }
        catch { }
        if (_open && _loaded)
        {
            // AE's "click bar" gates its own transform gizmo; we have no gizmo (direct click-drag on
            // the render instead), so this toggle gates that interaction to preserve the same intent.
            if (!_playing && _clickBarOn)
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
        // mousePosition y is measured from the BOTTOM: exclude the left panel and the bottom toolbar.
        bool inRender = mp.x >= LeftPanelW + 4f && mp.y > BottomBarH + 4f;

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
        if (best == null)
        {
            // Animated humanoids can briefly have no stable renderer AABB while states swap.
            // Fall back to the nearest visible actor pivot within a forgiving screen radius.
            float nearest = 110f;
            foreach (var mt in dm.actorList)
            {
                if (mt == null || mt.isBG || !mt.isVisible || mt.tt == null) continue;
                Vector3 sp = Camera.main.WorldToScreenPoint(mt.tt.position); float d = Vector2.Distance(new Vector2(mp.x, mp.y), new Vector2(sp.x, sp.y));
                if (d < nearest) { nearest = d; best = mt; }
            }
        }
        return best;
    }

    private Vector2 CurrentObjPos()
    {
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, SelPrefix());
        string cmd = i >= 0 ? dm.dData.frames[_page][i] : PrevStateCmd(SelPrefix());
        var f = Body(cmd);
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
        if (i < 0 && _autoKey && _page >= 1)
        {
            // Auto-key: dragging an object that has no key on this page creates one from its
            // previous-page state (AE re-records the page on every edit; this is the same net effect).
            AddKeyToPage(_selKind, _selId);
            AutoAnimKeyForSelected();
            i = FindIdx(_page, SelPrefix());
            if (i >= 0) InfinityLoaderMod.SafeLog("[cutedit/autokey] keyed " + _selKind + " #" + _selId + " on page " + _page + " (drag)");
        }
        if (i < 0) return;
        var f = Body(dm.dData.frames[_page][i]);
        if (f == null || f.Length < 7) return;
        f[5] = x.ToString("F2", System.Globalization.CultureInfo.InvariantCulture);
        f[6] = y.ToString("F2", System.Globalization.CultureInfo.InvariantCulture);
        dm.dData.frames[_page][i] = Rebuild("Object", f);
        try { dm.LoadPage(_page); } catch { }
    }

    // Rule-of-thirds grid + center cross over the render area — purely a framing aid, writes
    // nothing to the scene. AE's own "screenGuide" toggles visibility of scene objects tagged
    // "Cutscene Guide" that don't exist in this build, so we draw the grid ourselves instead.
    private void DrawScreenGuides()
    {
        if (_txGuide == null) _txGuide = Tex(Color.white);
        float left = LeftPanelW, top = 0f, w = Screen.width - LeftPanelW, h = Screen.height - BottomBarH;
        Color line = new Color(1f, 1f, 1f, 0.30f);
        GuideLine(new Rect(left + w / 3f, top, 1f, h), line);
        GuideLine(new Rect(left + w * 2f / 3f, top, 1f, h), line);
        GuideLine(new Rect(left, top + h / 3f, w, 1f), line);
        GuideLine(new Rect(left, top + h * 2f / 3f, w, 1f), line);
        Color cross = new Color(1f, 0.85f, 0.2f, 0.55f);
        float cx = left + w / 2f, cy = top + h / 2f;
        GuideLine(new Rect(cx - 10f, cy, 20f, 1f), cross);
        GuideLine(new Rect(cx, cy - 10f, 1f, 20f), cross);
    }

    private void GuideLine(Rect r, Color c)
    {
        var old = GUI.color; GUI.color = c;
        GUI.DrawTexture(r, _txGuide);
        GUI.color = old;
    }

    private static readonly string[] _cats = { "Actors", "BGs", "Boxes", "Audio" };

    private void OnGUI()
    {
        if (!_open || !HasStaffAccess()) return;
        try
        {
            EnsureStyles();
            if (_playing) { DrawPlaybackBar(); return; }
            DrawLeftPanel();
            if (_loaded) DrawBottomBar();
            if (_loaded && _showGuides) DrawScreenGuides();
            if (_browseOpen) DrawBrowser();
            if (_branchOpen) DrawBranchWindowOuter();
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] OnGUI " + ex.Message); }
    }

    // ---- left panel: header + object list + inspector ------------------------
    private void DrawLeftPanel()
    {
        const float w = LeftPanelW;
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
            // AE-style per-row delete: only on the selected row so a stray click can't nuke an actor.
            if (sel && GUILayout.Button("✕", _sBtnOrange, GUILayout.Width(24)))
            {
                DeleteSelected();
                GUILayout.EndHorizontal();
                return;
            }
            if (kind != "audio")   // audio has no per-page Object{}/Box{} visibility to toggle
            {
                bool vis = IsVisibleOnPage(kind, id);
                if (GUILayout.Button(vis ? "◉" : "○", _sEye, GUILayout.Width(24))) ToggleVisible(kind, id);
            }
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
        if (i < 0 && _autoKey && _page >= 1)
        {
            AddKeyToPage(kind, id);
            i = FindIdx(_page, (kind == "box" ? "Box{" : "Object{") + id + "|");
        }
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
        if (_selKind == "audio")
        {
            // Music/sfx aren't per-page keyed state (no Object{}/Box{}/Camera{}) — they're one-shot
            // page EVENTS like Timer/Fade, so they skip the buffered LoadBuf/ApplyBuf pipeline entirely.
            GUILayout.Label("AUDIO #" + _selId + "   ·   page " + _page, _sSection);
            DrawAudioInspector();
            return;
        }
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
        if (_buf.ContainsKey("__ghost"))
            GUILayout.Label("No key on this page — the first edit auto-keys it here.", _sMuted);

        if (_selKind == "obj")
        {
            NudgeRow(5, 6);
            PosRow(5, 6);
            DrawMovementTimeline();
            SliderRow("Rotation", 10, -180f, 180f);
            FieldSet("Scale", 4); FieldSet("Z Order", 3); TweenRow(8); ShakeRow(8); FieldSet("Tint", 9);
            GUILayout.BeginHorizontal(); ToggleBtn("Visible", 1, "1", "0"); ToggleBtn("Face L", 2, "-1", "1"); GUILayout.EndHorizontal();
            DrawAnimationInspector();
        }
        else if (_selKind == "box")
        {
            if (_buf.ContainsKey("speaker")) { GUILayout.Label("Speaker", _sLabel); _buf["speaker"] = GUILayout.TextField(_buf["speaker"] ?? "", _sField); }
            if (_buf.ContainsKey("subtitle")) { GUILayout.Label("Subtitle", _sLabel); _buf["subtitle"] = GUILayout.TextField(_buf["subtitle"] ?? "", _sField); }
            if (_buf.ContainsKey("f8")) { GUILayout.Label("Dialog text", _sLabel); _buf["f8"] = GUILayout.TextArea(_buf["f8"] ?? "", _sField, GUILayout.Height(52)); }
            if (GUILayout.Button("Apply text", _sBtnBlue)) ApplyBuf();
            GUILayout.Space(3);
            NudgeRow(1, 2);
            PosRow(1, 2);
            FieldSet("Scale", 3); FieldSet("Width", 5); FieldSet("Height", 6); FieldSet("Font size", 11);
            GUILayout.Label("Colors (hex)", _sSection);
            FieldSet("Plate", 12); FieldSet("Name", 13); FieldSet("Text", 15); FieldSet("Body", 14);
            // f16 shownLead: -1 = no tail, else an index into a prefab-defined anchor array whose length
            // isn't visible from IL — official cutscenes use values up to 31, so clamp well above that.
            // ApplyBuf's LoadPage call is try/caught, so an out-of-range pick fails safely, not a crash.
            StepRow("Tail anchor", 16, -1, 40);
            StepRow("Arrow style", 17, 0, 4);      // f17 leadPos: the tail/arrow style
            // f18 skin: real official content never varies this from 0, but Dialogger_BoxController.
            // ChangeSkin bounds-checks internally (`if (skins.Length >= sk)`) — an out-of-range pick
            // is a silent no-op, not even a caught exception, so a generous stepper is fully safe.
            StepRow("Skin", 18, 0, 8);
            GUILayout.BeginHorizontal(); ToggleBtn("Visible", 4, "1", "0"); GUILayout.EndHorizontal();
        }
        else // cam
        {
            int len = _buf.ContainsKey("__len") ? int.Parse(_buf["__len"]) : 0;
            if (len >= 7) { FieldSet("Zoom", 0); PosRow(1, 2); FieldSet("Rotation", 4); TweenRow(5); ShakeRow(5); }
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

    private void DrawMovementTimeline()
    {
        GUILayout.Space(4); GUILayout.Label("MOVEMENT", _sSection);
        if (_page <= 1) { GUILayout.Label("Clone this page first, then drag the actor to its destination.", _sMuted); return; }
        GUILayout.Label("Target is this page's Position. Source is the previous page.", _sMuted);
        GUILayout.BeginHorizontal();
        GUILayout.Label("Duration", _sLabel, GUILayout.Width(52));
        _moveSec = GUILayout.TextField(_moveSec ?? "1", _sField, GUILayout.Width(42));
        GUILayout.Label("sec", _sMuted, GUILayout.Width(22));
        if (GUILayout.Button("Move here", _sBtnBlue)) SetMoveFromPrevious();
        if (GUILayout.Button("Preview", _sBtn, GUILayout.Width(56))) StartCoroutine(PreviewMove());
        GUILayout.EndHorizontal();
    }

    private void SetMoveFromPrevious()
    {
        var dm = Dialogger_Manager.instance; int i = FindIdx(_page, SelPrefix()); float seconds;
        if (dm == null || i < 0 || !float.TryParse(_moveSec, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out seconds)) { _status = "enter a valid move duration"; return; }
        seconds = Mathf.Max(0.01f, seconds); _moveSec = seconds.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
        var f = Body(dm.dData.frames[_page][i]); if (f == null || f.Length <= 8) return;
        f[8] = _moveSec + " 3"; // smooth easing
        dm.dData.frames[_page][i] = Rebuild("Object", f);
        UpsertPageTimer(seconds);
        _bufSig = ""; _status = "move set: previous page -> this target in " + _moveSec + "s";
        StartCoroutine(PreviewMove());
    }

    private void UpsertPageTimer(float seconds)
    {
        UpsertPageCmd("Timer{", "Timer{" + seconds.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture) + "}");
    }

    // Replace the current page's existing command with this prefix (if any), else append. Used for
    // one-per-page commands (Timer, Label, Redirect, Buttons) where re-editing shouldn't pile up dupes.
    private void UpsertPageCmd(string prefix, string cmd)
    {
        var frame = Dialogger_Manager.instance.dData.frames[_page];
        for (int i = 0; i < frame.Count; i++) if (frame[i] != null && frame[i].StartsWith(prefix)) { frame[i] = cmd; return; }
        frame.Add(cmd);
    }

    private IEnumerator PreviewMove()
    {
        var dm = Dialogger_Manager.instance; if (dm == null || _page <= 1) yield break;
        int targetPage = _page;
        EnsureAllActorAnimationMetadata(dm); dm.LoadPage(targetPage - 1); HoldPageAnimations(dm, targetPage - 1);
        yield return null;
        dm.LoadPage(targetPage); HoldPageAnimations(dm, targetPage);
        _status = "previewing move into page " + targetPage;
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

    // Shake shares the same field as Tween — it's just trailing tokens 2-4 of that same string
    // ("speed ease [power duration kind]", per Dialogger_MovementTransform.Shake/LateUpdate).
    // kind: 1=both axes, 2=horizontal only, 3=vertical only.
    private static readonly string[] _shakeKindNames = { "Off", "Both", "Horizontal", "Vertical" };
    private void ShakeRow(int idx)
    {
        string k = "f" + idx; if (!_buf.ContainsKey(k)) return;
        var toks = (_buf[k] ?? "").Split(' ');
        float power = 0f, duration = 0f; int kind = 0;
        if (toks.Length > 2) float.TryParse(toks[2], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out power);
        if (toks.Length > 3) float.TryParse(toks[3], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out duration);
        if (toks.Length > 4) int.TryParse(toks[4], out kind);

        GUILayout.BeginHorizontal();
        GUILayout.Label("Shake", _sLabel, GUILayout.Width(60));
        string powStr = power.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
        string durStr = duration.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
        GUILayout.Label("pow", _sMuted, GUILayout.Width(24));
        string newPow = GUILayout.TextField(powStr, _sField, GUILayout.Width(36));
        GUILayout.Label("sec", _sMuted, GUILayout.Width(24));
        string newDur = GUILayout.TextField(durStr, _sField, GUILayout.Width(36));
        GUILayout.EndHorizontal();
        GUILayout.BeginHorizontal();
        GUILayout.Space(64);
        for (int i = 1; i <= 3; i++)
        {
            bool sel = kind == i;
            if (GUILayout.Button(_shakeKindNames[i], sel ? _sBtnOrange : _sBtn)) SetShake(idx, toks, newPow, newDur, i);
        }
        if (GUILayout.Button("Clear", _sBtn, GUILayout.Width(44))) SetShake(idx, toks, "0", "0", 0);
        GUILayout.EndHorizontal();
        if (kind > 0 && (newPow != powStr || newDur != durStr)) SetShake(idx, toks, newPow, newDur, kind);
    }

    private void SetShake(int idx, string[] toks, string pow, string dur, int kind)
    {
        string speed = toks.Length > 0 ? toks[0] : "-1";
        string ease = toks.Length > 1 ? toks[1] : "0";
        _buf["f" + idx] = kind > 0 ? (speed + " " + ease + " " + pow + " " + dur + " " + kind) : (speed + " " + ease);
        ApplyBuf();
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

    // Native grammar: Actor{id|animation[#secondary]|speed|normalizedStartTime}.
    private static readonly string[] _animPresets = { "Idle", "Walk", "Run", "Attack1", "Attack2", "Die1" };
    private void DrawAnimationInspector()
    {
        GUILayout.Space(6); GUILayout.Label("ANIMATION", _sSection);
        GUILayout.Label("Clip name (primary#secondary for layered actors)", _sMuted);
        if (!_buf.ContainsKey("__anim")) _buf["__anim"] = "";
        if (!_buf.ContainsKey("__animSpeed")) _buf["__animSpeed"] = "1";
        if (!_buf.ContainsKey("__animStart")) _buf["__animStart"] = "0";
        _buf["__anim"] = GUILayout.TextField(_buf["__anim"] ?? "", _sField);
        GUILayout.BeginHorizontal();
        GUILayout.Label("Speed", _sLabel, GUILayout.Width(42)); _buf["__animSpeed"] = GUILayout.TextField(_buf["__animSpeed"], _sField, GUILayout.Width(42));
        GUILayout.Label("Start", _sLabel, GUILayout.Width(36)); _buf["__animStart"] = GUILayout.TextField(_buf["__animStart"], _sField, GUILayout.Width(42));
        if (GUILayout.Button("Play / Set", _sBtnBlue)) ApplyAnimation();
        GUILayout.EndHorizontal();
        DrawDiscoveredClips();
        if (FindIdx(_page, "Actor{" + _selId + "|") >= 0 && GUILayout.Button("Clear animation on this page", _sBtnOrange)) ClearAnimation();
    }

    // ---- animation discovery: list the REAL state/clip names from the actor's Animator
    // controller(s) instead of guessing. Primary clicks keep any layered secondary; clicking a
    // secondary clip composes "primary#secondary" per the shipped Actor grammar.
    // Above this many discovered clips, don't dump the whole list — the shared Player/Redux
    // Animator controllers carry 100-180+ states (per harvested dumps), which is an unusable wall
    // of buttons. Bespoke actor rigs (backgrounds, one-off NPCs) stay under this and show in full,
    // matching how official cutscenes actually use them (small, browsable lists).
    private const int ClipGridThreshold = 24;

    private void DrawDiscoveredClips()
    {
        EnsureAnimClipCache();
        bool any = _animClips.Length > 0 || _animClips2.Length > 0;
        // These six cover the overwhelming majority of real usage — across our harvested official
        // cutscenes "Idle" alone is nearly a third of every Actor{} command — so they stay visible
        // even when the full list is gated behind a filter below.
        GUILayout.BeginHorizontal();
        foreach (string preset in _animPresets) if (GUILayout.Button(preset, _sBtn)) { _buf["__anim"] = preset; ApplyAnimation(); }
        if (GUILayout.Button("↻", _sBtn, GUILayout.Width(26))) _animClipSig = "";   // retry after async load
        GUILayout.EndHorizontal();
        if (!any)
        {
            GUILayout.Label("No Animator clips discovered yet (presets shown). ↻ retries.", _sMuted);
            return;
        }

        int total = _animClips.Length + _animClips2.Length;
        bool large = total > ClipGridThreshold;

        GUILayout.BeginHorizontal();
        GUILayout.Label("Filter", _sLabel, GUILayout.Width(36));
        _animFilter = GUILayout.TextField(_animFilter ?? "", _sField);
        if (GUILayout.Button("×", _sBtn, GUILayout.Width(26))) _animFilter = "";
        GUILayout.EndHorizontal();

        if (large && _animTags.Length > 0)
        {
            GUILayout.Label(total + " clips on this actor — click a tag or type above to narrow:", _sMuted);
            string cur = (_animFilter ?? "").Trim();
            int col = 0;
            GUILayout.BeginHorizontal();
            foreach (string tag in _animTags)
            {
                if (col == 6) { GUILayout.EndHorizontal(); GUILayout.BeginHorizontal(); col = 0; }
                bool sel = cur.Equals(tag, StringComparison.OrdinalIgnoreCase);
                if (GUILayout.Button(tag, sel ? _sBtnOrange : _sBtn)) _animFilter = tag;
                col++;
            }
            GUILayout.EndHorizontal();
        }

        if (large && (_animFilter ?? "").Trim().Length == 0)
        {
            GUILayout.Label("Type a filter or click a tag above to browse the full list.", _sMuted);
            return;
        }

        if (_animClips.Length > 0)
        {
            GUILayout.Label("Clips (" + _animClips.Length + ")", _sMuted);
            DrawClipGrid(_animClips, false);
        }
        if (_animClips2.Length > 0)
        {
            GUILayout.Label("Layer-2 clips (" + _animClips2.Length + ") — compose primary#secondary", _sMuted);
            DrawClipGrid(_animClips2, true);
        }
    }

    private void DrawClipGrid(string[] clips, bool secondary)
    {
        string filter = (_animFilter ?? "").Trim();
        int shown = 0, col = 0;
        const int MAX = 60, COLS = 3;
        GUILayout.BeginHorizontal();
        foreach (string clip in clips)
        {
            if (filter.Length > 0 && clip.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0) continue;
            if (shown >= MAX) { GUILayout.EndHorizontal(); GUILayout.Label("… narrow the filter to see more", _sMuted); return; }
            if (col == COLS) { GUILayout.EndHorizontal(); GUILayout.BeginHorizontal(); col = 0; }
            if (GUILayout.Button(clip, _sBtn))
            {
                string cur = _buf.ContainsKey("__anim") ? (_buf["__anim"] ?? "") : "";
                var parts = cur.Split(new[] { '#' }, 2);
                if (secondary) _buf["__anim"] = (parts[0].Length > 0 ? parts[0] : "Idle") + "#" + clip;
                else _buf["__anim"] = parts.Length > 1 && parts[1].Length > 0 ? clip + "#" + parts[1] : clip;
                ApplyAnimation();
            }
            shown++; col++;
        }
        GUILayout.EndHorizontal();
        if (shown == 0) GUILayout.Label("no clips match the filter", _sMuted);
    }

    private void EnsureAnimClipCache()
    {
        string sig = _selKind + ":" + _selId;
        if (sig == _animClipSig) return;
        _animClipSig = sig;
        _animClips = new string[0]; _animClips2 = new string[0]; _animTags = new string[0];
        if (_selKind != "obj") return;
        int id; if (!int.TryParse(_selId, out id)) return;
        var dm = Dialogger_Manager.instance; if (dm == null) return;
        var mt = dm.GetActorFromID(id);
        EnsureActorAnimationMetadata(mt, id);
        if (mt == null || mt.dmd == null) return;
        _animClips = ClipNames(mt.dmd.anim);
        _animClips2 = ClipNames(mt.dmd.anim2);
        var combined = new string[_animClips.Length + _animClips2.Length];
        _animClips.CopyTo(combined, 0); _animClips2.CopyTo(combined, _animClips.Length);
        _animTags = DeriveTags(combined);
        InfinityLoaderMod.SafeLog("[cutedit/anim] discovered #" + id + " clips=" + _animClips.Length + " layer2=" + _animClips2.Length + " tags=" + _animTags.Length);
    }

    private static string[] ClipNames(Animator animator)
    {
        if (animator == null || animator.runtimeAnimatorController == null) return new string[0];
        var names = new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var clip in animator.runtimeAnimatorController.animationClips)
            if (clip != null && !string.IsNullOrEmpty(clip.name)) names.Add(clip.name);
        var arr = new string[names.Count]; names.CopyTo(arr); return arr;
    }

    // Group clip names by their leading word (letters only, case-insensitive), so Redux/player-scale
    // controllers with 100+ states (e.g. Rogue_Attack1..4, DuelWieldAttack1..4) collapse into a
    // handful of clickable tags instead of one flat wall of buttons. Verified against the shared
    // Player controller's actual 181-clip naming convention (weapon/class prefix + numbered variants).
    private static string[] DeriveTags(string[] clips)
    {
        var counts = new Dictionary<string, int>(StringComparer.Ordinal);
        foreach (string clip in clips)
        {
            string tag = TagOf(clip);
            if (tag.Length == 0) continue;
            int n; counts.TryGetValue(tag, out n); counts[tag] = n + 1;
        }
        var tags = new List<string>();
        foreach (var kv in counts) if (kv.Value >= 2) tags.Add(kv.Key);
        tags.Sort((a, b) => counts[b] - counts[a]);
        if (tags.Count > 14) tags.RemoveRange(14, tags.Count - 14);
        return tags.ToArray();
    }

    private static string TagOf(string clip)
    {
        var m = System.Text.RegularExpressions.Regex.Match(clip ?? "", "^[A-Za-z]+");
        return m.Success ? m.Value.ToLowerInvariant() : "";
    }

    private void ApplyAnimation()
    {
        var dm = Dialogger_Manager.instance; int id;
        if (dm == null || _page < 1 || !int.TryParse(_selId, out id)) return;
        string anim = (_buf.ContainsKey("__anim") ? _buf["__anim"] : "").Trim();
        if (anim.Length == 0) { _status = "enter an animation clip name"; return; }
        float speed, start;
        if (!float.TryParse(_buf["__animSpeed"], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out speed)) speed = 1f;
        if (!float.TryParse(_buf["__animStart"], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out start)) start = 0f;
        speed = Mathf.Max(0f, speed); start = Mathf.Clamp01(start);
        _buf["__animSpeed"] = speed.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
        _buf["__animStart"] = start.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
        string cmd = "Actor{" + id + "|" + anim + "|" + _buf["__animSpeed"] + "|" + _buf["__animStart"] + "}";
        int i = FindIdx(_page, "Actor{" + id + "|"); if (i >= 0) dm.dData.frames[_page][i] = cmd; else dm.dData.frames[_page].Add(cmd);
        EnsureActorAnimationMetadata(dm.GetActorFromID(id), id);
        EnsureNextPageAnimationReset(id, anim);
        string[] clips = anim.Split(new[] { '#' }, 2);
        try
        {
            dm.ReadCommand_Actor(id, clips[0], clips.Length > 1 ? clips[1] : "", speed, start, true);
            var mt = dm.GetActorFromID(id);
            HoldActorAnimation(mt, clips[0], speed, start);
            // Clip name and Animator STATE name aren't always the same (documented mismatch on at
            // least the Void kit) — Play() silently no-ops on a bad name, so confirm it actually took.
            if (StateNameMatches(mt, clips[0]))
                _status = "animation " + anim + " set on #" + id + " for page " + _page;
            else
            {
                _status = "set '" + clips[0] + "', but the Animator's current state doesn't match — clip/state names can differ (see log)";
                InfinityLoaderMod.SafeLog("[cutedit/anim] WARNING #" + id + " clip '" + clips[0] + "' is not the resulting Animator state");
            }
        }
        catch (Exception ex)
        {
            try { dm.LoadPage(_page); } catch { }
            InfinityLoaderMod.SafeLog("[cutedit] animation preview " + ex.Message);
            _status = "animation preview error: " + ex.Message;
        }
    }

    private static bool StateNameMatches(Dialogger_MovementTransform mt, string clip)
    {
        try
        {
            if (mt == null || mt.dmd == null || mt.dmd.anim == null || string.IsNullOrEmpty(clip)) return true;
            return mt.dmd.anim.GetCurrentAnimatorStateInfo(0).IsName(clip);
        }
        catch { return true; }   // best-effort diagnostic only — never block a real edit on this
    }

    private void ClearAnimation()
    {
        var dm = Dialogger_Manager.instance; int id, i = FindIdx(_page, "Actor{" + _selId + "|");
        if (dm == null || i < 0 || !int.TryParse(_selId, out id)) return;
        dm.dData.frames[_page].RemoveAt(i); _buf["__anim"] = ""; _buf["__animSpeed"] = "1"; _buf["__animStart"] = "0";
        PlayEffectiveAnimation(id, _page); _status = "animation cleared on page " + _page;
    }

    private void EnsureNextPageAnimationReset(int id, string currentAnimation)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || _page + 1 >= dm.dData.frames.Count || FindIdx(_page + 1, "Actor{" + id + "|") >= 0) return;
        string reset = EffectiveAnimationBefore(id, _page);
        if (string.IsNullOrEmpty(reset) || reset == currentAnimation) reset = "Idle";
        dm.dData.frames[_page + 1].Add("Actor{" + id + "|" + reset + "|1|0}");
    }

    private string EffectiveAnimationBefore(int id, int pageExclusive)
    {
        var dm = Dialogger_Manager.instance; if (dm == null) return "Idle";
        for (int p = pageExclusive - 1; p >= 1; p--)
        {
            int i = FindIdx(p, "Actor{" + id + "|"); if (i < 0) continue;
            var f = Body(dm.dData.frames[p][i]);
            if (f != null && f.Length > 1 && !string.IsNullOrEmpty(f[1])) return f[1];
        }
        return "Idle";
    }

    private void PlayEffectiveAnimation(int id, int page)
    {
        var dm = Dialogger_Manager.instance; if (dm == null) return;
        string anim = EffectiveAnimationBefore(id, page + 1); string[] clips = anim.Split(new[] { '#' }, 2);
        var mt = dm.GetActorFromID(id); EnsureActorAnimationMetadata(mt, id);
        try { dm.ReadCommand_Actor(id, clips[0], clips.Length > 1 ? clips[1] : "", 1f, 0f, true); HoldActorAnimation(mt, clips[0], 1f, 0f); } catch { }
    }

    private static void HoldActorAnimation(Dialogger_MovementTransform mt, string clip, float speed, float start)
    {
        if (!EnsureActorAnimationMetadata(mt, mt != null ? mt.ID : -1) || string.IsNullOrEmpty(clip)) return;
        var animationLock = mt.GetComponent<CutsceneActorAnimationLock>();
        if (animationLock == null) animationLock = mt.gameObject.AddComponent<CutsceneActorAnimationLock>();
        animationLock.Hold(mt.dmd.anim, clip, speed, start);
    }

    private static void HoldPageAnimations(Dialogger_Manager dm, int page)
    {
        if (dm == null || dm.dData == null || page < 1 || page >= dm.dData.frames.Count) return;
        foreach (string command in dm.dData.frames[page])
        {
            if (string.IsNullOrEmpty(command) || !command.StartsWith("Actor{")) continue;
            var f = Body(command); int id; float speed = 1f, start = 0f;
            if (f == null || f.Length < 2 || !int.TryParse(f[0], out id)) continue;
            if (f.Length > 2) float.TryParse(f[2], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out speed);
            if (f.Length > 3) float.TryParse(f[3], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out start);
            string primary = f[1].Split(new[] { '#' }, 2)[0]; HoldActorAnimation(dm.GetActorFromID(id), primary, speed, start);
        }
    }
    private static void EnsureAllActorAnimationMetadata(Dialogger_Manager dm)
    {
        if (dm == null || dm.actorList == null) return;
        foreach (var mt in dm.actorList) if (mt != null) EnsureActorAnimationMetadata(mt, mt.ID);
    }
    private static bool EnsureActorAnimationMetadata(Dialogger_MovementTransform mt, int id)
    {
        if (mt == null || mt.dmd == null) return false;
        var animators = mt.GetComponentsInChildren<Animator>(includeInactive: true);
        if (mt.dmd.anim == null && animators.Length > 0) mt.dmd.anim = animators[0];
        if (mt.dmd.anim2 == null && animators.Length > 1) mt.dmd.anim2 = animators[1];
        bool ready = mt.dmd.anim != null;
        InfinityLoaderMod.SafeLog("[cutedit/anim] #" + id + " animator=" + ready + " count=" + animators.Length
            + (ready && mt.dmd.anim.runtimeAnimatorController != null ? " controller=" + mt.dmd.anim.runtimeAnimatorController.name : ""));
        return ready;
    }

    // ---- audio inspector: Music (Load-instanceID-keyed, per BGMusicManager.CustomBGMTracks) and
    // sfx (order-indexed into Dialogger_Manager.sfxList, per SFXPlayer/ReadCommand_Sound). Neither
    // has a persistent Object{} key — Play/Stop just append one-shot commands, like Timer/Fade.
    private void DrawAudioInspector()
    {
        int id; if (!int.TryParse(_selId, out id)) return;
        string type = LoadTypeOf(_selId);
        if (type == "music")
        {
            GUILayout.Label("Plays the soundtrack loaded on the setup page.", _sMuted);
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("▶ Play", _sBtnBlue)) AddCmd("Music{Play|" + id + "|}");
            if (GUILayout.Button("■ Stop", _sBtn)) AddCmd("Music{Stop|" + id + "|}");
            GUILayout.EndHorizontal();
            if (GUILayout.Button("Override Map Music (this page)", _sBtn)) AddCmd("OverrideMapMusic");
        }
        else if (type == "sfx")
        {
            // Audio bypasses the shared _bufSig pipeline (see DrawInspectorBody), so these two keys
            // need their own reset-on-selection-change check to avoid showing the last-picked cue.
            string sig = "sfx:" + _selId;
            if (sig != _audioBufSig) { _buf["__sfxName"] = ""; _buf["__sfxDelay"] = "0"; _audioBufSig = sig; }
            int idx = SfxListIndex(_selId);
            GUILayout.Label(idx >= 0 ? ("sound-list index " + idx) : "not loaded yet — Refresh once loading finishes", _sMuted);
            DrawSfxClipPicker(idx);
            GUILayout.BeginHorizontal();
            GUILayout.Label("Cue", _sLabel, GUILayout.Width(32));
            _buf["__sfxName"] = GUILayout.TextField(_buf["__sfxName"] ?? "", _sField);
            GUILayout.EndHorizontal();
            GUILayout.BeginHorizontal();
            GUILayout.Label("Delay", _sLabel, GUILayout.Width(40));
            _buf["__sfxDelay"] = GUILayout.TextField(_buf["__sfxDelay"] ?? "0", _sField, GUILayout.Width(42));
            GUILayout.Label("sec", _sMuted, GUILayout.Width(24));
            if (GUILayout.Button("▶ Play", _sBtnBlue))
            {
                string name = (_buf["__sfxName"] ?? "").Trim();
                if (name.Length == 0 || idx < 0) _status = "pick a cue name first (or wait for the bundle to finish loading)";
                else AddCmd("Sound{" + idx + "|" + name + "|Play|" + (_buf["__sfxDelay"] ?? "0") + "}");
            }
            if (GUILayout.Button("■ Stop", _sBtn))
            {
                string name = (_buf["__sfxName"] ?? "").Trim();
                if (name.Length > 0 && idx >= 0) AddCmd("Sound{" + idx + "|" + name + "|Stop}");
            }
            GUILayout.EndHorizontal();
        }
    }

    private void DrawSfxClipPicker(int idx)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || idx < 0 || idx >= SafeCount(dm.sfxList))
        {
            GUILayout.Label("Cue names appear here once the sfx bundle finishes loading.", _sMuted);
            return;
        }
        var player = dm.sfxList[idx];
        if (player == null || player.MixerTracks == null || player.MixerTracks.Count == 0)
        {
            GUILayout.Label("This bundle exposes no named cues — type the cue name directly.", _sMuted);
            return;
        }
        GUILayout.Label("Cues in this bundle:", _sMuted);
        int col = 0;
        GUILayout.BeginHorizontal();
        foreach (var track in player.MixerTracks)
        {
            if (track == null || track.Clip == null) continue;
            if (col == 3) { GUILayout.EndHorizontal(); GUILayout.BeginHorizontal(); col = 0; }
            if (GUILayout.Button(track.Clip.name, _sBtn)) _buf["__sfxName"] = track.Clip.name;
            col++;
        }
        GUILayout.EndHorizontal();
    }

    // The Load{} setup-frame entry is the only place an audio object's type is recorded.
    private static string LoadTypeOf(string id)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || dm.dData.frames.Count == 0) return null;
        foreach (string cmd in dm.dData.frames[0])
        {
            if (string.IsNullOrEmpty(cmd) || !cmd.StartsWith("Load{")) continue;
            var f = Body(cmd);
            if (f != null && f.Length > 2 && f[0] == id) return f[2];
        }
        return null;
    }

    // Sound{}'s first field is NOT the Load instanceID — it's the 0-based rank of this sfx Load
    // among ALL "sfx"-type Load{} entries in frame 0, matching the order Dialogger_Manager.sfxList
    // is actually populated in (ProcessLoadCommands processes frame 0 top-to-bottom).
    private static int SfxListIndex(string id)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || dm.dData.frames.Count == 0) return -1;
        int idx = 0;
        foreach (string cmd in dm.dData.frames[0])
        {
            if (string.IsNullOrEmpty(cmd) || !cmd.StartsWith("Load{")) continue;
            var f = Body(cmd);
            if (f == null || f.Length < 3 || f[2] != "sfx") continue;
            if (f[0] == id) return idx;
            idx++;
        }
        return -1;
    }

    // ---- add a NEW asset to the scene (Load on frame 0 + Object on this page) --
    private static readonly string[] _addTypes = { "actor", "npc", "player", "bg", "music", "sfx" };
    private static string AddHint(string t)
    {
        if (t == "actor") return "bundleId,PrefabName  (e.g. 66131,actor-veddrian)";
        if (t == "npc") return "monster / npc id  (e.g. 262)";
        if (t == "bg") return "image filename";
        if (t == "music") return "soundtrack id (numeric)";
        if (t == "sfx") return "bundleId,PrefabName  (e.g. 12345,sfx-something)";
        return "";
    }

    private void DrawAddObject()
    {
        GUILayout.Label("ADD OBJECT", _sSection);
        // Wrapped into rows of 3 — 6 types no longer fit one row in the 276px panel.
        int col = 0;
        GUILayout.BeginHorizontal();
        foreach (var t in _addTypes)
        {
            if (col == 3) { GUILayout.EndHorizontal(); GUILayout.BeginHorizontal(); col = 0; }
            if (GUILayout.Button(t, _addType == t ? _sBtnBlue : _sBtn, GUILayout.Width(80))) _addType = t;
            col++;
        }
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
        // No harvested catalog exists for soundtrack ids / sfx bundles — direct entry only.
        if (_addType != "music" && _addType != "sfx" && GUILayout.Button("Browse…", _sBtn, GUILayout.Width(72)))
        {
            string wanted = _addType == "bg" ? "background" : (_addType == "actor" ? "actor" : "npc");
            if (_browseMode != wanted) { _browseMode = wanted; _browseResults.Clear(); }
            _browseOpen = true; if (_browseResults.Count == 0) BrowseSearch();
        }
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
        if (GUILayout.Button("NPCs", _browseMode == "npc" ? _sBtnBlue : _sBtn)) { _browseMode = "npc"; _browseQuery = ""; BrowseSearch(); }
        if (GUILayout.Button("Backgrounds", _browseMode == "background" ? _sBtnBlue : _sBtn)) { _browseMode = "background"; _browseQuery = ""; BrowseSearch(); }
        if (GUILayout.Button("Actors", _browseMode == "actor" ? _sBtnBlue : _sBtn)) { _browseMode = "actor"; _browseQuery = ""; BrowseSearch(); }
        GUILayout.EndHorizontal();
        GUILayout.BeginHorizontal();
        _browseQuery = GUILayout.TextField(_browseQuery ?? "", _sField);
        if (GUILayout.Button("Clear", _sBtn, GUILayout.Width(48))) { _browseQuery = ""; BrowseSearch(); }
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
            string kind = _browseMode == "npc" ? "" : ("&kind=" + Uri.EscapeDataString(_browseMode));
            string query = ep + "?q=" + Uri.EscapeDataString(_browseQuery ?? "") + kind;
            string json = "";
            // The public HTTPS host may still be running an older API. Editor catalogs are local
            // server data, so ask the loopback API first and fall back to Main.WebApiURL.
            using (var wc = new WebClient())
            {
                try { json = Encoding.UTF8.GetString(wc.DownloadData("http://127.0.0.1:8182/" + query)); }
                catch { json = Encoding.UTF8.GetString(wc.DownloadData(Main.WebApiURL + query)); }
            }
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

    // ---- branching: Label (jump target) / Redirect (unconditional jump) / Buttons (up to 3 choices)
    // A draggable GUI.Window, same pattern as the asset browser.
    private void DrawBranchWindowOuter()
    {
        float w = 380f, h = Mathf.Min(430f, Screen.height - 90f);
        if (_branchRect.width < 1f)
            _branchRect = new Rect(LeftPanelW + Mathf.Max(12f, (Screen.width - LeftPanelW - w) * 0.5f), 46f, w, h);
        _branchRect.width = w; _branchRect.height = h;
        _branchRect = GUI.Window(BRANCH_WIN_ID, _branchRect, DrawBranchWindow, "", _sPanel);
    }

    private void DrawBranchWindow(int winId)
    {
        GUILayout.BeginHorizontal();
        GUILayout.Label("Branching — page " + _page, _sHeader);
        GUILayout.FlexibleSpace();
        if (GUILayout.Button("✕", _sBtn, GUILayout.Width(30))) _branchOpen = false;
        GUILayout.EndHorizontal();

        if (_page < 1)
        {
            GUILayout.Label("Page 0 is the invisible setup page — branching only applies to real content pages.", _sMuted);
            GUI.DragWindow(new Rect(0, 0, _branchRect.width, 30f));
            return;
        }

        _branchScroll = GUILayout.BeginScrollView(_branchScroll);

        GUILayout.Label("LABEL (jump target)", _sSection);
        GUILayout.Label("Marks this page so a Redirect or Buttons option elsewhere can jump straight here.", _sMuted);
        GUILayout.BeginHorizontal();
        _labelName = GUILayout.TextField(_labelName ?? "", _sField);
        if (GUILayout.Button("Set", _sBtnBlue, GUILayout.Width(44)) && (_labelName ?? "").Trim().Length > 0)
        {
            UpsertPageCmd("Label{", "Label{" + _labelName.Trim() + "}");
            _status = "labeled page " + _page + " as '" + _labelName.Trim() + "'";
        }
        GUILayout.EndHorizontal();
        string existingLabel = LabelOnPage(_page);
        if (existingLabel != null) GUILayout.Label("Current label on this page: " + existingLabel, _sMuted);

        GUILayout.Space(6);
        GUILayout.Label("REDIRECT (unconditional jump)", _sSection);
        GUILayout.Label("When this page is reached, jump straight to the named label instead of the next page.", _sMuted);
        GUILayout.BeginHorizontal();
        _redirectName = GUILayout.TextField(_redirectName ?? "", _sField);
        if (GUILayout.Button("Set", _sBtnBlue, GUILayout.Width(44)) && (_redirectName ?? "").Trim().Length > 0)
        {
            UpsertPageCmd("Redirect{", "Redirect{" + _redirectName.Trim() + "}");
            _status = "page " + _page + " redirects to '" + _redirectName.Trim() + "'";
        }
        if (GUILayout.Button("Clear", _sBtn, GUILayout.Width(50)))
        { RemoveCmdEverywhere("Redirect{", _page, _page); _status = "redirect cleared on page " + _page; }
        GUILayout.EndHorizontal();

        GUILayout.Space(6);
        GUILayout.Label("BUTTONS (up to 3 dialogue choices)", _sSection);
        for (int i = 0; i < 3; i++)
        {
            GUILayout.BeginHorizontal();
            GUILayout.Label("Opt " + (i + 1), _sLabel, GUILayout.Width(40));
            _btnText[i] = GUILayout.TextField(_btnText[i] ?? "", _sField, GUILayout.Width(108));
            GUILayout.Label("→", _sMuted, GUILayout.Width(14));
            _btnTarget[i] = GUILayout.TextField(_btnTarget[i] ?? "", _sField, GUILayout.Width(64));
            if (GUILayout.Button("style " + _btnStyle[i], _sBtn, GUILayout.Width(58))) _btnStyle[i] = (_btnStyle[i] + 1) % 5;
            GUILayout.EndHorizontal();
        }
        if (GUILayout.Button(_btnVertical ? "Layout: Vertical" : "Layout: Horizontal", _sBtn, GUILayout.Width(140))) _btnVertical = !_btnVertical;
        GUILayout.Label("Description (optional)", _sMuted);
        _btnDesc = GUILayout.TextField(_btnDesc ?? "", _sField);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Set Branch Options", _sBtnBlue)) ApplyButtons();
        if (GUILayout.Button("Clear", _sBtnOrange, GUILayout.Width(50)))
        { RemoveCmdEverywhere("Buttons{", _page, _page); _status = "branch options cleared on page " + _page; }
        GUILayout.EndHorizontal();
        GUILayout.Label("Current: " + (FindIdx(_page, "Buttons{") >= 0 ? "set on this page" : "none on this page"), _sMuted);

        GUILayout.Space(6);
        GUILayout.Label("LABELS IN THIS SCENE", _sSection);
        var labels = AllLabels();
        if (labels.Count == 0) GUILayout.Label("none yet — set one above", _sMuted);
        foreach (var kv in labels)
            if (GUILayout.Button(kv.Value + "   (page " + kv.Key + ")", _sRow))
            {
                _redirectName = kv.Value;
                if ((_btnTarget[0] ?? "").Length == 0) _btnTarget[0] = kv.Value;
            }

        GUILayout.EndScrollView();
        GUI.DragWindow(new Rect(0, 0, _branchRect.width, 30f));
    }

    private void ApplyButtons()
    {
        if (_page < 1) { _status = "go to a page >= 1 first"; return; }
        string op1 = _btnText[0] ?? "", op2 = _btnText[1] ?? "", op3 = _btnText[2] ?? "";
        if (op1.Trim().Length == 0 && op2.Trim().Length == 0 && op3.Trim().Length == 0)
        { _status = "enter at least one option's text"; return; }
        var fields = new List<string>
        {
            op1, op2, op3,
            _btnTarget[0] ?? "", _btnTarget[1] ?? "", _btnTarget[2] ?? "",
            _btnStyle[0].ToString(), _btnStyle[1].ToString(), _btnStyle[2].ToString(),
            _btnVertical ? "1" : "0",
        };
        // Colors/icons (fields 10-15) are never set by real official content — only append them
        // (as blanks) when a description is present, since desc (field 16) requires Params.Count>15.
        if (!string.IsNullOrEmpty(_btnDesc))
            fields.AddRange(new[] { "", "", "", "0", "0", "0", _btnDesc });
        string cmd = "Buttons{" + string.Join("|", fields) + "}";
        UpsertPageCmd("Buttons{", cmd);
        _status = "branch options set on page " + _page;
        InfinityLoaderMod.SafeLog("[cutedit/branch] " + cmd);
    }

    private string LabelOnPage(int page)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || page < 0 || page >= dm.dData.frames.Count) return null;
        foreach (string cmd in dm.dData.frames[page])
        {
            if (string.IsNullOrEmpty(cmd) || !cmd.StartsWith("Label{")) continue;
            var f = Body(cmd);
            if (f != null && f.Length > 0) return f[0];
        }
        return null;
    }

    private List<KeyValuePair<int, string>> AllLabels()
    {
        var result = new List<KeyValuePair<int, string>>();
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null) return result;
        for (int p = 0; p < dm.dData.frames.Count; p++)
        {
            string name = LabelOnPage(p);
            if (name != null) result.Add(new KeyValuePair<int, string>(p, name));
        }
        return result;
    }

    private int FindLabelPage(string name)
    {
        foreach (var kv in AllLabels()) if (kv.Value == name) return kv.Key;
        return -1;
    }

    private void AddObject(string link, string type)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null) return;
        if ((type == "actor" || type == "sfx") && !link.Contains(","))
        { _status = type + " needs 'bundleId,PrefabName' (e.g. 66131,actor-veddrian)"; return; }
        if (type == "music" && !int.TryParse(link, out _)) { _status = "music needs a numeric soundtrack id"; return; }
        if (type == "npc") InfinityLoaderMod.EnsureNpcLoaderPatch();
        bool audio = type == "music" || type == "sfx";
        int id = dm.dData.idCount; dm.dData.idCount = id + 1;
        dm.dData.frames[0].Add("Load{" + id + "|" + link + "|" + type + "}");
        // Music/sfx have no visual representation — only actors/backgrounds get an Object{} key.
        // z-order 20 so a visual object renders in FRONT of the background layers (z 0 hides behind
        // the BG). Placed at origin; the author positions it in the inspector.
        if (!audio && _page >= 1) dm.dData.frames[_page].Add("Object{" + id + "|1|1|20|1|0|0|0|-1 0|FFFFFFFF|0|0|1}");
        _selKind = audio ? "audio" : "obj"; _selId = id.ToString(); _bufSig = "";

        _status = "loading " + type + " #" + id + "…";
        InfinityLoaderMod.SafeLog("[cutedit] add " + type + " #" + id + " (" + link + ")");
        if (type == "npc")
        {
            StartCoroutine(LoadNpcObjectDirect(id, link));
            return;
        }

        // Load exactly the way the scene loads its own assets (Dialogger_Manager.ProcessLoadCommands):
        // preload any actor/sfx bundle metadata via AssetBundleDataLoader.Load, THEN ReadCommand_Load
        // in its callback (which only acts while pageNumber==0, so flip it for that call). Music uses
        // its own SoundtrackLoader internally (ReadCommand_Load's "music" branch) — no bundle preload.
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
        StartCoroutine(audio ? WaitThenRenderAudio(id, type) : WaitThenRender(id));
    }

    // Music/sfx never register in actorList, so WaitThenRender's actor-lookup success check doesn't
    // apply — just wait for the load to finish and report a generic status.
    private IEnumerator WaitThenRenderAudio(int id, string type)
    {
        var dm = Dialogger_Manager.instance;
        float t = 0f; while (t < 3f) { if (dm.IsAssetLoadInProgress) break; t += Time.deltaTime; yield return null; }
        float t2 = 0f; while (dm.IsAssetLoadInProgress && t2 < 30f) { t2 += Time.deltaTime; yield return null; }
        yield return new WaitForSeconds(0.25f);
        _bufSig = "";
        _status = "added " + type + " #" + id + " — select it to Play/Stop";
        InfinityLoaderMod.SafeLog("[cutedit] add " + type + " #" + id + " load wait done");
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
        string npcGender = "M";
        try { mb = FetchNpcMonbranch(npcId, out npcGender); }
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
        // A detached cutscene actor must not create the world-NPC interaction/apop button.
        // NPCButton expects map-owned UI state and can abort HumanoidAvatar.setupAndLoad().
        mb.apopID = -1;

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
        Monster character = null;
        try
        {
            character = new Monster(mb.ID, mb, ig: false);
            character.SetGender(npcGender);
            character.init();
            character.showHelm = character.Helm != null;
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
        bool avatarSetupStarted = false;
        bool avatarCreationRetried = false;
        while (t < 30f)
        {
            t += Time.unscaledDeltaTime;
            var ha = avt as HumanoidAvatar;
            if ((ha == null || ha.CC == null) && !avatarCreationRetried && t >= 1f)
            {
                avatarCreationRetried = true;
                InfinityLoaderMod.SafeLog("[cutedit] npc #" + id + " avatar CC missing after 1s; retrying createAvatar");
                character.createAvatar();
                avt = character.GetAvatar();
                if (avt != null) avt.hideFlame = true;
                yield return null;
                continue;
            }
            if (ha != null && ha.CC != null)
            {
                if (!avatarSetupStarted)
                {
                    avatarSetupStarted = true;
                    var setupAvatar = typeof(Entity).GetMethod("setupAvatar",
                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Public);
                    if (setupAvatar != null) setupAvatar.Invoke(character, null);
                    yield return null;
                    continue;
                }

                int renderers = 0;
                try { renderers = ha.CC.gameObject.GetComponentsInChildren<Renderer>(includeInactive: true).Length; } catch { }
                if (ha.allLoaded && renderers > 30 && t > 0.5f)
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

            int prepared = PrepareCutsceneHumanoid(asset);
            int selectedBeforeWrap = CountActiveRenderers(asset);
            var rendererState = CaptureHumanoidRendererState(asset);
            int revealed = InfinityLoaderMod.RevealLoadedHumanoidSlots(asset);
            LogHumanoidSlotInventory(id, asset);
            int enabledBeforeWrap = CountActiveRenderers(asset);
            dm.BumperJumper(asset, "OBJ " + id + " - " + name, id, frombund: false, "O" + id + " " + name);
            var wrapped = dm.GetActorFromID(id);
            if (wrapped != null && wrapped.dmd != null && finalHumanoid != null && finalHumanoid.getAnimator() != null) wrapped.dmd.anim = finalHumanoid.getAnimator();
            EnsureActorAnimationMetadata(wrapped, id);
            PrepareCutsceneHumanoid(asset);
            RestoreHumanoidRendererState(rendererState);
            int enabled = CountActiveRenderers(asset);
            bool humanoidDone = finalHumanoid != null && finalHumanoid.allLoaded;
            InfinityLoaderMod.SafeLog("[cutedit] direct npc #" + id + " prepared renderers=" + prepared + " selectedBeforeWrap=" + selectedBeforeWrap + " revealedSlots=" + revealed + " enabledBeforeWrap=" + enabledBeforeWrap + " enabledAfterWrap=" + enabled + " allLoaded=" + humanoidDone);
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

    private static Monbranch FetchNpcMonbranch(int npcId, out string gender)
    {
        gender = "M";
        string baseUrl = Main.WebApiURL;
        if (string.IsNullOrEmpty(baseUrl)) baseUrl = "https://130-162-189-229.sslip.io/";
        if (!baseUrl.EndsWith("/")) baseUrl += "/";
        string json;
        using (var wc = new WebClient()) json = wc.DownloadString(baseUrl + "data/GetMonsterData?ids=" + npcId);
        var arr = JArray.Parse(json);
        if (arr.Count == 0) return null;
        var obj = arr[0] as JObject;
        if (obj == null) return null;
        string wireGender = Convert.ToString(obj["Gender"] ?? obj["strGender"]);
        if (string.Equals(wireGender, "F", StringComparison.OrdinalIgnoreCase)) gender = "F";
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

    private static void LogHumanoidSlotInventory(int actorId, GameObject asset)
    {
        if (asset == null) return;
        try
        {
            foreach (var slot in asset.GetComponentsInChildren<CustomizableSlot>(includeInactive: true))
            {
                if (slot == null || slot.spriteRenderer == null || slot.spriteRenderer.sprite == null) continue;
                string path = slot.name;
                Transform parent = slot.transform.parent;
                int depth = 0;
                while (parent != null && parent != asset.transform && depth++ < 5)
                {
                    path = parent.name + "/" + path;
                    parent = parent.parent;
                }
                InfinityLoaderMod.SafeLog("[cutedit/slot] #" + actorId + " path=" + path
                    + " sprite=" + slot.spriteRenderer.sprite.name
                    + " enabled=" + slot.spriteRenderer.enabled
                    + " active=" + slot.gameObject.activeInHierarchy);
            }
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit/slot] inventory failed " + ex.Message); }
    }
    private sealed class HumanoidRendererState
    {
        public readonly Dictionary<Renderer, bool> renderers = new Dictionary<Renderer, bool>();
        public readonly Dictionary<GameObject, bool> objects = new Dictionary<GameObject, bool>();
    }

    private static HumanoidRendererState CaptureHumanoidRendererState(GameObject asset)
    {
        var state = new HumanoidRendererState();
        if (asset == null) return state;
        foreach (var renderer in asset.GetComponentsInChildren<Renderer>(includeInactive: true))
        {
            if (renderer == null) continue;
            state.renderers[renderer] = renderer.enabled;
            state.objects[renderer.gameObject] = renderer.gameObject.activeSelf;
        }
        return state;
    }

    private static void RestoreHumanoidRendererState(HumanoidRendererState state)
    {
        if (state == null) return;
        foreach (var pair in state.objects)
            if (pair.Key != null) pair.Key.SetActive(pair.Value);
        foreach (var pair in state.renderers)
            if (pair.Key != null) pair.Key.enabled = pair.Value;
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
        bool ok = false; try { var mt = dm.GetActorFromID(id); ok = mt != null; EnsureActorAnimationMetadata(mt, id); } catch { }
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
        _loaded = true; _selKind = null; _selId = null; _bufSig = ""; _invisoboxOn = false; dm.invisoboxGlobal = false;
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
        const float x = LeftPanelW, h = BottomBarH;
        int total = FrameCount();
        GUILayout.BeginArea(new Rect(x, Screen.height - h, Screen.width - x, h), _sPanel);

        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Setup", _sBtn, GUILayout.Width(52))) Goto(0);
        if (GUILayout.Button("First", _sBtn, GUILayout.Width(48))) Goto(1);
        if (GUILayout.Button("Back", _sBtn, GUILayout.Width(48))) Goto(_page - 1);
        _frameGoto = GUILayout.TextField(_frameGoto ?? "", _sField, GUILayout.Width(38));
        if (GUILayout.Button("Go To", _sBtn, GUILayout.Width(48))) { int g; if (int.TryParse(_frameGoto, out g)) Goto(g); }
        if (GUILayout.Button("Next", _sBtn, GUILayout.Width(48))) AdvanceNext();
        if (GUILayout.Button("End", _sBtn, GUILayout.Width(48))) Goto(total - 1);
        if (GUILayout.Button("New page", _sBtnYellow, GUILayout.Width(80))) ClonePage();
        if (GUILayout.Button("Refresh", _sBtn, GUILayout.Width(62))) StartCoroutine(RefreshPageFromPrevious());
        GUILayout.Label("  " + _page + " / " + (total - 1), _sHeader, GUILayout.Width(84));
        GUILayout.FlexibleSpace();
        if (GUILayout.Button("▶ Play", _sBtnBlue, GUILayout.Width(64))) StartCoroutine(PlaySequence(false));
        if (GUILayout.Button("▶ All", _sBtnBlue, GUILayout.Width(56))) StartCoroutine(PlaySequence(true));
        GUILayout.EndHorizontal();

        GUILayout.Space(3);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Add Bubble", _sBtn, GUILayout.Width(90))) AddBubble();
        if (GUILayout.Button("Blank Page", _sBtn, GUILayout.Width(84))) BlankPage();
        if (GUILayout.Button("Delete Page", _sBtnOrange, GUILayout.Width(92))) DeletePage();
        if (GUILayout.Button("Keyframe All", _sBtnYellow, GUILayout.Width(88))) KeyframeAll();
        if (GUILayout.Button("Remove Key", _sBtnOrange, GUILayout.Width(82))) RemoveSelectedKey();
        if (GUILayout.Button("Add Timer", _sBtn, GUILayout.Width(78))) AddCmd("Timer{" + (_timerSec ?? "2") + "}");
        _timerSec = GUILayout.TextField(_timerSec ?? "2", _sField, GUILayout.Width(32));
        GUILayout.Label("s", _sMuted, GUILayout.Width(10));
        if (GUILayout.Button("Fade To", _sBtn, GUILayout.Width(58))) AddCmd("FadeToBlack");
        if (GUILayout.Button("Fade From", _sBtn, GUILayout.Width(68))) AddCmd("FadeFromBlack");
        if (GUILayout.Button("Cut To", _sBtn, GUILayout.Width(50))) AddCmd("CutToBlack");
        if (GUILayout.Button("Cut From", _sBtn, GUILayout.Width(60))) AddCmd("CutFromBlack");
        GUILayout.FlexibleSpace();
        if (GUILayout.Button(_autoKey ? "Auto-key Active" : "Auto-key Off", _autoKey ? _sBtnOrange : _sBtn, GUILayout.Width(110)))
            _autoKey = !_autoKey;
        if (GUILayout.Button(_autoAnimKey ? "Auto-anim key On" : "Auto-anim key Off", _autoAnimKey ? _sBtnOrange : _sBtn, GUILayout.Width(122)))
            _autoAnimKey = !_autoAnimKey;
        GUILayout.EndHorizontal();

        GUILayout.Space(3);
        GUILayout.BeginHorizontal();
        for (int s = 0; s < 4; s++)
            if (GUILayout.Button("C" + (s + 1), _sBtn, GUILayout.Width(34))) CopySlot(s);
        GUILayout.Space(6);
        for (int s = 0; s < 4; s++)
        {
            bool has = _copySlots[s] != null;
            if (GUILayout.Button("P" + (s + 1), has ? _sBtnBlue : _sBtn, GUILayout.Width(34))) PasteSlot(s);
        }
        GUILayout.Space(10);
        GUILayout.Label("Paste:", _sMuted, GUILayout.Width(40));
        _pasteCam = PasteToggle("Camera", _pasteCam);
        _pasteObj = PasteToggle("Objects", _pasteObj);
        _pasteAnim = PasteToggle("Anims", _pasteAnim);
        _pasteSound = PasteToggle("Sound", _pasteSound);
        _pasteText = PasteToggle("Text", _pasteText);
        GUILayout.FlexibleSpace();
        if (GUILayout.Button(_showGuides ? "Guides On" : "Guides Off", _showGuides ? _sBtnOrange : _sBtn, GUILayout.Width(84)))
            _showGuides = !_showGuides;
        if (GUILayout.Button(_muteSfx ? "SFX Off" : "SFX On", _muteSfx ? _sBtnOrange : _sBtn, GUILayout.Width(64)))
        {
            _muteSfx = !_muteSfx;
            var dmS = Dialogger_Manager.instance;
            if (dmS != null) dmS.blockSounds = _muteSfx;
        }
        GUILayout.EndHorizontal();

        GUILayout.Space(3);
        GUILayout.BeginHorizontal();
        GUILayout.Label("Engine SFX", _sMuted, GUILayout.Width(64));
        _engineSfxName = GUILayout.TextField(_engineSfxName ?? "", _sField, GUILayout.Width(130));
        if (GUILayout.Button("▶", _sBtn, GUILayout.Width(28)))
        {
            string n = (_engineSfxName ?? "").Trim();
            if (n.Length > 0) AddCmd("EngineSFX{" + n + "|Play|0}"); else _status = "enter an SFX cue name first";
        }
        if (GUILayout.Button("■", _sBtn, GUILayout.Width(28)))
        {
            string n = (_engineSfxName ?? "").Trim();
            if (n.Length > 0) AddCmd("EngineSFX{" + n + "|Stop|0}");
        }
        GUILayout.FlexibleSpace();
        if (GUILayout.Button(_branchOpen ? "Branching (open)" : "Branching…", _branchOpen ? _sBtnOrange : _sBtn, GUILayout.Width(112)))
            _branchOpen = !_branchOpen;
        GUILayout.EndHorizontal();

        // AE editor aids (Button_Mute* in the decompiled Dialogger_EditorManager) — real AE controls,
        // wired to their actual effect rather than left cosmetic.
        GUILayout.Space(3);
        GUILayout.BeginHorizontal();
        if (GUILayout.Button(_invisoboxOn ? "Invisibox On" : "Invisibox Off", _invisoboxOn ? _sBtnOrange : _sBtn, GUILayout.Width(92)))
            SetInvisibox(!_invisoboxOn);
        if (GUILayout.Button(_clickBarOn ? "Clickbar On" : "Clickbar Off", _clickBarOn ? _sBtnOrange : _sBtn, GUILayout.Width(90)))
            _clickBarOn = !_clickBarOn;
        if (GUILayout.Button(_respectTimerOn ? "Timer On" : "Timer Off", _respectTimerOn ? _sBtnOrange : _sBtn, GUILayout.Width(72)))
        {
            _respectTimerOn = !_respectTimerOn;
            var dmT = Dialogger_Manager.instance; if (dmT != null) dmT.respectTimer = _respectTimerOn;
        }
        if (GUILayout.Button("Hide Fader", _sBtn, GUILayout.Width(78)))
        {
            var dmF = Dialogger_Manager.instance;
            if (dmF != null) { try { dmF.FadeFromBlack(); _status = "fader cleared"; } catch { } }
        }
        GUILayout.FlexibleSpace();
        GUILayout.EndHorizontal();

        GUILayout.EndArea();
    }

    private bool PasteToggle(string label, bool value)
    {
        if (GUILayout.Button(label, value ? _sBtnYellow : _sBtn, GUILayout.Width(58))) return !value;
        return value;
    }

    private static int FrameCount()
    {
        var dm = Dialogger_Manager.instance;
        if (dm != null && dm.dData != null && dm.dData.frames != null) return dm.dData.frames.Count;
        return 0;
    }

    // ---- full-sequence playback --------------------------------------------
    // Dialogger_Manager's own timer auto-advance path (Timer -> interrupt -> NextPage) NREs in
    // editor mode because NextPage dereferences dem, which the playtest build stripped. So the
    // editor drives playback itself: LoadPage each page (tweens/fades/sounds run exactly like
    // real playback since RunCommands executes them), dwell on the page's Timer, and hold pages
    // without a Timer until a click/space — the same contract as live cutscene playback.
    private void DrawPlaybackBar()
    {
        float w = 360f;
        GUILayout.BeginArea(new Rect((Screen.width - w) * 0.5f, Screen.height - 46f, w, 40f), _sPanel);
        GUILayout.BeginHorizontal();
        GUILayout.Label("▶ " + _page + " / " + (FrameCount() - 1), _sHeader, GUILayout.Width(76));
        GUILayout.Label("click / space = next · Esc stops", _sMuted);
        if (GUILayout.Button("■ Stop", _sBtnOrange, GUILayout.Width(64))) _playing = false;
        GUILayout.EndHorizontal();
        GUILayout.EndArea();
    }

    private static bool AdvanceClicked()
    {
        return Input.GetMouseButtonDown(0) || Input.GetKeyDown(KeyCode.Space) || Input.GetKeyDown(KeyCode.RightArrow);
    }

    private float PageTimerSeconds(int p)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || p < 0 || p >= dm.dData.frames.Count) return -1f;
        foreach (string cmd in dm.dData.frames[p])
        {
            if (string.IsNullOrEmpty(cmd) || !cmd.StartsWith("Timer{")) continue;
            var f = Body(cmd); float t;
            if (f != null && f.Length > 0 && float.TryParse(f[0], System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out t)) return t;
        }
        return -1f;
    }

    private string RedirectTarget(int page)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || page < 0 || page >= dm.dData.frames.Count) return null;
        foreach (string cmd in dm.dData.frames[page])
        {
            if (string.IsNullOrEmpty(cmd) || !cmd.StartsWith("Redirect{")) continue;
            var f = Body(cmd);
            if (f != null && f.Length > 0) return f[0];
        }
        return null;
    }

    private IEnumerator PlaySequence(bool fromStart)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || !_loaded || FrameCount() < 2 || _playing) yield break;
        _playing = true;
        dm.blockSounds = _muteSfx;
        int p = fromStart ? 1 : Mathf.Max(1, _page);
        InfinityLoaderMod.SafeLog("[cutedit/play] start page=" + p + " end=" + (FrameCount() - 1) + " mute=" + _muteSfx);
        yield return null;   // swallow the click that pressed Play
        bool completed = false;
        bool needsLoad = true;
        while (_playing)
        {
            _page = p;
            if (needsLoad)
            {
                EnsureAllActorAnimationMetadata(dm);
                try { dm.LoadPage(p); }
                catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit/play] page " + p + " err " + ex.Message); }
            }
            HoldPageAnimations(dm, p);
            needsLoad = true;

            if (FindIdx(p, "Buttons{") >= 0)
            {
                // ShowOptions() already ran inside dm.LoadPage(p) and made the real, shipped option
                // buttons interactive — clicking one calls Dialogger_Manager.PressedOption, which
                // calls LoadPage directly and changes dm.pageNumber out from under us. Wait for that
                // instead of a generic click, then resync to wherever it actually jumped.
                while (_playing && dm.pageNumber == p) yield return null;
                if (!_playing) break;
                p = dm.pageNumber;
                needsLoad = false;   // the click handler already ran LoadPage(p) for us — don't double-fire it
                InfinityLoaderMod.SafeLog("[cutedit/play] branch choice -> page " + p);
                continue;
            }

            string redirectTo = RedirectTarget(p);
            if (!string.IsNullOrEmpty(redirectTo))
            {
                // Dialogger_Manager.Awake sets respectRedirects=false whenever editor==true (which we
                // always are), so the shipped RunCommands never follows Redirect{} itself while we're
                // driving playback — resolve the Label{} and jump there ourselves.
                int target = FindLabelPage(redirectTo);
                if (target >= 0) { p = target; InfinityLoaderMod.SafeLog("[cutedit/play] redirect '" + redirectTo + "' -> page " + target); continue; }
                InfinityLoaderMod.SafeLog("[cutedit/play] WARNING redirect '" + redirectTo + "' has no matching Label{}");
            }

            float dwell = PageTimerSeconds(p);
            if (dwell > 0f)
            {
                float t = 0f;
                while (_playing && t < dwell)
                {
                    t += Time.deltaTime;
                    if (AdvanceClicked()) break;   // author can skip a timed page
                    yield return null;
                }
            }
            else
            {
                // No Timer: hold like real playback until the author advances.
                while (_playing && !AdvanceClicked()) yield return null;
            }
            if (!_playing) break;
            if (p + 1 >= FrameCount()) { completed = true; break; }
            p++;
            yield return null;   // one frame so a skip-click can't double-advance
        }
        _playing = false;
        dm.blockSounds = _muteSfx;   // the SFX toggle owns this outside playback
        try { dm.KillSounds(); } catch { }
        _page = Mathf.Clamp(_page, 1, FrameCount() - 1);
        Goto(_page);
        _bufSig = "";
        _status = completed ? ("playback finished on page " + _page) : ("playback stopped on page " + _page);
        InfinityLoaderMod.SafeLog("[cutedit/play] " + _status);
    }

    // ---- AE C1-C4 / P1-P4 page clipboard ------------------------------------
    private void CopySlot(int slot)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || _page < 1 || _page >= FrameCount()) { _status = "go to a page >= 1 first"; return; }
        _copySlots[slot] = new List<string>(dm.dData.frames[_page]);
        _status = "copied page " + _page + " → C" + (slot + 1) + " (" + _copySlots[slot].Count + " command(s))";
    }

    // Paste semantics: with EVERY category on this is AE's PasteFromSlot (replace the whole page
    // with the slot, which also carries timers/fades/labels). With any category off we merge
    // instead: only the enabled categories are replaced on this page — safer than AE, which
    // silently drops the disabled categories from the page entirely.
    private void PasteSlot(int slot)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || _page < 1 || _page >= FrameCount()) { _status = "go to a page >= 1 first"; return; }
        if (_copySlots[slot] == null) { _status = "C" + (slot + 1) + " is empty — copy a page into it first"; return; }
        bool full = _pasteCam && _pasteObj && _pasteAnim && _pasteSound && _pasteText;
        if (full)
        {
            dm.dData.frames[_page] = new List<string>(_copySlots[slot]);
        }
        else
        {
            var page = dm.dData.frames[_page];
            for (int i = page.Count - 1; i >= 0; i--)
                if (PasteCatEnabled(page[i])) page.RemoveAt(i);
            foreach (string cmd in _copySlots[slot])
                if (PasteCatEnabled(cmd)) page.Add(cmd);
        }
        try { dm.LoadPage(_page); HoldPageAnimations(dm, _page); } catch { }
        _bufSig = "";
        _status = (full ? "pasted page " : "pasted selected categories of ") + "C" + (slot + 1) + " onto page " + _page;
        InfinityLoaderMod.SafeLog("[cutedit/paste] slot=" + (slot + 1) + " page=" + _page + " full=" + full);
    }

    private bool PasteCatEnabled(string cmd)
    {
        if (string.IsNullOrEmpty(cmd)) return false;
        if (cmd.StartsWith("Camera{")) return _pasteCam;
        if (cmd.StartsWith("Object{")) return _pasteObj;
        if (cmd.StartsWith("Actor{")) return _pasteAnim;
        if (cmd.StartsWith("Sound{") || cmd.StartsWith("Music{") || cmd.StartsWith("EngineSFX{")) return _pasteSound;
        if (cmd.StartsWith("Box{")) return _pasteText;
        return false;   // timers/fades/labels only move on a full paste
    }

    // ---- complete object lifecycle: delete an actor/box everywhere ----------
    private void DeleteSelected()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null) return;
        if (_selKind != "obj" && _selKind != "box" && _selKind != "audio") return;
        int id; if (!int.TryParse(_selId, out id)) return;
        int removed = 0;
        if (_selKind == "obj")
        {
            removed += RemoveCmdEverywhere("Load{" + id + "|", 0, 0);
            removed += RemoveCmdEverywhere("Object{" + id + "|", 1, int.MaxValue);
            removed += RemoveCmdEverywhere("Actor{" + id + "|", 1, int.MaxValue);
            var mt = dm.GetActorFromID(id);
            if (mt != null)
            {
                dm.actorList.Remove(mt);
                try { Destroy(mt.gameObject); } catch { }
            }
        }
        else if (_selKind == "box")
        {
            removed += RemoveCmdEverywhere("SpawnBox{" + id + "}", 0, 0);
            removed += RemoveCmdEverywhere("Box{" + id + "|", 1, int.MaxValue);
            var bt = dm.GetBoxFromID(id);
            if (bt != null)
            {
                dm.boxList.Remove(bt);
                try { Destroy(bt.gameObject); } catch { }
            }
        }
        else // audio
        {
            string loadType = LoadTypeOf(_selId);
            if (loadType == "sfx")
            {
                // Sound{}'s ID is an ORDER-based rank, not this instanceID — capture it before the
                // Load{} disappears, then shift every later sfx's Sound{} references down by one.
                int sfxIdx = SfxListIndex(_selId);
                removed += RemoveCmdEverywhere("Load{" + id + "|", 0, 0);
                removed += RenumberSoundRefsAfterDelete(sfxIdx);
            }
            else
            {
                removed += RemoveCmdEverywhere("Load{" + id + "|", 0, 0);
                removed += RemoveMusicCmdsFor(id);
            }
        }
        string what = (_selKind == "obj" ? "object #" : _selKind == "box" ? "box #" : "audio #") + id;
        _selKind = null; _selId = null; _bufSig = ""; _animClipSig = "";
        try { dm.LoadPage(_page); HoldPageAnimations(dm, _page); } catch { }
        _status = "deleted " + what + " (" + removed + " command(s) across all pages)";
        InfinityLoaderMod.SafeLog("[cutedit/delete] " + _status);
    }

    private int RemoveMusicCmdsFor(int id)
    {
        var dm = Dialogger_Manager.instance;
        int removed = 0;
        for (int p = 1; p < dm.dData.frames.Count; p++)
        {
            var frame = dm.dData.frames[p];
            for (int i = frame.Count - 1; i >= 0; i--)
            {
                // Music{action|ID|data} — the id is the SECOND field, so a plain StartsWith can't
                // isolate it; parse the body and compare the actual field.
                if (frame[i] == null || !frame[i].StartsWith("Music{")) continue;
                var f = Body(frame[i]);
                if (f != null && f.Length > 1 && f[1] == id.ToString()) { frame.RemoveAt(i); removed++; }
            }
        }
        return removed;
    }

    private int RenumberSoundRefsAfterDelete(int deletedIndex)
    {
        var dm = Dialogger_Manager.instance;
        int touched = 0;
        if (deletedIndex < 0) return touched;
        for (int p = 1; p < dm.dData.frames.Count; p++)
        {
            var frame = dm.dData.frames[p];
            for (int i = frame.Count - 1; i >= 0; i--)
            {
                if (frame[i] == null || !frame[i].StartsWith("Sound{")) continue;
                var f = Body(frame[i]); int idx;
                if (f == null || f.Length < 1 || !int.TryParse(f[0], out idx)) continue;
                if (idx == deletedIndex) { frame.RemoveAt(i); touched++; }
                else if (idx > deletedIndex) { f[0] = (idx - 1).ToString(); frame[i] = Rebuild("Sound", f); touched++; }
            }
        }
        return touched;
    }

    private int RemoveCmdEverywhere(string prefix, int firstFrame, int lastFrame)
    {
        var dm = Dialogger_Manager.instance;
        int removed = 0, end = Mathf.Min(lastFrame, dm.dData.frames.Count - 1);
        for (int p = firstFrame; p <= end; p++)
        {
            var frame = dm.dData.frames[p];
            for (int i = frame.Count - 1; i >= 0; i--)
                if (frame[i] != null && frame[i].StartsWith(prefix)) { frame.RemoveAt(i); removed++; }
        }
        return removed;
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
        _invisoboxOn = FindIdx(0, "Invisibox") >= 0;   // reflect this cutscene's actual saved state, not the last one's
        if (dm != null) dm.invisoboxGlobal = _invisoboxOn;
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
            g.HideEntitiesLayer(on);             // hide live map NPCs, pets and other entity render layers
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
        EnsureAllActorAnimationMetadata(dm);
        try { dm.LoadPage(_page); HoldPageAnimations(dm, _page); }
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
        _driving = false; _loaded = false; _open = false; _playing = false;
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
                // Music/sfx have no visual Object{} state — they're page EVENTS (like Timer/Fade),
                // not persistent per-page keys — so they get their own tree kind and inspector.
                string kind = (f[2] == "music" || f[2] == "sfx") ? "audio" : "obj";
                list.Add(new RObj(kind, f[0], f[2], LoadName(f[0], f[1], f[2])));
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
        string cmd = i >= 0 ? dm.dData.frames[_page][i] : null;
        bool ghost = false;
        if (cmd == null && _autoKey && _page >= 1)
        {
            // Auto-key: no key on this page — edit against the effective previous state (a "ghost").
            // The first Apply materialises it as a real key (see ApplyBuf).
            cmd = PrevStateCmd(SelPrefix());
            if (cmd == null) cmd = DefaultCmd(_selKind, _selId);
            ghost = cmd != null;
        }
        if (cmd == null) { _buf["__missing"] = "1"; return; }
        var f = Body(cmd);
        if (f == null) { _buf["__missing"] = "1"; return; }
        if (ghost) _buf["__ghost"] = "1";
        for (int k = 0; k < f.Length; k++) _buf["f" + k] = f[k];
        _buf["__len"] = f.Length.ToString();
        if (_selKind == "box" && f.Length > 7) { _buf["speaker"] = ExtractSpeaker(f[7]); _buf["subtitle"] = ExtractSubtitle(f[7]); }
        if (_selKind == "obj")
        {
            int ai = FindIdx(_page, "Actor{" + _selId + "|"); var af = ai >= 0 ? Body(dm.dData.frames[_page][ai]) : null;
            _buf["__anim"] = af != null && af.Length > 1 ? af[1] : "";
            _buf["__animSpeed"] = af != null && af.Length > 2 ? af[2] : "1";
            _buf["__animStart"] = af != null && af.Length > 3 ? af[3] : "0";
        }
    }

    private void ApplyBuf()
    {
        var dm = Dialogger_Manager.instance;
        int i = FindIdx(_page, SelPrefix());
        if (i < 0)
        {
            if (!_buf.ContainsKey("__ghost")) return;
            // Auto-key: materialise the ghost as a real key on this page, then apply the edit to it.
            AddKeyToPage(_selKind, _selId);
            AutoAnimKeyForSelected();
            i = FindIdx(_page, SelPrefix());
            if (i < 0) return;
            InfinityLoaderMod.SafeLog("[cutedit/autokey] keyed " + _selKind + " #" + _selId + " on page " + _page + " (inspector)");
        }
        var f = Body(dm.dData.frames[_page][i]);
        if (f == null) return;
        if (_selKind == "box" && _buf.ContainsKey("speaker") && f.Length > 7)
        {
            // Previously this hardcoded an empty subtitle line, silently erasing it on every speaker
            // edit — the nameplate format is genuinely "<size=42>NAME</size>\n<size=24>SUBTITLE</size>".
            string sp = _buf["speaker"] ?? "";
            string sub = _buf.ContainsKey("subtitle") ? (_buf["subtitle"] ?? "") : "";
            _buf["f7"] = (sp.Length == 0 && sub.Length == 0) ? "" : "<size=42>" + sp + "</size>\n<size=24>" + sub + "</size>";
        }
        for (int k = 0; k < f.Length; k++) if (_buf.ContainsKey("f" + k)) f[k] = _buf["f" + k];
        string name = _selKind == "obj" ? "Object" : (_selKind == "box" ? "Box" : "Camera");
        dm.dData.frames[_page][i] = Rebuild(name, f);
        try { dm.LoadPage(_page); } catch (Exception ex) { _status = "render err: " + ex.Message; }
        _bufSig = "";                                // reload buffers from the now-canonical command
    }

    private void AddToPage() { AddKeyToPage(_selKind, _selId); }

    // Insert a key for (kind,id) on the current page, copying the effective previous-page state
    // (falls back to a sane default). Used by the Add button AND every auto-key path.
    private void AddKeyToPage(string kind, string id)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || _page < 1) return;
        string prefix = KeyPrefix(kind, id);
        if (prefix == null || FindIdx(_page, prefix) >= 0) return;
        string cmd = PrevStateCmd(prefix);
        if (cmd == null) cmd = DefaultCmd(kind, id);
        if (cmd != null) { dm.dData.frames[_page].Add(cmd); try { dm.LoadPage(_page); } catch { } }
    }

    private static string KeyPrefix(string kind, string id)
    {
        if (kind == "obj") return "Object{" + id + "|";
        if (kind == "box") return "Box{" + id + "|";
        if (kind == "cam") return "Camera{";
        return null;
    }

    // The effective state a key would inherit: the nearest earlier page's command for this prefix.
    private string PrevStateCmd(string prefix)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || prefix == null) return null;
        for (int k = _page - 1; k >= 1; k--)
        {
            int i = FindIdx(k, prefix);
            if (i >= 0) return dm.dData.frames[k][i];
        }
        return null;
    }

    private static string DefaultCmd(string kind, string id)
    {
        if (kind == "obj") return "Object{" + id + "|1|1|0|1|0|0|0|-1 0|FFFFFFFF|0|0|1}";
        if (kind == "box") return "Box{" + id + "|0|0|1|1|0.5|0.862069|<size=42></size>\n<size=24></size>||1|0|38|000000|FFFFFF|FFFFFF|000000|-1|0|0}";
        if (kind == "cam") return "Camera{1|0|0|1|0|-1 0|0}";
        return null;
    }

    // Auto-anim-key: when an auto key is created for the selected actor, also key the animation
    // it is effectively playing so the page is self-contained (AE's separate auto-anim toggle).
    private void AutoAnimKeyForSelected()
    {
        if (!_autoAnimKey || _selKind != "obj") return;
        var dm = Dialogger_Manager.instance;
        int id;
        if (dm == null || _page < 1 || !int.TryParse(_selId, out id)) return;
        if (FindIdx(_page, "Actor{" + id + "|") >= 0) return;
        string anim = EffectiveAnimationBefore(id, _page);
        if (string.IsNullOrEmpty(anim)) return;
        dm.dData.frames[_page].Add("Actor{" + id + "|" + anim + "|1|0}");
        InfinityLoaderMod.SafeLog("[cutedit/autokey] anim key " + anim + " for #" + id + " on page " + _page);
    }

    private static string ExtractSpeaker(string nameplate)
    {
        if (string.IsNullOrEmpty(nameplate)) return "";
        var m = System.Text.RegularExpressions.Regex.Match(nameplate, "<size=42>(.*?)</size>",
            System.Text.RegularExpressions.RegexOptions.Singleline);
        return m.Success ? m.Groups[1].Value : nameplate;
    }

    private static string ExtractSubtitle(string nameplate)
    {
        if (string.IsNullOrEmpty(nameplate)) return "";
        var m = System.Text.RegularExpressions.Regex.Match(nameplate, "<size=24>(.*?)</size>",
            System.Text.RegularExpressions.RegexOptions.Singleline);
        return m.Success ? m.Groups[1].Value : "";
    }

    // ---- Phase 3: page management + authoring + save ------------------------
    private IEnumerator RefreshPageFromPrevious()
    {
        var dm = Dialogger_Manager.instance; if (dm == null || _page < 1) yield break;
        int target = _page;
        if (target > 1) { dm.LoadPage(target - 1); HoldPageAnimations(dm, target - 1); yield return null; }
        dm.LoadPage(target); HoldPageAnimations(dm, target); _bufSig = "";
        _status = "page " + target + " refreshed from its previous state";
    }

    private void KeyframeAll()
    {
        var dm = Dialogger_Manager.instance; if (dm == null || _page <= 1) return;
        var frame = dm.dData.frames[_page]; var previous = dm.dData.frames[_page - 1]; int added = 0;
        foreach (string command in previous)
        {
            if (string.IsNullOrEmpty(command)) continue;
            string prefix = null; var f = Body(command);
            if (command.StartsWith("Object{") && f != null) prefix = "Object{" + f[0] + "|";
            else if (command.StartsWith("Actor{") && f != null) prefix = "Actor{" + f[0] + "|";
            else if (command.StartsWith("Box{") && f != null) prefix = "Box{" + f[0] + "|";
            else if (command.StartsWith("Camera{")) prefix = "Camera{";
            if (prefix != null && FindIdx(_page, prefix) < 0) { frame.Add(command); added++; }
        }
        StartCoroutine(RefreshPageFromPrevious()); _status = "keyframed " + added + " missing state(s) on page " + _page;
    }

    private void RemoveSelectedKey()
    {
        var dm = Dialogger_Manager.instance; if (dm == null || _page <= 1 || _selKind == null) return;
        int removed = 0; string prefix = SelPrefix(); int i = FindIdx(_page, prefix);
        if (i >= 0) { dm.dData.frames[_page].RemoveAt(i); removed++; }
        if (_selKind == "obj") { i = FindIdx(_page, "Actor{" + _selId + "|"); if (i >= 0) { dm.dData.frames[_page].RemoveAt(i); removed++; } }
        _bufSig = ""; StartCoroutine(RefreshPageFromPrevious()); _status = "removed " + removed + " selected key(s)";
    }
    private void ClonePage()
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || _page < 0 || _page >= dm.dData.frames.Count) return;
var next = new System.Collections.Generic.List<string>();
        foreach (string command in dm.dData.frames[_page])
        {
            if (string.IsNullOrEmpty(command)) continue;
            if (command.StartsWith("Object{"))
            {
                var f = Body(command); if (f == null) continue;
                if (f.Length > 8) f[8] = "-1 0"; // editable snapshot; Move here adds the new tween
                next.Add(Rebuild("Object", f));
            }
            else if (command.StartsWith("Box{")) next.Add(command);
            else if (command.StartsWith("Camera{"))
            {
                var f = Body(command); if (f == null) continue;
                if (f.Length > 5) f[5] = "-1 0";
                next.Add(Rebuild("Camera", f));
            }
        }
        // Timers, fades, sounds and Actor animation triggers are events, not persistent state.
        dm.dData.frames.Insert(_page + 1, next);
        Goto(_page + 1); _bufSig = ""; _status = _selKind == "obj"
            ? "cloned page - drag the selected actor to its target, then click Move here" : "cloned page";
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

    // Invisibox is a bare scene command (no braces, like FadeToBlack) that — per the shipped
    // RunCommands — once set on any page stays in effect for the rest of that playthrough (only
    // StartCutscene resets dm.invisoboxGlobal to false). AE's own editor writes it to page 0
    // specifically (Button_MuteInvisibox/WriteWordOnPage) rather than the current page — mirror that.
    private void SetInvisibox(bool on)
    {
        var dm = Dialogger_Manager.instance;
        if (dm == null || dm.dData == null || dm.dData.frames == null || dm.dData.frames.Count == 0) return;
        _invisoboxOn = on;
        dm.invisoboxGlobal = on;
        if (on) { if (FindIdx(0, "Invisibox") < 0) dm.dData.frames[0].Add("Invisibox"); }
        else RemoveCmdEverywhere("Invisibox", 0, int.MaxValue);
        _status = on ? "invisibox enabled (added to page 0)" : "invisibox disabled (removed from all pages)";
    }

    // Mirrors AE's manual-Next pacing: with the Timer toggle on, Next waits out the CURRENT page's
    // Timer before advancing (like real playback would), instead of jumping instantly.
    private void AdvanceNext()
    {
        float dwell = _respectTimerOn ? PageTimerSeconds(_page) : -1f;
        if (dwell > 0f) StartCoroutine(AdvanceNextAfterDelay(dwell));
        else Goto(_page + 1);
    }

    private IEnumerator AdvanceNextAfterDelay(float seconds)
    {
        _status = "respecting this page's timer (" + seconds.ToString("0.#", System.Globalization.CultureInfo.InvariantCulture) + "s)…";
        float t = 0f;
        while (t < seconds) { t += Time.deltaTime; yield return null; }
        Goto(_page + 1);
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
