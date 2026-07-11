using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using HarmonyLib;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

// Doorstop invokes Doorstop.Entrypoint.Start() at process startup (before the game's first
// scene). We apply our Harmony patches there using AE's OWN HarmonyLib.
namespace Doorstop
{
    public static class Entrypoint
    {
        public static void Start()
        {
            try { InfinityLoaderMod.Boot(); }
            catch (Exception ex) { InfinityLoaderMod.SafeLog("[InfinityLoader] FATAL " + ex); }
        }
    }
}

/// <summary>
/// The whole mod: redirect the client's web API to our local server, plus the always-on
/// c2s/s2c packet logger. Each patch is applied independently in a try/catch so a single
/// failed patch (e.g. a renamed method in a future game build) never takes the others down —
/// the WebApi redirect, the one essential piece, stays up even if the logger can't bind.
/// </summary>
public static class InfinityLoaderMod
{
    private const string DEFAULT_WEBAPI = "http://127.0.0.1:8182/";
    private static readonly object _ioLock = new object();
    private static string _userDataRoot;     // ...\UserData
    private static string _beyondDir;        // ...\UserData\Beyond
    private static string _packetLog;        // ...\UserData\Beyond\packets.jsonl
    private static string _loaderLog;        // ...\UserData\Beyond\infinity_loader.log
    private static MethodInfo _serialize;    // AEC.Serialize (private) for faithful c2s logging

    public static void Boot()
    {
        string root = AppContext.BaseDirectory;
        if (string.IsNullOrEmpty(root)) root = Directory.GetCurrentDirectory();
        _userDataRoot = Path.Combine(root, "UserData");
        _beyondDir = Path.Combine(_userDataRoot, "Beyond");
        try { Directory.CreateDirectory(_beyondDir); } catch { }
        _packetLog = Path.Combine(_beyondDir, "packets.jsonl");
        _loaderLog = Path.Combine(_beyondDir, "infinity_loader.log");

        var h = new Harmony("infinity.local.loader");

        // 1) ESSENTIAL: route the entire web API (login/nowinfinity, server list, monster data,
        //    dev tools) at our local server, so the client never reaches AE's API.
        TryPatch(h, "WebApiURL redirect",
            AccessTools.PropertyGetter(typeof(Main), "WebApiURL"),
            postfix: nameof(WebApiPostfix));

        // 1b) Route asset bundles (Game.BaseURL + "assetbundles/...") at our content mirror
        //     (content.py), so map/cutscene/item art loads via us (and is cacheable). Opt-in via
        //     UserData/infinity_content.txt. WebApiURL is patched separately above, so redirecting
        //     BaseURL here only affects asset loads, not the API.
        TryPatch(h, "BaseURL (content) redirect",
            AccessTools.PropertyGetter(typeof(Main), "BaseURL"),
            postfix: nameof(BaseUrlPostfix));

        // 2) Always-on packet capture (our ground truth) — c2s requests and s2c responses.
        TryPatch(h, "c2s logger",
            AccessTools.Method(typeof(AEC), "sendRequest"),
            prefix: nameof(SendRequestPrefix));
        TryPatch(h, "s2c logger",
            AccessTools.Method(typeof(AEC), "WrapAndQueueResponse"),
            prefix: nameof(WrapResponsePrefix));

        // 3) ALLOW our plain-HTTP API on Unity 6. UnityWebRequest blocks cleartext HTTP to any
        //    non-localhost host ("Insecure connection not allowed"), which kills login against a
        //    hosted http:// API. For http:// URLs we run the request through .NET WebClient (no
        //    Unity insecure-http policy) and drive WebCom's callbacks ourselves; https:// (AE's
        //    asset CDN) is untouched. Expect-100 off so a POST doesn't stall on a simple http server.
        System.Net.ServicePointManager.Expect100Continue = false;
        TryPatch(h, "insecure-http API (POST)",
            AccessTools.Method(typeof(WebCom), "SendData"),
            prefix: nameof(WebCom_SendData_Prefix));
        TryPatch(h, "insecure-http API (GET)",
            AccessTools.Method(typeof(WebCom), "Send"),
            prefix: nameof(WebCom_Send_Prefix));

        // 4) Chat emoji: expand :shortcodes: -> <sprite name="..."> and point the chat log's TMP
        //    field at our emoji sprite-asset bundle. Cosmetic; failure never affects login/chat text.
        TryPatch(h, "chat emoji",
            AccessTools.Method(typeof(UIChat), "SetText"),
            prefix: nameof(UIChat_SetText_Prefix));

        // 5) In-game "web edit" pencil (DevMode): WebEditButton opens _baseURL + AddURL + AddParam
        //    in a browser, but _baseURL ships empty so the button does nothing. Point it at our API
        //    (WebApiURL) so apop/Edit.aspx (the structured apop editor served by webapi.py) opens,
        //    pre-loaded to the clicked apop's ?ID=. IsSafeWebUrl accepts our https sslip host.
        TryPatch(h, "web-edit button base URL",
            AccessTools.Method(typeof(WebEditButton), "OnMouseDown"),
            prefix: nameof(WebEditButton_OnMouseDown_Prefix));

        // 5b) Custom portrait/name-plate frames on TOP of the shipped 0-4. The enum PortraitFrameId
        //     is int-backed, so the server can grant ids >4 (see game.PORTRAIT_FRAME_POTATO=5); the
        //     client's NameplatePortraitFixerData has no setting for them, so FindByFrame falls back
        //     to Default. We postfix FindByFrame to synthesize a setting (sprites from PNGs in
        //     UserData/Beyond/portraits/) for our custom ids — so BOTH the picker option and the
        //     applied frame render our art, and the shipped tiers stay untouched (1:1).
        TryPatch(h, "custom portrait frames",
            AccessTools.Method(typeof(NameplatePortraitFixerData), "FindByFrame"),
            postfix: nameof(FindByFrame_Postfix));

        // 5c) Custom-frame layer fit-up: the shipped Image rects are sized for the vanilla art, so
        //     our fixed-perspective PNGs get stretched. For custom frames (id>4) we log each layer's
        //     rect/type (diag) and keep the round layers circular via preserveAspect.
        TryPatch(h, "custom portrait layer fit",
            AccessTools.Method(typeof(NameplatePortraitFixer), "ApplyPortrait"),
            postfix: nameof(ApplyPortrait_Postfix));

        // 5d) Apop portrait for avatar-assembled NPCs. Apop.OnActorSpawnready does
        //     asset.transform.Find("CameraFocus").position — but an NPC assembled from equipped
        //     items (HumanoidAvatar, e.g. custom Redux) has NO CameraFocus child (only bundle
        //     prefabs ship one), so it NREs and the portrait never appears beside the apop. Inject
        //     a CameraFocus at the avatar's origin before the original runs so positioning works.
        TryPatch(h, "apop portrait CameraFocus guard",
            AccessTools.Method(typeof(Apop), "OnActorSpawnready"),
            prefix: nameof(Apop_OnActorSpawnready_Prefix));

        // 6) In-client cutscene editor (Phase 1): drives the shipped Dialogger_Manager to render
        //    saved cutscenes under our control. IMGUI panel, F8 to toggle.
        //    IMPORTANT: do NOT spawn it here. Boot() runs at the Doorstop entrypoint, BEFORE Unity's
        //    scripting runtime is initialized — touching any UnityEngine type (e.g. `new GameObject`)
        //    forces UnityEngine.Object's static initializer to run too early, it throws
        //    (GetOffsetOfInstanceIDInCPlusPlusObject native binding not ready), and a type initializer
        //    that throws once POISONS that type for the whole process -> every Unity object is then
        //    dead -> black screen. So the editor is spawned lazily from UIChat.SetText (below), which
        //    only ever runs well after the game is up.

        SafeLog("[InfinityLoader] booted; WebApiURL -> " + (ReadWebApiUrl() ?? "(live AE, no marker)")
            + "; BaseURL -> " + (ReadContentUrl() ?? "(live AE, no marker)")
            + "; UserData=" + _beyondDir);
    }

    private static void TryPatch(Harmony h, string name, MethodBase target,
                                 string prefix = null, string postfix = null)
    {
        try
        {
            if (target == null) { SafeLog("[InfinityLoader] SKIP " + name + " (target method not found)"); return; }
            h.Patch(target,
                prefix: prefix == null ? null : new HarmonyMethod(typeof(InfinityLoaderMod), prefix),
                postfix: postfix == null ? null : new HarmonyMethod(typeof(InfinityLoaderMod), postfix));
            SafeLog("[InfinityLoader] patched: " + name);
        }
        catch (Exception ex) { SafeLog("[InfinityLoader] FAILED " + name + ": " + ex.Message); }
    }

    // ---- WebApi redirect -----------------------------------------------------
    // OPT-IN, like the old mod: only redirect when UserData/infinity_api.txt exists (empty =>
    // our default local API; or put a base URL on the first line). No marker => leave the URL
    // untouched so the client still reaches live AE. Returns null when we should NOT redirect.
    private static string ReadWebApiUrl()
    {
        try
        {
            string flag = Path.Combine(_userDataRoot, "infinity_api.txt");
            if (!File.Exists(flag)) return null;
            string s = File.ReadAllText(flag).Trim();
            if (s.Length == 0) return DEFAULT_WEBAPI;
            return s.EndsWith("/") ? s : s + "/";
        }
        catch { return null; }
    }

    // Set the WebEditButton's base URL to our API at click time, so the in-game pencil opens
    // OUR apop editor (WebApiURL + "apop/Edit.aspx?ID=n") instead of an empty/relative URL.
    public static void WebEditButton_OnMouseDown_Prefix()
    {
        try { WebEditButton._baseURL = Main.WebApiURL; } catch { }
    }

    // Ensure an avatar-assembled apop NPC has the "CameraFocus" child Apop.OnActorSpawnready derefs
    // (bundle prefabs ship one; HumanoidAvatar-assembled NPCs don't). Origin-anchored so the portrait
    // lands where the original math expects; harmless if a CameraFocus already exists.
    public static void Apop_OnActorSpawnready_Prefix(GameObject asset)
    {
        try
        {
            if (asset != null && asset.transform.Find("CameraFocus") == null)
            {
                var cf = new GameObject("CameraFocus");
                cf.transform.SetParent(asset.transform, worldPositionStays: false);
                cf.transform.localPosition = Vector3.zero;
            }
        }
        catch { }
    }

    public static void WebApiPostfix(ref string __result)
    {
        string url = ReadWebApiUrl();
        if (!string.IsNullOrEmpty(url)) __result = url;
    }

    private const string DEFAULT_CONTENT = "http://127.0.0.1:8181/game/";

    private static string ReadContentUrl()
    {
        try
        {
            string flag = Path.Combine(_userDataRoot, "infinity_content.txt");
            if (!File.Exists(flag)) return null;
            string s = File.ReadAllText(flag).Trim();
            if (s.Length == 0) return DEFAULT_CONTENT;
            return s.EndsWith("/") ? s : s + "/";
        }
        catch { return null; }
    }

    public static void BaseUrlPostfix(ref string __result)
    {
        string url = ReadContentUrl();
        if (!string.IsNullOrEmpty(url)) __result = url;
    }

    // ---- allow our plain-HTTP API (Unity 6 insecure-connection bypass) -------
    // We replace the UnityWebRequest send for http:// URLs with a synchronous .NET WebClient
    // call (no Unity insecure-http block, no SynchronizationContext deadlock — WebClient is
    // blocking, not async/await) and fire WebCom's own LoadCompleted/onError so the rest of the
    // client flow is unchanged. Returning false skips the original UnityWebRequest path.
    public static bool WebCom_SendData_Prefix(WebCom __instance, WebComData wd)
    {
        if (wd == null || string.IsNullOrEmpty(wd.URI) ||
            !wd.URI.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
            return true;                          // https / other -> let Unity handle it
        try
        {
            object form = Traverse.Create(wd).Field("Payload").GetValue();   // WWWForm (as object)
            byte[] body = form == null ? new byte[0]
                : (Traverse.Create(form).Property("data").GetValue<byte[]>() ?? new byte[0]);
            string ctype = "application/x-www-form-urlencoded";
            var headers = form == null ? null
                : Traverse.Create(form).Property("headers")
                    .GetValue<System.Collections.Generic.Dictionary<string, string>>();
            if (headers != null && headers.TryGetValue("Content-Type", out var ct) && !string.IsNullOrEmpty(ct))
                ctype = ct;
            using (var wc = new System.Net.WebClient())
            {
                wc.Headers[System.Net.HttpRequestHeader.ContentType] = ctype;
                byte[] resp = wc.UploadData(wd.URI, "POST", body);
                __instance.receivedText = Encoding.UTF8.GetString(resp);
            }
            InvokeLoaded(__instance);
        }
        catch (Exception ex) { InvokeError(__instance, ex.Message); }
        return false;
    }

    public static bool WebCom_Send_Prefix(WebCom __instance, string uri)
    {
        if (string.IsNullOrEmpty(uri) ||
            !uri.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
            return true;
        try
        {
            using (var wc = new System.Net.WebClient())
            {
                byte[] resp = wc.DownloadData(uri);
                __instance.receivedText = Encoding.UTF8.GetString(resp);
            }
            InvokeLoaded(__instance);
        }
        catch (Exception ex) { InvokeError(__instance, ex.Message); }
        return false;
    }

    private static void InvokeLoaded(WebCom inst)
    {
        var d = Traverse.Create(inst).Field("LoadCompleted").GetValue<Action<WebCom>>();
        d?.Invoke(inst);
    }

    private static void InvokeError(WebCom inst, string msg)
    {
        SafeLog("[InfinityLoader] WebCom http error: " + msg);
        var d = Traverse.Create(inst).Field("onError").GetValue<Action<string>>();
        d?.Invoke(msg);
    }

    // ---- chat emoji ----------------------------------------------------------
    // Load emoji.unity3d (shipped next to the loader, in UserData/Beyond) once, register its
    // TMP_SpriteAsset as the TMP default + on the chat log field, and expand :shortcodes: in every
    // chat line to <sprite name="..."> tags TMP renders inline. Lazy so TMP is initialized.
    private static bool _emojiTried;
    private static TMP_SpriteAsset _emojiAsset;
    private static HashSet<string> _emojiNames;
    private static readonly Regex _emojiRe = new Regex(":([A-Za-z0-9_]+):", RegexOptions.Compiled);

    private static void EnsureEmoji()
    {
        if (_emojiTried) return;
        _emojiTried = true;
        try
        {
            string path = Path.Combine(_beyondDir, "emoji.unity3d");
            if (!File.Exists(path)) { SafeLog("[emoji] bundle missing: " + path); return; }
            AssetBundle bundle = AssetBundle.LoadFromFile(path);
            if (bundle == null) { SafeLog("[emoji] LoadFromFile returned null"); return; }
            _emojiAsset = bundle.LoadAsset<TMP_SpriteAsset>("Emoji");
            if (_emojiAsset == null) { SafeLog("[emoji] 'Emoji' asset not in bundle"); return; }
            _emojiNames = new HashSet<string>();
            foreach (var c in _emojiAsset.spriteCharacterTable)
                if (!string.IsNullOrEmpty(c.name)) _emojiNames.Add(c.name);
            // Global default so any TMP field (chat log, preview) resolves <sprite name="...">.
            try
            {
                var inst = TMP_Settings.instance;
                if (inst != null)
                {
                    var f = typeof(TMP_Settings).GetField("m_defaultSpriteAsset",
                        BindingFlags.NonPublic | BindingFlags.Instance);
                    if (f != null) f.SetValue(inst, _emojiAsset);
                }
            }
            catch (Exception ex) { SafeLog("[emoji] default-set warn: " + ex.Message); }
            SafeLog("[emoji] loaded " + _emojiNames.Count + " emoji");
        }
        catch (Exception ex) { SafeLog("[emoji] load FAILED: " + ex); }
    }

    private static string ExpandEmoji(string s)
    {
        if (_emojiNames == null || _emojiNames.Count == 0 || s.IndexOf(':') < 0) return s;
        return _emojiRe.Replace(s, m =>
        {
            string n = m.Groups[1].Value.ToLowerInvariant();
            return _emojiNames.Contains(n) ? "<sprite name=\"" + n + "\">" : m.Value;
        });
    }

    public static void UIChat_SetText_Prefix(UIChat __instance, ref string s)
    {
        CutsceneEditorController.Spawn();      // lazy fallback: guaranteed in-game, Unity ready
        EnsureEmoji();
        if (_emojiAsset != null && __instance != null)
        {
            try
            {
                var tf = __instance.textField;                 // public TMP_Text on UIChat
                if (tf != null && tf.spriteAsset != _emojiAsset) tf.spriteAsset = _emojiAsset;
            }
            catch { }
        }
        if (!string.IsNullOrEmpty(s)) s = ExpandEmoji(s);
    }

    // ---- custom portrait / name-plate frames ---------------------------------
    // Frame ids > 4 are OURS (added on top of the shipped Default..Tier3). For each, we build a
    // NameplatePortraitFixerData setting from PNGs in UserData/Beyond/portraits/<key>_*.png and
    // hand it back from FindByFrame, so the stock picker + ApplyPortrait paths render our art.
    //   id 5 = "potato": potato_frame.png (ring) / potato_plate.png / potato_background.png /
    //                    potato_lvlcircle.png. Missing layers are simply left as the default.
    private static readonly Dictionary<int, NameplatePortraitFixerData.NameplatePortraitFixerSettings> _customFrames
        = new Dictionary<int, NameplatePortraitFixerData.NameplatePortraitFixerSettings>();

    private static readonly Dictionary<int, string> _customFrameKey = new Dictionary<int, string>
    {
        { 5, "potato" },
    };

    public static void FindByFrame_Postfix(PortraitFrameId frame,
        ref NameplatePortraitFixerData.NameplatePortraitFixerSettings __result)
    {
        int id = (int)frame;
        if (id <= 4) return;                       // shipped frames — never touch
        var custom = GetCustomFrame(id);
        if (custom != null) __result = custom;     // else leave the Default fallback in place
    }

    private static NameplatePortraitFixerData.NameplatePortraitFixerSettings GetCustomFrame(int id)
    {
        NameplatePortraitFixerData.NameplatePortraitFixerSettings s;
        if (_customFrames.TryGetValue(id, out s)) return s;    // cached (incl. null "tried")
        string key;
        if (!_customFrameKey.TryGetValue(id, out key)) { _customFrames[id] = null; return null; }
        try
        {
            string dir = Path.Combine(_beyondDir, "portraits");
            // The plate is 9-sliced (see ApplyPortrait_Postfix): its sprite carries a border so the
            // dirt corners stay fixed while the wide UI rect stretches the middle. border = (left,
            // bottom, right, top) in sprite px — sized to the dirt/gold frame thickness.
            s = new NameplatePortraitFixerData.NameplatePortraitFixerSettings
            {
                name = key,
                frame = (PortraitFrameId)id,
                portraitSprite    = LoadSpritePng(Path.Combine(dir, key + "_frame.png")),
                // plate is built WIDE (~583x321, the vanilla aspect) so a plain Simple stretch to the
                // 430x240 rect is ~distortion-free — no 9-slice needed.
                Nameplate_Plate       = LoadSpritePng(Path.Combine(dir, key + "_plate.png")),
                Nameplate_Background  = LoadSpritePng(Path.Combine(dir, key + "_background.png")),
                Nameplate_LvlCircle   = LoadSpritePng(Path.Combine(dir, key + "_lvlcircle.png")),
            };
            SafeLog("[portrait] built custom frame #" + id + " (" + key + ") ring="
                + (s.portraitSprite != null) + " plate=" + (s.Nameplate_Plate != null)
                + " bg=" + (s.Nameplate_Background != null) + " lvl=" + (s.Nameplate_LvlCircle != null));
        }
        catch (Exception ex) { SafeLog("[portrait] build #" + id + " FAILED: " + ex.Message); s = null; }
        _customFrames[id] = s;
        return s;
    }

    private static FieldInfo _fPortrait, _fPlate, _fBg, _fLvl;

    public static void ApplyPortrait_Postfix(NameplatePortraitFixer __instance, PortraitFrameId frame)
    {
        if (__instance == null) return;
        try
        {
            if (_fPortrait == null)
            {
                var t = typeof(NameplatePortraitFixer);
                const BindingFlags BF = BindingFlags.NonPublic | BindingFlags.Instance;
                _fPortrait = t.GetField("portraitFrame", BF);
                _fPlate = t.GetField("nameplatePlate", BF);
                _fBg = t.GetField("nameplateBackground", BF);
                _fLvl = t.GetField("nameplateLvlCircle", BF);
            }
            bool custom = (int)frame > 4;
            // Round layers (ring + level badge) are square sprites: preserveAspect keeps them
            // circular in a non-square rect. The plate is a wide frame: 9-slice (Sliced) so the
            // dirt corners stay put and only the middle stretches to the 430x240 rect. For shipped
            // frames we restore the vanilla state (Simple, no preserveAspect).
            Fit("portraitFrame", _fPortrait, __instance, custom, Image.Type.Simple);
            Fit("nameplatePlate", _fPlate, __instance, false, Image.Type.Simple);
            Fit("nameplateLvlCircle", _fLvl, __instance, custom, Image.Type.Simple);
        }
        catch (Exception ex) { SafeLog("[portrait] fit err: " + ex.Message); }
    }

    private static void Fit(string label, FieldInfo f, object inst, bool preserveAspect, Image.Type type)
    {
        if (f == null) return;
        var img = f.GetValue(inst) as Image;
        if (img == null) { SafeLog("[portrait/rect] " + label + " = null"); return; }
        var r = img.rectTransform.rect;
        string sp = img.sprite != null ? (img.sprite.name + " " + (int)img.sprite.rect.width + "x" + (int)img.sprite.rect.height + " border" + img.sprite.border) : "(none)";
        SafeLog("[portrait/rect] " + label + " rect=" + (int)r.width + "x" + (int)r.height
            + " type=" + img.type + "->" + type + " sprite=" + sp);
        LogSpriteOpaque(img.sprite);   // measure real vs custom footprint for every layer
        img.preserveAspect = preserveAspect;
        img.type = type;
    }

    // Measure a sprite's opaque bounding box (once per sprite) so I can match the real gold plate's
    // tight footprint. Uses a RenderTexture readback so it works even on non-readable bundle textures.
    private static readonly HashSet<string> _opaqueLogged = new HashSet<string>();
    private static void LogSpriteOpaque(Sprite sprite)
    {
        try
        {
            if (sprite == null || sprite.texture == null) return;
            string nm = sprite.name ?? "?";
            if (!_opaqueLogged.Add(nm)) return;
            var tex = sprite.texture;
            var tr = sprite.textureRect;                 // this sprite's region in the atlas texture
            int tw = (int)tr.width, th = (int)tr.height;
            if (tw <= 0 || th <= 0) return;
            var rt = RenderTexture.GetTemporary(tex.width, tex.height, 0, RenderTextureFormat.ARGB32);
            Graphics.Blit(tex, rt);
            var prev = RenderTexture.active; RenderTexture.active = rt;
            var rd = new Texture2D(tw, th, TextureFormat.RGBA32, false);
            rd.ReadPixels(new Rect(tr.x, tex.height - tr.y - th, tw, th), 0, 0);
            rd.Apply();
            RenderTexture.active = prev; RenderTexture.ReleaseTemporary(rt);
            var px = rd.GetPixels32();
            int minx = tw, miny = th, maxx = 0, maxy = 0;
            for (int y = 0; y < th; y++)
                for (int x = 0; x < tw; x++)
                    if (px[y * tw + x].a > 20)
                    {
                        if (x < minx) minx = x; if (x > maxx) maxx = x;
                        if (y < miny) miny = y; if (y > maxy) maxy = y;
                    }
            UnityEngine.Object.Destroy(rd);
            if (maxx < minx) { SafeLog("[portrait/opaque] " + nm + " fully transparent"); return; }
            SafeLog("[portrait/opaque] " + nm + " tex=" + tw + "x" + th
                + " opaque=" + (maxx - minx + 1) + "x" + (maxy - miny + 1)
                + " margins L" + minx + " R" + (tw - 1 - maxx) + " T" + miny + " B" + (th - 1 - maxy)
                + " => opaque " + ((maxx - minx + 1) * 100 / tw) + "% x " + ((maxy - miny + 1) * 100 / th) + "%");
        }
        catch (Exception ex) { SafeLog("[portrait/opaque] fail: " + ex.Message); }
    }

    private static Sprite LoadSpritePng(string path, Vector4 border = default(Vector4), float ppu = 100f)
    {
        try
        {
            if (!File.Exists(path)) return null;
            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
            if (!tex.LoadImage(File.ReadAllBytes(path))) return null;   // ImageConversion
            return Sprite.Create(tex, new Rect(0, 0, tex.width, tex.height),
                                 new Vector2(0.5f, 0.5f), ppu, 0, SpriteMeshType.FullRect, border);
        }
        catch (Exception ex) { SafeLog("[portrait] sprite load fail " + Path.GetFileName(path) + ": " + ex.Message); return null; }
    }

    // ---- packet logger -------------------------------------------------------
    public static void SendRequestPrefix(Request r)
    {
        if (r == null) return;
        try
        {
            string json = null;
            try
            {
                if (_serialize == null) _serialize = AccessTools.Method(typeof(AEC), "Serialize");
                if (_serialize != null && AEC.Instance != null)
                    json = _serialize.Invoke(AEC.Instance, new object[] { r }) as string;
            }
            catch { }
            if (string.IsNullOrEmpty(json)) json = Newtonsoft.Json.JsonConvert.SerializeObject(r);
            WritePacket("c2s", json);
        }
        catch { }
    }

    public static void WrapResponsePrefix(byte[] data)
    {
        if (data == null) return;
        try { WritePacket("s2c", Encoding.UTF8.GetString(data)); }
        catch { }
    }

    private static void WritePacket(string dir, string rawPkt)
    {
        if (string.IsNullOrEmpty(rawPkt)) return;
        try
        {
            double ts = (DateTime.UtcNow - new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc)).TotalSeconds;
            // Match the existing capture format so the mining tools keep working:
            //   {"ts": <epoch>, "dir": "c2s|s2c", "ok": true, "pkt": <raw packet object>}
            string line = "{\"ts\": " + ts.ToString("F3", CultureInfo.InvariantCulture)
                + ", \"dir\": \"" + dir + "\", \"ok\": true, \"pkt\": " + rawPkt + "}\n";
            lock (_ioLock) File.AppendAllText(_packetLog, line);
        }
        catch { }
    }

    public static void SafeLog(string msg)
    {
        try
        {
            lock (_ioLock)
                File.AppendAllText(_loaderLog ?? "infinity_loader.log",
                    DateTime.Now.ToString("HH:mm:ss.fff") + " " + msg + "\n");
        }
        catch { }
    }
}
