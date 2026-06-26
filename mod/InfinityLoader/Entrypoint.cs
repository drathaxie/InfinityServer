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
