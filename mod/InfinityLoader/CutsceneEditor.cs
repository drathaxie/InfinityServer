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
        GUILayout.BeginArea(new Rect(12, 12, 340, 150), GUI.skin.box);
        GUILayout.Label("Cutscene Editor — Phase 1 (render preview)");

        GUILayout.BeginHorizontal();
        GUILayout.Label("Cutscene id", GUILayout.Width(74));
        _idText = GUILayout.TextField(_idText ?? "", GUILayout.Width(90));
        if (GUILayout.Button("Load")) StartCoroutine(LoadAndRender());
        if (GUILayout.Button("Close")) CloseEditor();
        GUILayout.EndHorizontal();

        if (_loaded)
        {
            int total = FrameCount();
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("|< First")) Goto(1);
            if (GUILayout.Button("< Back")) Goto(_page - 1);
            GUILayout.Label(" page " + _page + " / " + (total - 1) + " ", GUILayout.Width(96));
            if (GUILayout.Button("Next >")) Goto(_page + 1);
            if (GUILayout.Button("End >|")) Goto(total - 1);
            GUILayout.EndHorizontal();
        }

        GUILayout.Label(_status);
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
}
