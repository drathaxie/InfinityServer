using System;
using System.Collections;
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
using UnityEngine.Networking;

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
/// failed patch (e.g. a renamed method in a future game build) never takes the others down
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
    private static bool _npcLoaderPatched;
    private const int CUSTOM_STATUE_ITEM_ID = 978659;   // AE's real Player KS Statue (bundle 78659), live 2026-07-31

    // Overhead guild tag: lowercase player name -> (guild name, colour hex). Fed from the
    // guildName/guildTagColor fields our server adds to every user object (initPlayer.user,
    // AreaJoin.uoBranch[], AreaAdd.userData). The base client has no guild-on-nameplate concept,
    // so we render a coloured "Guild" line under the name from this map.
    private static readonly Dictionary<string, KeyValuePair<string, string>> _guildByName =
        new Dictionary<string, KeyValuePair<string, string>>(StringComparer.OrdinalIgnoreCase);
    private static readonly object _guildLock = new object();

    // Guild-tag colour shop (initPlayer.tagShop / standalone "tagShop" push)  drives the picker
    // we inject into the guild panel. All access on the main thread (packet Execute / UI), so no lock.
    internal static readonly List<InfinityTagColor> TagPalette = new List<InfinityTagColor>();
    internal static readonly HashSet<string> TagOwned =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    internal static string TagSelected = "";   // player's personal override name ('' = follow guild)
    internal static string TagGuildDefault = "";
    internal static volatile bool TagShopDirty;   // set off-thread; main-thread ticker repaints panel
    private static NameplateTicker _ticker;    // per-frame rainbow driver (created lazily)

    public static void Boot()
    {
        string root = AppContext.BaseDirectory;
        if (string.IsNullOrEmpty(root)) root = Directory.GetCurrentDirectory();
        _userDataRoot = Path.Combine(root, "UserData");
        _beyondDir = Path.Combine(_userDataRoot, "Beyond");
        try { Directory.CreateDirectory(_beyondDir); } catch { }
        _packetLog = Path.Combine(_beyondDir, "packets.jsonl");
        _loaderLog = Path.Combine(_beyondDir, "infinity_loader.log");

        try { System.Net.ServicePointManager.SecurityProtocol = System.Net.SecurityProtocolType.Tls12; }
        catch (Exception ex) { SafeLog("[InfinityLoader] TLS1.2 enable failed: " + ex.Message); }

        var h = new Harmony("infinity.local.loader");

        // 1) ESSENTIAL: route the entire web API (login/nowinfinity, server list, monster data,
        //    dev tools) at our local server, so the client never reaches AE's API.
        TryPatch(h, "WebApiURL redirect",
            AccessTools.PropertyGetter(typeof(Main), "WebApiURL"),
            postfix: nameof(WebApiPostfix));

        // RedeemCodeModal bypasses Main.WebApiURL and hardcodes account.aq.com. Rewrite only
        // those two Heromart requests to our selected private-server API before Unity sends them.
        TryPatch(h, "Heromart redeem URL redirect",
            AccessTools.Method(typeof(UnityWebRequest), "SendWebRequest", Type.EmptyTypes),
            prefix: nameof(UnityWebRequest_SendWebRequest_Prefix));

        // 1b) Route asset bundles (Game.BaseURL + "assetbundles/...") at our content mirror
        //     (content.py), so map/cutscene/item art loads via us (and is cacheable). Opt-in via
        //     UserData/infinity_content.txt. WebApiURL is patched separately above, so redirecting
        //     BaseURL here only affects asset loads, not the API.
        TryPatch(h, "BaseURL (content) redirect",
            AccessTools.PropertyGetter(typeof(Main), "BaseURL"),
            postfix: nameof(BaseUrlPostfix));

        // 2) Always-on packet capture (our ground truth)  c2s requests and s2c responses.
        TryPatch(h, "c2s logger",
            AccessTools.Method(typeof(AEC), "sendRequest"),
            prefix: nameof(SendRequestPrefix));
        TryPatch(h, "s2c logger",
            AccessTools.Method(typeof(AEC), "WrapAndQueueResponse"),
            prefix: nameof(WrapResponsePrefix));

        // 2b) TEMPORARY: the sky-blade (classInfinityHero_S1_P4) spawns then vanishes within
        //     ~1 frame. The server packet is already verified correct against the live DB, so
        //     the failure is in the CLIENT's particle lifecycle -- which no server-side log can
        //     observe. These two traces record which NodeParticle branch the cue takes, and then
        //     re-read the spawned GameObject over the following frames so we can see WHICH
        //     property changes when it disappears (destroyed / deactivated / scaled to zero /
        //     moved off-camera / finished emitting). Remove once the effect renders.
        TryPatch(h, "particle diagnostics (Execute)",
            AccessTools.Method(typeof(NodeParticle), "Execute"),
            prefix: nameof(ParticleExecutePrefix));
        TryPatch(h, "particle diagnostics (SpawnParticle)",
            AccessTools.Method(typeof(NodeParticle), "SpawnParticle"),
            postfix: nameof(ParticleSpawnPostfix));

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
        //     UserData/Beyond/portraits/) for our custom ids  so BOTH the picker option and the
        //     applied frame render our art, and the shipped tiers stay untouched (1:1).
        TryPatch(h, "custom portrait frames",
            AccessTools.Method(typeof(NameplatePortraitFixerData), "FindByFrame"),
            postfix: nameof(FindByFrame_Postfix));
        TryPatch(h, "private-server dynamic statues",
            AccessTools.Method(typeof(DynamicStatue), "Start"),
            prefix: nameof(DynamicStatue_Start_Prefix));
        // ResponseStatueVersion.Execute() (real AE class, ships in the base client) already calls
        // DynamicStatue.SetVersion(cid, version) whenever the server pushes a statueVersion s2c.
        // But DynamicStatue_Start_Prefix below fully replaces Start() and returns false, so our
        // redirected instances are NEVER added to DynamicStatue's own private `live` list  AE's
        // SetVersion would find nothing to refresh. This postfix re-triggers OUR redirect load for
        // any of our own live-tracked instances instead, so the bystander live-refresh actually reaches
        // the private-server art (not just AE's always-404 CDN path).
        TryPatch(h, "private-server dynamic statue live refresh",
            AccessTools.Method(typeof(DynamicStatue), "SetVersion"),
            postfix: nameof(DynamicStatue_SetVersion_Postfix));
        TryPatch(h, "private-server statue capture",
            AccessTools.Method(typeof(ResponseGenerateStatue), "Execute"),
            postfix: nameof(ResponseGenerateStatue_Execute_Postfix));
        TryPatch(h, "private-server statue download",
            AccessTools.Method(typeof(ApopButton), "ClickAction"),
            prefix: nameof(ApopButton_ClickAction_Prefix));

        TryPatch(h, "private-server custom statue activation",
            AccessTools.Method(typeof(HouseItemManager), "SpawnItem"),
            postfix: nameof(HouseItemManager_SpawnItem_Postfix));


        // 5c) Custom-frame layer fit-up: the shipped Image rects are sized for the vanilla art, so
        //     our fixed-perspective PNGs get stretched. For custom frames (id>4) we log each layer's
        //     rect/type (diag) and keep the round layers circular via preserveAspect.
        TryPatch(h, "custom portrait layer fit",
            AccessTools.Method(typeof(NameplatePortraitFixer), "ApplyPortrait"),
            postfix: nameof(ApplyPortrait_Postfix));

        // 5d) Apop portrait for avatar-assembled NPCs. Apop.OnActorSpawnready does
        //     asset.transform.Find("CameraFocus").position  but an NPC assembled from equipped
        //     items (HumanoidAvatar, e.g. custom Redux) has NO CameraFocus child (only bundle
        //     prefabs ship one), so it NREs and the portrait never appears beside the apop. Inject
        //     a CameraFocus at the avatar's origin before the original runs so positioning works.
        TryPatch(h, "apop portrait CameraFocus guard",
            AccessTools.Method(typeof(Apop), "OnActorSpawnready"),
            prefix: nameof(Apop_OnActorSpawnready_Prefix));


        // 5e) Guild name under the overhead nameplate. Our server tags every user object with
        //     guildName/guildTagColor; we capture those in WrapResponsePrefix into _guildByName,
        //     then append a coloured "Guild" line to the plate text. The SetUserData postfix
        //     refreshes an already-spawned player's plate live when their colour/guild changes.
        TryPatch(h, "guild nameplate line",
            AccessTools.Method(typeof(Player), "ComposeNameplateText"),
            postfix: nameof(ComposeNameplateText_Postfix));
        TryPatch(h, "guild nameplate live refresh",
            AccessTools.Method(typeof(Player), "SetUserData"),
            postfix: nameof(SetUserData_Postfix));
        // 5f) Guild-panel colour picker: append palette rows to the guild list so colours are
        //     bought/worn from the actual panel, not just chat. Backed by initPlayer.tagShop.
        TryPatch(h, "guild panel colour picker",
            AccessTools.Method(typeof(FriendListUI), "Refresh"),
            postfix: nameof(FriendList_Refresh_Postfix));

        // 5g) Fill AE's dormant Character -> Achievements tab from server-owned bitfields.
        TryPatch(h, "character achievements enable button",
            AccessTools.Method(typeof(UICharacterCanvas), "Start"),
            postfix: nameof(UICharacterCanvas_Start_Postfix));
        TryPatch(h, "character achievements panel",
            AccessTools.Method(typeof(UICharacterCanvas), "Tab_ShowAchievments"),
            postfix: nameof(UICharacterCanvas_Achievements_Postfix));
        TryPatch(h, "character achievements hide on overview",
            AccessTools.Method(typeof(UICharacterCanvas), "Tab_ShowOverview"),
            prefix: nameof(UICharacterCanvas_OtherTab_Prefix));
        TryPatch(h, "character achievements hide on reputation",
            AccessTools.Method(typeof(UICharacterCanvas), "Tab_ShowReputation"),
            prefix: nameof(UICharacterCanvas_OtherTab_Prefix));

        // Login-screen art/text is server-pushed, and this late lifecycle point is also where
        // the self-updater can safely use HTTPS on the game's Mono runtime.
        TryPatch(h, "login screen + self-update",
            AccessTools.Method(typeof(UILogin), "GetLoginData"),
            postfix: nameof(UILogin_GetLoginData_Postfix));

        // 6) In-client cutscene editor (Phase 1): drives the shipped Dialogger_Manager to render
        //    saved cutscenes under our control. IMGUI panel, F8 to toggle.
        //    IMPORTANT: do NOT spawn it here. Boot() runs at the Doorstop entrypoint, BEFORE Unity's
        //    scripting runtime is initialized  touching any UnityEngine type (e.g. `new GameObject`)
        //    forces UnityEngine.Object's static initializer to run too early, it throws
        //    (GetOffsetOfInstanceIDInCPlusPlusObject native binding not ready), and a type initializer
        //    that throws once POISONS that type for the whole process -> every Unity object is then
        //    dead -> black screen. So the editor is spawned lazily from UIChat.SetText (below), which
        //    only ever runs well after the game is up.

        SafeLog("[InfinityLoader] booted; WebApiURL -> " + (ReadWebApiUrl() ?? "(live AE, no marker)")
            + "; BaseURL -> " + (ReadContentUrl() ?? "(live AE, no marker)")
            + "; UserData=" + _beyondDir);
    }

    // ---- self-update --------------------------------------------------------
    // Compare this DLL's hash with the server-published build. A running assembly cannot replace
    // itself, so a verified download is staged and a detached batch file swaps it after game exit.
    private static void CheckForSelfUpdate()
    {
        try
        {
            string api = ReadWebApiUrl();
            if (string.IsNullOrEmpty(api)) return;
            string selfPath = Assembly.GetExecutingAssembly().Location;
            if (string.IsNullOrEmpty(selfPath) || !File.Exists(selfPath)) return;
            string localHash = Sha256Hex(File.ReadAllBytes(selfPath));
            string remoteHash;
            using (var wc = new InfinityWebClient())
                remoteHash = (wc.DownloadString(api + "mod/InfinityLoader.dll.sha256") ?? "").Trim();
            if (!Regex.IsMatch(remoteHash, "^[0-9a-fA-F]{64}$"))
            {
                SafeLog("[self-update] server has no valid published build, skipping");
                return;
            }
            if (string.Equals(remoteHash, localHash, StringComparison.OrdinalIgnoreCase))
            {
                SafeLog("[self-update] up to date (" + localHash.Substring(0, 8) + ")");
                return;
            }
            byte[] newDll;
            using (var wc = new InfinityWebClient())
                newDll = wc.DownloadData(api + "mod/InfinityLoader.dll");
            if (newDll == null || newDll.Length == 0 ||
                !string.Equals(Sha256Hex(newDll), remoteHash, StringComparison.OrdinalIgnoreCase))
            {
                SafeLog("[self-update] downloaded build hash mismatch, aborting");
                return;
            }
            string updatePath = selfPath + ".update";
            File.WriteAllBytes(updatePath, newDll);
            ScheduleSelfUpdate(updatePath, selfPath);
            SafeLog("[self-update] staged " + localHash.Substring(0, 8) + " -> "
                + remoteHash.Substring(0, 8) + "; applies after this session closes");
        }
        catch (Exception ex) { SafeLog("[self-update] check failed: " + ex.Message); }
    }

    private static string Sha256Hex(byte[] data)
    {
        using (var sha = System.Security.Cryptography.SHA256.Create())
        {
            byte[] hash = sha.ComputeHash(data);
            var sb = new StringBuilder(hash.Length * 2);
            foreach (byte b in hash) sb.Append(b.ToString("x2"));
            return sb.ToString();
        }
    }

    private static void ScheduleSelfUpdate(string updatePath, string targetPath)
    {
        try
        {
            string bat = Path.Combine(Path.GetTempPath(),
                "infinity_loader_update_" + Guid.NewGuid().ToString("N") + ".bat");
            string script =
                "@echo off\r\n" +
                "for /L %%i in (1,1,30) do (\r\n" +
                "  move /Y \"" + updatePath + "\" \"" + targetPath + "\" >nul 2>&1\r\n" +
                "  if not exist \"" + updatePath + "\" goto :done\r\n" +
                "  timeout /T 1 /NOBREAK >nul\r\n" +
                ")\r\n:done\r\ndel \"%~f0\"\r\n";
            File.WriteAllText(bat, script);
            System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo
            {
                FileName = "cmd.exe",
                Arguments = "/C \"" + bat + "\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = System.Diagnostics.ProcessWindowStyle.Hidden,
            });
            SafeLog("[self-update] swap scheduled via " + bat);
        }
        catch (Exception ex) { SafeLog("[self-update] schedule failed: " + ex.Message); }
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
            RevealLoadedHumanoidSlots(asset);
            if (asset != null && asset.transform.Find("CameraFocus") == null)
            {
                var cf = new GameObject("CameraFocus");
                cf.transform.SetParent(asset.transform, worldPositionStays: false);
                cf.transform.localPosition = Vector3.zero;
            }
        }
        catch { }
    }

    public static bool NPCLoader_LoadMob_Prefix(NPCLoader __instance, Monbranch mb)
    {
        try
        {
            if (mb == null || mb.equippedItems == null || mb.equippedItems.Count == 0)
                return true;

            var character = new Monster(mb.ID, mb, ig: false);
            character.init();
            Traverse.Create(__instance).Field("avtGO").SetValue(character.getGameObject());
            bool fired = false;

            character.createAvatar();
            var avt = character.GetAvatar();
            Traverse.Create(__instance).Field("avt").SetValue(avt);
            if (avt != null)
            {
                avt.hideFlame = true;
                avt.OnSetupComplete = (Action<GameObject>)Delegate.Combine(avt.OnSetupComplete, (Action<GameObject>)delegate(GameObject ready)
                {
                    try
                    {
                        if (fired) return;
                        fired = true;
                        GameObject asset = ready != null ? ready : character.getGameObject();
                        var humanoid = avt as HumanoidAvatar;
                        if (humanoid != null && humanoid.CC != null)
                            asset = humanoid.CC.gameObject;
                        if (asset == null) return;
                        int revealed = RevealLoadedHumanoidSlots(asset);
                        asset.transform.localScale = Vector3.one;
                        if (string.IsNullOrEmpty(asset.name) || !asset.name.EndsWith("(Clone)", StringComparison.Ordinal))
                            asset.name = (string.IsNullOrEmpty(mb.strMonName) ? ("NPC" + mb.ID) : mb.strMonName) + "(Clone)";
                        EnsureCameraFocus(asset);
                        StripNpcLoaderRuntimeComponents(asset);
                        __instance.isDone = true;
                        var loaded = Traverse.Create(__instance).Field("OnAssetLoaded").GetValue<Action<GameObject>>();
                        loaded?.Invoke(asset);
                        int activeRenderers = 0;
                        foreach (var renderer in asset.GetComponentsInChildren<Renderer>(includeInactive: true))
                            if (renderer != null && renderer.enabled && renderer.gameObject.activeInHierarchy)
                                activeRenderers++;
                        InfinityLoaderMod.SafeLog("[npc-loader] loaded humanoid npc " + mb.ID
                            + " after avatar setup root=" + asset.name + " revealedSlots=" + revealed
                            + " activeRenderers=" + activeRenderers);
                    }
                    catch (Exception ex) { InfinityLoaderMod.SafeLog("[npc-loader] humanoid complete failed " + ex.Message); }
                });
                avt.OnLoadError = (Action<string>)Delegate.Combine(avt.OnLoadError, (Action<string>)delegate(string error)
                {
                    __instance.isDone = true;
                    var failed = Traverse.Create(__instance).Field("LoadFailed").GetValue<Action<string>>();
                    failed?.Invoke(error);
                });
            }
            return false;
        }
        catch (Exception ex)
        {
            __instance.isDone = true;
            var failed = Traverse.Create(__instance).Field("LoadFailed").GetValue<Action<string>>();
            failed?.Invoke("Humanoid NPC load failed: " + ex.Message);
            InfinityLoaderMod.SafeLog("[npc-loader] humanoid load failed " + ex);
            return false;
        }
    }

    public static int RevealLoadedHumanoidSlots(GameObject asset)
    {
        if (asset == null) return 0;
        var cc = asset.GetComponentInChildren<CustomizableCharacter>(includeInactive: true);
        if (cc == null) return 0;
        try { cc.setActive(true); } catch { }
        int revealed = 0;
        foreach (var slot in cc.GetComponentsInChildren<CustomizableSlot>(includeInactive: true))
        {
            if (slot == null || slot.spriteRenderer == null || slot.spriteRenderer.sprite == null) continue;
            Transform cursor = slot.transform;
            while (cursor != null && cursor != cc.transform)
            {
                cursor.gameObject.SetActive(true);
                cursor = cursor.parent;
            }
            slot.spriteRenderer.enabled = true;
            revealed++;
        }
        return revealed;
    }
    private static void EnsureCameraFocus(GameObject asset)
    {
        if (asset != null && asset.transform.Find("CameraFocus") == null)
        {
            var cf = new GameObject("CameraFocus");
            cf.transform.SetParent(asset.transform, worldPositionStays: false);
            cf.transform.localPosition = Vector3.zero;
        }
    }

    private static void StripNpcLoaderRuntimeComponents(GameObject asset)
    {
        if (asset == null) return;
        foreach (var c in asset.GetComponentsInChildren<Collider2D>(includeInactive: true))
            UnityEngine.Object.Destroy(c);
        foreach (var z in asset.GetComponentsInChildren<ZOffset>(includeInactive: true))
            UnityEngine.Object.Destroy(z);
        foreach (var w in asset.GetComponentsInChildren<Walk>(includeInactive: true))
            UnityEngine.Object.Destroy(w);
    }

    public static void EnsureNpcLoaderPatch()
    {
        if (_npcLoaderPatched) return;
        _npcLoaderPatched = true;
        try
        {
            var h = new Harmony("infinity.local.npc-loader.lazy");
            TryPatch(h, "humanoid NPC loader",
                AccessTools.Method(typeof(NPCLoader), "LoadMob", new[] { typeof(Monbranch) }),
                prefix: nameof(NPCLoader_LoadMob_Prefix));
        }
        catch (Exception ex)
        {
            SafeLog("[npc-loader] lazy patch failed " + ex);
        }
    }

    public static void WebApiPostfix(ref string __result)
    {
        ApplyWebApiRedirect(ref __result);
    }

    public static void UnityWebRequest_SendWebRequest_Prefix(UnityWebRequest __instance)
    {
        try
        {
            if (__instance == null) return;
            string api = ReadWebApiUrl();
            if (string.IsNullOrEmpty(api)) return;
            const string ae = "https://account.aq.com/webapi/Heromart/";
            string url = __instance.url;
            if (!string.IsNullOrEmpty(url) && url.StartsWith(ae, StringComparison.OrdinalIgnoreCase))
            {
                __instance.url = api + "webapi/Heromart/" + url.Substring(ae.Length);
                SafeLog("[heromart] redirect -> " + __instance.url);
            }
        }
        catch (Exception ex) { SafeLog("[heromart] redirect failed: " + ex.Message); }
    }

    // A successful private-server response starts the real assembled-avatar capture.
    public static void ResponseGenerateStatue_Execute_Postfix(ResponseGenerateStatue __instance)
    {
        try
        {
            string api = ReadWebApiUrl();
            if (__instance != null && __instance.Success && !string.IsNullOrEmpty(api))
                InfinityStatueCapture.Begin(api);
        }
        catch (Exception ex) { SafeLog("[statue] capture start failed " + ex); }
    }

    // DynamicStatue hardcodes AE's CDN. Private-server character ids only exist
    // APop links are normally static. Vinchi uses this private sentinel so the link can be
    // resolved at click time with the current character id, then served as a PNG attachment.
    public static bool ApopButton_ClickAction_Prefix(ApopButton __instance)
    {
        if (__instance == null || !string.Equals(__instance.url, "infinity://statue/download",
                                                  StringComparison.OrdinalIgnoreCase))
            return true;

        PlayerInfo info = Entity.myPlayerData == null ? null : Entity.myPlayerData.Info;
        string api = ReadWebApiUrl();
        if (info == null || info.CharID <= 0 || string.IsNullOrEmpty(api))
        {
            SafeLog("[statue] download skipped: no authenticated player or private API");
            return false;
        }

        string url = api + "statue/" + info.CharID + "/download.png";
        SafeLog("[statue] opening download cid=" + info.CharID);
        Application.OpenURL(url);
        return false;
    }

    // Our own equivalent of DynamicStatue's private static `live` list (which our redirected
    // instances never join, since DynamicStatue_Start_Prefix returns false and skips the
    // original Start() entirely). Keyed by cid so DynamicStatue_SetVersion_Postfix can find and
    // reload every currently-spawned instance for that character when the server pushes
    // statueVersion. A dead/destroyed DynamicStatue is pruned lazily on next lookup.
    private static readonly Dictionary<string, List<DynamicStatue>> _liveCustomStatues =
        new Dictionary<string, List<DynamicStatue>>();

    // in our DB, so load the cid render from our WebApiURL instead.
    public static bool DynamicStatue_Start_Prefix(DynamicStatue __instance)
    {
        string api = ReadWebApiUrl();
        if (string.IsNullOrEmpty(api) || __instance == null)
            return true;                         // no Infinity marker: preserve live AE behavior
        HouseItemInstance inst = __instance.GetComponent<HouseItemInstance>();
        // Item 99514 is AE's existing Day 1 reward. Only our separately minted
        // custom house item is redirected to the private statue renderer.
        if (inst == null || inst.data == null || inst.data.ItemID != CUSTOM_STATUE_ITEM_ID)
            return true;
        string cid = ReadStatueMeta(inst == null ? null : inst.Meta, "cid");
        if (string.IsNullOrEmpty(cid))
            return true;
        string rev = ReadStatueMeta(inst.Meta, "rev");
        List<DynamicStatue> bucket;
        if (!_liveCustomStatues.TryGetValue(cid, out bucket))
            _liveCustomStatues[cid] = bucket = new List<DynamicStatue>();
        bucket.Add(__instance);
        LoadCustomStatuePng(__instance, inst, cid, api, rev);
        return false;                            // keep the prefab art on a local load failure
    }

    // The server pushes {Cmd:"statueVersion", cid, version} whenever a statue is (re)generated
    // (see statues.version_push server-side); the shipped ResponseStatueVersion.Execute() already
    // calls this method for us. AE's own body would just re-hit its always-404 CDN for our
    // character ids, so we intercept here and redirect any of OUR live instances for that cid to
    // our webapi instead, cache-busted with the pushed version  the visible statue refreshes
    // without the viewer needing to leave and re-enter the area.
    public static void DynamicStatue_SetVersion_Postfix(string cid, long version)
    {
        try
        {
            string api = ReadWebApiUrl();
            List<DynamicStatue> bucket;
            if (string.IsNullOrEmpty(api) || string.IsNullOrEmpty(cid)
                || !_liveCustomStatues.TryGetValue(cid, out bucket))
                return;
            bucket.RemoveAll(ds => ds == null);
            foreach (DynamicStatue ds in bucket)
            {
                HouseItemInstance inst = ds.GetComponent<HouseItemInstance>();
                if (inst == null) continue;
                LoadCustomStatuePng(ds, inst, cid, api, version.ToString());
            }
            if (bucket.Count == 0) _liveCustomStatues.Remove(cid);
        }
        catch (Exception ex) { SafeLog("[statue] live refresh failed cid=" + cid + ": " + ex.Message); }
    }

    private static void LoadCustomStatuePng(DynamicStatue instance, HouseItemInstance inst,
                                            string cid, string api, string rev)
    {
        string url = api + "statue/" + cid + ".png"
            + (string.IsNullOrEmpty(rev) ? "" : "?v=" + rev);
        Texture2D texture = null;
        Sprite sprite = null;
        try
        {
            byte[] png;
            using (var wc = new InfinityWebClient())
                png = wc.DownloadData(url);
            if (png == null || png.Length == 0)
                return;
            texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            if (!texture.LoadImage(png, false))
            {
                UnityEngine.Object.Destroy(texture);
                return;
            }
            float nativeH = inst.CurrentSpriteNativeHeight;
            Vector2 pivot = inst.CurrentSpritePivotNormalized;
            float ppu = nativeH > 0.0001f ? texture.height / nativeH : 100f;
            sprite = Sprite.Create(texture, new Rect(0f, 0f, texture.width, texture.height),
                                   pivot, ppu);
            inst.ApplyLoadedSprite(sprite);
            var lifetime = instance.GetComponent<InfinityStatueLifetime>();
            if (lifetime == null) lifetime = instance.gameObject.AddComponent<InfinityStatueLifetime>();
            else
            {
                // replacing a previously-applied render: the old texture/sprite are no longer
                // referenced by anything once ApplyLoadedSprite swaps to the new one above.
                if (lifetime.Sprite != null) UnityEngine.Object.Destroy(lifetime.Sprite);
                if (lifetime.Texture != null) UnityEngine.Object.Destroy(lifetime.Texture);
            }
            lifetime.Texture = texture;
            lifetime.Sprite = sprite;
            SafeLog("[statue] loaded private snapshot cid=" + cid + " v=" + rev);
        }
        catch (Exception ex)
        {
            if (sprite != null) UnityEngine.Object.Destroy(sprite);
            if (texture != null) UnityEngine.Object.Destroy(texture);
            SafeLog("[statue] load failed " + url + ": " + ex.Message);
        }
    }

    // 978659's Bundle (78659, items/flooritems/78659_playerksstatue.unity3d) is a genuine live
    // AE asset now (confirmed: HTTP 200 + valid UnityFS header from contentinf.aq.com), so
    // HouseItemManager's stock LoadItemPrefab/SpawnItem download and use the REAL geometry/prefab
    // on their own  no override needed here anymore. This mod only redirects the character
    // PORTRAIT (DynamicStatue's PNG source, below), which is the one piece that structurally can't
    // come from AE (our character ids aren't on AE's Statues CDN). Kept as documentation: this
    // used to force a local Resources fallback / synthetic quad back when the item shipped with
    // Bundle:None (our old fabricated 200002).

    public static void HouseItemManager_SpawnItem_Postfix(PlacedHouseItem phi,
                                                           HouseItemInstance __result)
    {
        if (phi == null || phi.ItemID != CUSTOM_STATUE_ITEM_ID || __result == null) return;
        // SpawnItem configures the clone first; activation starts DynamicStatue only
        // after HouseItemInstance.Meta has been assigned by the original method.
        __result.gameObject.hideFlags = HideFlags.None;
        __result.gameObject.SetActive(true);
    }

    private static string ReadStatueMeta(string meta, string wanted)
    {
        if (string.IsNullOrEmpty(meta)) return null;
        foreach (string part in meta.Split(','))
        {
            int colon = part.IndexOf(':');
            if (colon <= 0 || !string.Equals(part.Substring(0, colon).Trim(), wanted,
                                             StringComparison.OrdinalIgnoreCase))
                continue;
            string value = part.Substring(colon + 1).Trim();
            if (value.Length == 0) return null;
            for (int i = 0; i < value.Length; i++)
                if (value[i] < '0' || value[i] > '9') return null;
            return value;
        }
        return null;
    }
    private static void ApplyWebApiRedirect(ref string __result)
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
    // call (no Unity insecure-http block, no SynchronizationContext deadlock  WebClient is
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
        EnsureNpcLoaderPatch();                // lazy: NPCLoader static init is unsafe at Doorstop boot
        CutsceneEditorController.Spawn();      // lazy fallback: guaranteed in-game, Unity ready
        NpcBakerController.Spawn();            // F9 runtime capture for dressed custom NPCs
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
        if (id <= 4) return;                       // shipped frames  never touch
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
            // bottom, right, top) in sprite px  sized to the dirt/gold frame thickness.
            s = new NameplatePortraitFixerData.NameplatePortraitFixerSettings
            {
                name = key,
                frame = (PortraitFrameId)id,
                portraitSprite    = LoadSpritePng(Path.Combine(dir, key + "_frame.png")),
                // plate is built WIDE (~583x321, the vanilla aspect) so a plain Simple stretch to the
                // 430x240 rect is ~distortion-free  no 9-slice needed.
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

    // ---- login screen + deferred self-update -------------------------------
    private static Sprite _loginBgSprite;
    private static bool _loginBgFetchTried;
    private static bool _selfUpdateTried;

    public static void UILogin_GetLoginData_Postfix(UILogin __instance, WebCom wcom)
    {
        EnsureTicker();
        if (!_selfUpdateTried) { _selfUpdateTried = true; CheckForSelfUpdate(); }
        if (__instance == null) return;
        try
        {
            Transform root = __instance.transform;
            ApplyLoginBackground(root);
            ApplyGameNewsHeading(root, wcom);
        }
        catch (Exception ex) { SafeLog("[login-screen] override failed: " + ex.Message); }
    }

    private static void ApplyGameNewsHeading(Transform root, WebCom wcom)
    {
        Transform tf = root.Find("_/Login2023/Main/Header/NewReleaseText");
        TMP_Text heading = tf != null ? tf.GetComponent<TMP_Text>() : null;
        if (heading == null) return;
        string text = null;
        if (wcom != null && !string.IsNullOrEmpty(wcom.receivedText))
        {
            try
            {
                var vars = Newtonsoft.Json.JsonConvert.DeserializeObject<List<GameVar>>(wcom.receivedText);
                if (vars != null)
                    foreach (var item in vars)
                        if (item != null && item.sInfo == "infinityGameNewsHeading")
                        {
                            text = Game.IsLiveClient() ? item.live : item.test;
                            break;
                        }
            }
            catch (Exception ex) { SafeLog("[login-screen] heading parse failed: " + ex.Message); }
        }
        if (string.IsNullOrEmpty(text))
        {
            string local = Path.Combine(_beyondDir, "loginscreen", "game_news_heading.txt");
            if (File.Exists(local)) text = File.ReadAllText(local).Trim();
        }
        if (!string.IsNullOrEmpty(text))
        {
            heading.text = text;
            SafeLog("[login-screen] Game News heading set: " + text);
        }
    }

    private static void ApplyLoginBackground(Transform root)
    {
        Transform tf = root.Find("_/BG");
        Image bg = tf != null ? tf.GetComponent<Image>() : null;
        if (bg == null) return;
        if (!_loginBgFetchTried)
        {
            _loginBgFetchTried = true;
            string api = ReadWebApiUrl();
            if (!string.IsNullOrEmpty(api))
            {
                try
                {
                    using (var wc = new InfinityWebClient())
                    {
                        byte[] png = wc.DownloadData(api + "loginscreen/background.png");
                        if (png != null && png.Length > 0)
                        {
                            var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                            if (tex.LoadImage(png, false))
                                _loginBgSprite = Sprite.Create(tex, new Rect(0, 0, tex.width, tex.height),
                                    new Vector2(0.5f, 0.5f), 100f);
                            else UnityEngine.Object.Destroy(tex);
                        }
                    }
                }
                catch (Exception ex) { SafeLog("[login-screen] background fetch failed: " + ex.Message); }
            }
            if (_loginBgSprite == null)
                _loginBgSprite = LoadSpritePng(Path.Combine(_beyondDir, "loginscreen", "background.png"));
        }
        if (_loginBgSprite != null)
        {
            bg.sprite = _loginBgSprite;
            SafeLog("[login-screen] custom background applied");
        }
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

    // TEMPORARY sky-blade diagnostics -- see ParticleDiagnostics.cs. Both are wrapped so a
    // trace failure can never disturb the cast itself.
    public static void ParticleExecutePrefix(Entity caster, Newtonsoft.Json.Linq.JObject props)
    {
        try { ParticleDiagnostics.OnExecute(caster, props); } catch { }
    }

    public static void ParticleSpawnPostfix(string fx, UnityEngine.GameObject __result)
    {
        try { ParticleDiagnostics.OnSpawn(fx, __result); } catch { }
    }

    public static void WrapResponsePrefix(byte[] data)
    {
        if (data == null) return;
        string raw = null;
        try { raw = Encoding.UTF8.GetString(data); } catch { }
        try { WritePacket("s2c", raw); } catch { }
        // Capture guild-tag data off the user objects. Runs off the main thread (packet arrival),
        // so we ONLY touch our own dictionary here  never Unity objects. The plate is composed
        // later on the main thread (ResponseAreaJoin/AreaAdd.Execute -> createNameplate), by which
        // point the map is populated. Live colour changes refresh via SetUserData_Postfix.
        try { IngestGuildTags(raw); } catch { }
        try { TemporalMonsterEffects.IngestPacket(raw); } catch { }
    }

    private static void IngestGuildTags(string rawPkt)
    {
        if (string.IsNullOrEmpty(rawPkt)) return;
        bool hasGuild = rawPkt.IndexOf("guildName", StringComparison.Ordinal) >= 0;
        bool hasShop = rawPkt.IndexOf("tagShop", StringComparison.Ordinal) >= 0
                    || rawPkt.IndexOf("\"Cmd\":\"tagShop\"", StringComparison.Ordinal) >= 0;
        if (!hasGuild && !hasShop) return;
        var pkt = Newtonsoft.Json.Linq.JObject.Parse(rawPkt);
        string cmd = (string)pkt["Cmd"];
        if (cmd == "initPlayer") { IngestUserObject(pkt["user"]); IngestTagShop(pkt["tagShop"]); }
        else if (cmd == "tagShop") { IngestTagShop(pkt); }
        else if (cmd == "AreaAdd") { IngestUserObject(pkt["userData"]); }
        else if (cmd == "AreaJoin")
        {
            var uo = pkt["uoBranch"] as Newtonsoft.Json.Linq.JArray;
            if (uo != null) foreach (var u in uo) IngestUserObject(u);
        }
    }

    private static void IngestTagShop(Newtonsoft.Json.Linq.JToken shop)
    {
        if (shop == null) return;
        try
        {
            var pal = shop["palette"] as Newtonsoft.Json.Linq.JArray;
            if (pal != null)
            {
                TagPalette.Clear();
                foreach (var p in pal)
                    TagPalette.Add(new InfinityTagColor
                    {
                        name = (string)p["name"] ?? "",
                        hex = (string)p["hex"] ?? "#FFFFFF",
                        cost = (int?)p["cost"] ?? 0,
                        coins = (bool?)p["coins"] ?? false,
                        animated = (bool?)p["animated"] ?? false,
                    });
            }
            var owned = shop["owned"] as Newtonsoft.Json.Linq.JArray;
            if (owned != null)
            {
                TagOwned.Clear();
                foreach (var o in owned) TagOwned.Add((string)o);
                TagOwned.Add("green");
            }
            TagSelected = (string)shop["selected"] ?? TagSelected;
            TagGuildDefault = (string)shop["guildDefault"] ?? TagGuildDefault;
            // this runs off the main thread (packet arrival)  never touch Unity here. Flag the
            // panel for repaint; the main-thread ticker consumes it.
            TagShopDirty = true;
        }
        catch { }
    }

    private static void IngestUserObject(Newtonsoft.Json.Linq.JToken u)
    {
        if (u == null) return;
        string name = (string)u["Name"];
        if (string.IsNullOrEmpty(name)) return;
        string guild = (string)u["guildName"] ?? "";
        string color = (string)u["guildTagColor"] ?? "";
        lock (_guildLock)
            _guildByName[name] = new KeyValuePair<string, string>(guild, color);
    }

    // Append a coloured "Guild" line under the name. TMP supports multi-line + rich text, so
    // one label carries both. Keeps the existing "(IGNORED) name" text; only adds a second line.
    public static void ComposeNameplateText_Postfix(Player __instance, ref string __result)
    {
        try
        {
            EnsureTicker();
            if (__instance == null || string.IsNullOrEmpty(__instance.Name)) return;
            KeyValuePair<string, string> tag;
            lock (_guildLock)
                if (!_guildByName.TryGetValue(__instance.Name, out tag)) return;
            string guild = tag.Key;
            if (string.IsNullOrEmpty(guild)) return;
            string colorTok = string.IsNullOrEmpty(tag.Value) ? "#99FF00" : tag.Value;
            // "rainbow" (and any animated keyword) isn't a hex  resolve to a per-frame hue offset
            // by name so plates don't all pulse in lock-step.
            string hex = colorTok;
            if (colorTok == "rainbow")
            {
                float off = (Math.Abs(__instance.Name.GetHashCode()) % 360) / 360f;
                float h = ((Time.time * 0.25f) + off) % 1f;
                hex = "#" + ColorUtility.ToHtmlStringRGB(Color.HSVToRGB(h, 0.85f, 1f));
            }
            __result = __result + "\n<size=60%><color=" + hex + ">" + guild + "</color></size>";
        }
        catch { }
    }

    internal static bool AnyRainbowTag()
    {
        lock (_guildLock)
            foreach (var kv in _guildByName)
                if (kv.Value.Value == "rainbow") return true;
        return false;
    }

    private static void EnsureTicker()
    {
        if (_ticker != null) return;
        try
        {
            var go = new GameObject("InfinityNameplateTicker");
            UnityEngine.Object.DontDestroyOnLoad(go);
            _ticker = go.AddComponent<NameplateTicker>();
        }
        catch { }
    }

    // After a live user-data update (e.g. our AreaAdd rebroadcast on a colour change), redraw the
    // plate so the new guild/colour shows without a relog. Main thread -> safe to touch Unity.
    public static void SetUserData_Postfix(Player __instance)
    {
        try { __instance?.RefreshNameplate(); } catch { }
    }

    // The guild panel (FriendListUI) just repopulated  in guild mode, append our colour picker.
    public static void FriendList_Refresh_Postfix(FriendListUI __instance)
    {
        try { InfinityGuildColorPicker.OnRefreshed(__instance); } catch { }
    }

    public static void UICharacterCanvas_Start_Postfix(UICharacterCanvas __instance)
    {
        InfinityAchievementsPanel.EnableButton(__instance);
    }

    public static void UICharacterCanvas_Achievements_Postfix(UICharacterCanvas __instance)
    {
        InfinityAchievementsPanel.Show(__instance);
    }

    public static void UICharacterCanvas_OtherTab_Prefix(UICharacterCanvas __instance)
    {
        InfinityAchievementsPanel.Hide(__instance);
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

/// <summary>One guild-tag palette colour (initPlayer.tagShop.palette entry).</summary>
public class InfinityTagColor
{
    public string name;
    public string hex;
    public int cost;
    public bool coins;      // true => priced in AdventureCoins, false => gold
    public bool animated;   // true => rendered as a cycling rainbow, not a flat hex
}

/// <summary>Per-frame driver: cycles rainbow nameplates and repaints the guild panel when the
/// tag shop changes off-thread. One instance, created lazily on the main thread.</summary>
public class NameplateTicker : MonoBehaviour
{
    private float _last;

    private void Update()
    {
        try
        {
            TemporalMonsterEffects.Tick();
            if (InfinityLoaderMod.TagShopDirty)
            {
                InfinityLoaderMod.TagShopDirty = false;
                InfinityGuildColorPicker.RefreshOpenPanel();
            }
            if (Time.time - _last < 0.08f) return;      // ~12 fps is plenty for a smooth cycle
            _last = Time.time;
            if (InfinityLoaderMod.AnyRainbowTag()) Player.RefreshAllNameplates();
        }
        catch { }
    }
}

/// <summary>Injects a colour-picker into the guild panel (FriendListUI). Each palette colour is a
/// row on the shared list template: owned -> click to wear, unowned -> click to buy. Sends the
/// same server `/tagcolor` command the chat path uses, so the backend is unchanged.</summary>
public static class InfinityGuildColorPicker
{
    private static FriendListUI _open;

    public static void OnRefreshed(FriendListUI ui)
    {
        try
        {
            if (ui == null) return;
            var t = Traverse.Create(ui);
            var mode = t.Field("mode").GetValue();
            if (mode == null || mode.ToString() != "Guild") return;
            if (Entity.myPlayerData == null || Entity.myPlayerData.Info == null
                || Entity.myPlayerData.Info.guild == null) return;   // only your own guild panel
            _open = ui;
            var listContent = t.Field("listContent").GetValue<Transform>();
            var template = t.Field("itemTemplate").GetValue<GameObject>();
            var spawned = t.Field("spawnedItems").GetValue<List<GameObject>>();
            if (listContent == null || template == null) return;
            AddRow(listContent, template, spawned, " GUILD TAG COLOURS ", "", null, null, false);
            foreach (var c in InfinityLoaderMod.TagPalette)
            {
                bool owned = InfinityLoaderMod.TagOwned.Contains(c.name);
                bool selected = string.Equals(InfinityLoaderMod.TagSelected, c.name,
                    StringComparison.OrdinalIgnoreCase);
                string label = Cap(c.name) + (selected ? "  " : "");
                string right = owned ? "OWNED"
                    : (c.cost == 0 ? "FREE" : c.cost.ToString("N0") + (c.coins ? " AC" : " gold"));
                Color col;
                if (!ColorUtility.TryParseHtmlString(string.IsNullOrEmpty(c.hex) ? "#FFFFFF" : c.hex, out col))
                    col = Color.white;
                string nm = c.name; bool own = owned;
                AddRow(listContent, template, spawned, label, right, col,
                    () => OnColorClicked(nm, own), true);
            }
        }
        catch { }
    }

    private static void OnColorClicked(string name, bool owned)
    {
        try
        {
            if (AEC.Instance == null) return;
            if (owned)
            {
                AEC.Instance.sendRequest(new RequestCmd("tagcolor", new[] { "tagcolor", name }));
                InfinityLoaderMod.TagSelected = name;       // optimistic; server confirms via tagShop
            }
            else
            {
                AEC.Instance.sendRequest(new RequestCmd("tagcolor", new[] { "tagcolor", "buy", name }));
            }
            RefreshOpenPanel();
        }
        catch { }
    }

    public static void RefreshOpenPanel()
    {
        try
        {
            if (_open != null && _open.isActiveAndEnabled)
                Traverse.Create(_open).Method("Refresh").GetValue();
        }
        catch { }
    }

    private static void AddRow(Transform parent, GameObject template, List<GameObject> spawned,
        string left, string right, Color? swatch, Action onClick, bool interactive)
    {
        var go = UnityEngine.Object.Instantiate(template, parent);
        go.SetActive(true);
        spawned?.Add(go);
        SetText(go.transform, "PlayerName", left);
        SetText(go.transform, "ServerText", right);
        SetText(go.transform, "LevelText", "");
        var on = FindDeep(go.transform, "OnlineCircle");
        var off = FindDeep(go.transform, "OfflineCircle");
        if (off != null) off.gameObject.SetActive(false);
        if (on != null)
        {
            on.gameObject.SetActive(swatch.HasValue);
            if (swatch.HasValue)
            {
                var img = on.GetComponent<Image>();
                if (img != null) img.color = swatch.Value;
            }
        }
        var btn = go.GetComponent<Button>();
        if (btn != null)
        {
            btn.onClick.RemoveAllListeners();
            if (interactive && onClick != null) btn.onClick.AddListener(() => onClick());
        }
    }

    private static void SetText(Transform root, string child, string val)
    {
        var t = FindDeep(root, child);
        if (t == null) return;
        var tmp = t.GetComponent<TMP_Text>();
        if (tmp != null) tmp.text = val;
    }

    private static Transform FindDeep(Transform root, string name)
    {
        if (root == null) return null;
        if (root.name == name) return root;
        for (int i = 0; i < root.childCount; i++)
        {
            var r = FindDeep(root.GetChild(i), name);
            if (r != null) return r;
        }
        return null;
    }

    private static string Cap(string s)
    {
        return string.IsNullOrEmpty(s) ? s : char.ToUpper(s[0]) + s.Substring(1);
    }
}

/// <summary>Runtime contents for AE's shipped-but-empty Character -> Achievements tab.</summary>
public static class InfinityAchievementsPanel
{
    private sealed class Entry
    {
        public readonly int bit;
        public readonly string name;
        public readonly string description;
        public Entry(int bit, string name, string description)
        {
            this.bit = bit; this.name = name; this.description = description;
        }
    }

    private static readonly Entry[] FounderAchievements =
    {
        new Entry(0, "Infinity: Day One", "Backed AdventureQuest Worlds: Infinity on its first day."),
        new Entry(1, "Infinity: 100% Funded", "Helped the Infinity Kickstarter reach its funding goal."),
        new Entry(2, "Infinity: Founder", "AdventureQuest Worlds: Infinity Founder."),
        new Entry(3, "Infinity: Epic Founder", "AdventureQuest Worlds: Infinity Epic Founder."),
        new Entry(4, "Infinity: Underworld Founder", "AdventureQuest Worlds: Infinity Underworld Founder."),
        new Entry(5, "Infinity: Legendary Founder", "AdventureQuest Worlds: Infinity Legendary Founder."),
        new Entry(6, "Infinity: Immortalized", "An Infinity founder immortalized in the world of Lore."),
        new Entry(7, "Infinity: Benevolent", "Supported Infinity at the Benevolent Founder tier."),
        new Entry(8, "Infinity: Weapon Designer", "Earned the Infinity Weapon Designer founder reward."),
        new Entry(9, "Infinity: Armor Designer", "Earned the Infinity Armor Designer founder reward."),
        new Entry(10, "Infinity: Mysterious Offer", "Accepted the Mysterious Stranger's Infinity offer.")
    };

    private const string PanelName = "InfinityAchievementsPanel";

    public static void EnableButton(UICharacterCanvas canvas)
    {
        try
        {
            if (canvas == null) return;
            Button found = null;
            foreach (Button button in canvas.GetComponentsInChildren<Button>(true))
            {
                if (button == null || button.name.IndexOf("Achiev", StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                found = button;
                button.gameObject.SetActive(true);
                button.interactable = true;
                button.onClick.AddListener(canvas.Tab_ShowAchievments);
                InfinityLoaderMod.SafeLog("[achievements] enabled button " + button.name);
            }
            if (found == null)
                InfinityLoaderMod.SafeLog("[achievements] button not found under UICharacterCanvas");
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[achievements] enable failed: " + ex); }
    }

    public static void Show(UICharacterCanvas canvas)
    {
        try
        {
            if (canvas == null) return;
            Transform old = canvas.transform.Find(PanelName);
            if (old != null) UnityEngine.Object.Destroy(old.gameObject);

            GameObject panel = MakeUI(PanelName, canvas.transform);
            RectTransform panelRect = panel.GetComponent<RectTransform>();
            panelRect.anchorMin = new Vector2(0.29f, 0.07f);
            panelRect.anchorMax = new Vector2(0.985f, 0.91f);
            panelRect.offsetMin = Vector2.zero;
            panelRect.offsetMax = Vector2.zero;
            Image panelImage = panel.AddComponent<Image>();
            panelImage.color = new Color(0.025f, 0.045f, 0.085f, 0.86f);

            GameObject header = MakeUI("Header", panel.transform);
            RectTransform headerRect = header.GetComponent<RectTransform>();
            headerRect.anchorMin = new Vector2(0f, 0.88f);
            headerRect.anchorMax = Vector2.one;
            headerRect.offsetMin = new Vector2(22f, 0f);
            headerRect.offsetMax = new Vector2(-22f, -8f);
            TMP_Text title = AddText(header, canvas, 27f, FontStyles.Bold);
            title.text = "INFINITY FOUNDER COLLECTION";
            title.alignment = TextAlignmentOptions.MidlineLeft;
            title.color = new Color(0.96f, 0.76f, 0.34f);

            bool ownCharacter = UICharacterCanvas.myPlayer == null
                || UICharacterCanvas.myPlayer == Entity.mainPlayer;
            int unlocked = 0;
            if (ownCharacter && Entity.myPlayerData != null && Entity.myPlayerData.Info != null)
                for (int i = 0; i < FounderAchievements.Length; i++)
                    if (Entity.myPlayerData.Info.hasAchievement("ip25", FounderAchievements[i].bit)) unlocked++;
            title.text += "  <size=60%><color=#C9D2E3>" + unlocked + "/"
                + FounderAchievements.Length + "</color></size>";

            GameObject content = MakeUI("Content", panel.transform);
            RectTransform contentRect = content.GetComponent<RectTransform>();
            contentRect.anchorMin = new Vector2(0f, 0f);
            contentRect.anchorMax = new Vector2(1f, 0.88f);
            contentRect.offsetMin = new Vector2(20f, 18f);
            contentRect.offsetMax = new Vector2(-20f, -6f);
            GridLayoutGroup grid = content.AddComponent<GridLayoutGroup>();
            grid.padding = new RectOffset(4, 4, 4, 4);
            grid.spacing = new Vector2(12f, 11f);
            grid.cellSize = new Vector2(610f, 88f);
            grid.startCorner = GridLayoutGroup.Corner.UpperLeft;
            grid.startAxis = GridLayoutGroup.Axis.Horizontal;
            grid.childAlignment = TextAnchor.UpperCenter;
            grid.constraint = GridLayoutGroup.Constraint.FixedColumnCount;
            grid.constraintCount = 2;

            if (!ownCharacter)
            {
                AddMessage(content.transform, canvas,
                    "Another player's achievements are private.");
            }
            else
            {
                foreach (Entry entry in FounderAchievements)
                {
                    bool earned = Entity.myPlayerData != null && Entity.myPlayerData.Info != null
                        && Entity.myPlayerData.Info.hasAchievement("ip25", entry.bit);
                    AddAchievement(content.transform, canvas, entry, earned);
                }
            }
            panel.transform.SetAsLastSibling();
        }
        catch (Exception ex) { InfinityLoaderMod.SafeLog("[achievements] panel failed: " + ex); }
    }

    public static void Hide(UICharacterCanvas canvas)
    {
        try
        {
            if (canvas == null) return;
            Transform panel = canvas.transform.Find(PanelName);
            if (panel != null) UnityEngine.Object.Destroy(panel.gameObject);
        }
        catch { }
    }

    private static void AddAchievement(Transform parent, UICharacterCanvas canvas, Entry entry, bool earned)
    {
        GameObject row = MakeUI("Achievement_ip25_" + entry.bit, parent);
        LayoutElement size = row.AddComponent<LayoutElement>();
        size.preferredHeight = 82f;
        Image bg = row.AddComponent<Image>();
        bg.color = earned ? new Color(0.18f, 0.115f, 0.045f, 0.96f)
                          : new Color(0.07f, 0.08f, 0.11f, 0.94f);
        GameObject label = MakeUI("Label", row.transform);
        RectTransform labelRect = label.GetComponent<RectTransform>();
        labelRect.anchorMin = Vector2.zero;
        labelRect.anchorMax = Vector2.one;
        labelRect.offsetMin = Vector2.zero;
        labelRect.offsetMax = Vector2.zero;
        TMP_Text text = AddText(label, canvas, 16f, FontStyles.Normal);
        text.margin = new Vector4(16f, 7f, 14f, 5f);
        text.alignment = TextAlignmentOptions.MidlineLeft;
        text.textWrappingMode = TextWrappingModes.Normal;
        text.text = (earned ? "<color=#F2C45F>UNLOCKED</color>  " : "<color=#747D8D>LOCKED</color>  ")
            + "<b>" + entry.name + "</b>\n<size=74%><color=#CBD2DD>" + entry.description
            + "</color></size>";
    }

    private static void AddMessage(Transform parent, UICharacterCanvas canvas, string message)
    {
        GameObject row = MakeUI("Message", parent);
        row.AddComponent<LayoutElement>().preferredHeight = 90f;
        TMP_Text text = AddText(row, canvas, 23f, FontStyles.Italic);
        text.text = message;
        text.color = new Color(0.75f, 0.79f, 0.86f);
        text.alignment = TextAlignmentOptions.Center;
    }

    private static GameObject MakeUI(string name, Transform parent)
    {
        GameObject go = new GameObject(name, typeof(RectTransform));
        go.transform.SetParent(parent, false);
        return go;
    }

    private static TMP_Text AddText(GameObject go, UICharacterCanvas canvas, float size, FontStyles style)
    {
        TextMeshProUGUI text = go.AddComponent<TextMeshProUGUI>();
        text.fontSize = size;
        text.fontStyle = style;
        text.color = Color.white;
        text.raycastTarget = false;
        if (canvas != null && canvas.text_playerName != null)
        {
            text.font = canvas.text_level != null ? canvas.text_level.font : canvas.text_playerName.font;
        }
        return text;
    }
}
