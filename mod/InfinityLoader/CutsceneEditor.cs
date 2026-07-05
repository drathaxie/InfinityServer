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

    private void OnGUI()
    {
        if (!_open) return;
        try
        {
            DrawMainPanel();
            if (_loaded) { DrawTree(); DrawInspector(); }
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[cutedit] OnGUI " + ex.Message); }
    }

    private void DrawMainPanel()
    {
        GUILayout.BeginArea(new Rect(12, 12, 300, _loaded ? 232 : 92), GUI.skin.box);
        GUILayout.Label("Cutscene Editor — Phase 3");
        GUILayout.BeginHorizontal();
        GUILayout.Label("id", GUILayout.Width(16));
        _idText = GUILayout.TextField(_idText ?? "", GUILayout.Width(56));
        if (GUILayout.Button("Load")) StartCoroutine(LoadAndRender());
        if (GUILayout.Button("Close")) CloseEditor();
        GUILayout.EndHorizontal();
        if (_loaded)
        {
            int total = FrameCount();
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("|<")) Goto(1);
            if (GUILayout.Button("<")) Goto(_page - 1);
            GUILayout.Label(" pg " + _page + "/" + (total - 1) + " ", GUILayout.Width(60));
            if (GUILayout.Button(">")) Goto(_page + 1);
            if (GUILayout.Button(">|")) Goto(total - 1);
            GUILayout.EndHorizontal();

            GUILayout.Space(2); GUILayout.Label("Pages");
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Clone")) ClonePage();
            if (GUILayout.Button("Blank")) BlankPage();
            if (GUILayout.Button("Delete")) DeletePage();
            GUILayout.EndHorizontal();

            GUILayout.Label("Add to this page");
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Bubble")) AddBubble();
            if (GUILayout.Button("Timer")) AddCmd("Timer{" + (_timerSec ?? "2") + "}");
            _timerSec = GUILayout.TextField(_timerSec ?? "2", GUILayout.Width(30));
            GUILayout.Label("s", GUILayout.Width(8));
            GUILayout.EndHorizontal();
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Fade To")) AddCmd("FadeToBlack");
            if (GUILayout.Button("Fade From")) AddCmd("FadeFromBlack");
            GUILayout.EndHorizontal();

            GUILayout.Space(2);
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Save")) SaveScene(false);
            if (GUILayout.Button("Save as NEW")) SaveScene(true);
            GUILayout.EndHorizontal();
        }
        GUILayout.Label(_status);
        GUILayout.EndArea();
    }

    // ---- object tree ---------------------------------------------------------
    private void DrawTree()
    {
        GUILayout.BeginArea(new Rect(12, 250, 226, Mathf.Max(150, Screen.height - 284)), GUI.skin.box);
        GUILayout.Label("OBJECTS");
        _treeScroll = GUILayout.BeginScrollView(_treeScroll);
        GUILayout.Label("Camera");
        TreeRow("cam", "", "Camera");
        var roster = Roster();
        foreach (var cat in _cats)
        {
            bool header = false;
            foreach (var o in roster)
            {
                if (Category(o) != cat) continue;
                if (!header) { GUILayout.Space(4); GUILayout.Label(cat); header = true; }
                TreeRow(o.kind, o.id, o.name);
            }
        }
        GUILayout.EndScrollView();
        GUILayout.EndArea();
    }
    private static readonly string[] _cats = { "Actors", "BGs", "Boxes" };

    private void TreeRow(string kind, string id, string name)
    {
        bool sel = _selKind == kind && _selId == id;
        var prev = GUI.color;
        if (sel) GUI.color = Color.cyan;
        string label = kind == "cam" ? "Camera" : ("#" + id + " " + name);
        if (GUILayout.Button(label)) { _selKind = kind; _selId = id; _bufSig = ""; }
        GUI.color = prev;
    }

    // ---- inspector -----------------------------------------------------------
    private void DrawInspector()
    {
        if (_selKind == null) return;
        float w = 320f;
        GUILayout.BeginArea(new Rect(Screen.width - w - 10, 12, w, Mathf.Max(200, Screen.height - 40)), GUI.skin.box);

        string sig = _selKind + ":" + _selId + ":" + _page;
        if (sig != _bufSig) { LoadBuf(); _bufSig = sig; }

        string title = _selKind == "cam" ? "Camera" : (_selKind == "box" ? "Box " + _selId : "Object #" + _selId);
        GUILayout.Label(title + "   ·   page " + _page);

        if (_buf.ContainsKey("__missing"))
        {
            GUILayout.Label("Not present on this page.");
            if (GUILayout.Button("Add to this page")) { AddToPage(); _bufSig = ""; }
            GUILayout.EndArea();
            return;
        }

        _inspScroll = GUILayout.BeginScrollView(_inspScroll);
        if (_selKind == "obj") { BufToggle("Visible", 1, "1", "0"); BufField("X", 5); BufField("Y", 6);
            BufField("Scale", 4); BufField("Rotation", 10); BufField("Z-order", 3);
            BufToggle("Face left", 2, "-1", "1"); BufField("Tint hex", 9); BufField("Tween", 8); }
        else if (_selKind == "box") { BufToggle("Visible", 4, "1", "0");
            if (_buf.ContainsKey("speaker")) { GUILayout.Label("Speaker"); _buf["speaker"] = GUILayout.TextField(_buf["speaker"] ?? ""); }
            if (_buf.ContainsKey("f8")) { GUILayout.Label("Dialog text"); _buf["f8"] = GUILayout.TextArea(_buf["f8"] ?? "", GUILayout.Height(56)); }
            BufField("X", 1); BufField("Y", 2); BufField("Scale", 3); BufField("Font size", 11); }
        else if (_selKind == "cam") { int len = _buf.ContainsKey("__len") ? int.Parse(_buf["__len"]) : 0;
            if (len >= 7) { BufField("Zoom", 0); BufField("X", 1); BufField("Y", 2); BufField("Rotation", 4); BufField("Tween", 5); }
            else { BufField("X", 0); BufField("Y", 1); BufField("Scale", 3); BufField("Speed", 4); } }
        GUILayout.EndScrollView();

        GUILayout.Space(6);
        if (GUILayout.Button("Apply + render")) ApplyBuf();
        GUILayout.EndArea();
    }

    private void BufField(string label, int idx)
    {
        string key = "f" + idx;
        if (!_buf.ContainsKey(key)) return;
        GUILayout.BeginHorizontal();
        GUILayout.Label(label, GUILayout.Width(84));
        _buf[key] = GUILayout.TextField(_buf[key] ?? "", GUILayout.Width(150));
        GUILayout.EndHorizontal();
    }

    private void BufToggle(string label, int idx, string onVal, string offVal)
    {
        string key = "f" + idx;
        if (!_buf.ContainsKey(key)) return;
        bool cur = _buf[key] == onVal;
        bool nv = GUILayout.Toggle(cur, " " + label);
        if (nv != cur) _buf[key] = nv ? onVal : offVal;
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
