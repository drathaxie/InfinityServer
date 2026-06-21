using System.Collections.Generic;
using System.IO;
using HarmonyLib;
using MelonLoader;
using MelonLoader.Utils;
using Newtonsoft.Json;
using Pixelplacement;

namespace Infinity_TestMod.Patches
{
    /// <summary>
    /// Fully detaches login from Artix Entertainment.
    ///
    /// Two parts:
    ///  1. CapturePatch (always on): after a *real* AE login, dump the parsed
    ///     SessionState.LoginData to infinity_logindata.json. This is a one-time
    ///     bootstrap to harvest the asset bundles (the CustomizableCharacter
    ///     base bundle CharSelect needs) + a real character — data the game
    ///     socket capture never saw because login is HTTP.
    ///  2. LoginPatch (opt-in via infinity_local.txt): replace UILoginActions.Login
    ///     entirely — no network call. It loads the captured LoginData, overrides
    ///     the username/token/servers (and renames the character), and walks the
    ///     normal CharSelect path. Any username/password works; nothing touches AE.
    ///
    /// The authoritative account/character lives in InfinityServer's SQLite DB,
    /// keyed on RequestLogin.Params[1] (the username); the captured LoginData is
    /// only what the pre-game UI needs to render and reach ServerSelect.
    ///
    /// Bootstrap once: delete infinity_local.txt, log in with a real AE account
    /// (writes infinity_logindata.json), then recreate infinity_local.txt.
    /// </summary>
    [HarmonyPatch(typeof(UILoginActions), nameof(UILoginActions.Login))]
    public static class UILoginActionsLoginPatch
    {
        public static bool Prefix(string u, string p, ref WebCom __result)
        {
            string dir = MelonEnvironment.UserDataDirectory;
            if (!File.Exists(Path.Combine(dir, "infinity_local.txt")))
                return true;   // opt-in only; run real AE login

            string ldPath = Path.Combine(dir, "infinity_logindata.json");
            if (!File.Exists(ldPath))
            {
                MelonLogger.Error("[LocalLogin] infinity_logindata.json missing. "
                    + "Bootstrap once: remove infinity_local.txt, log in with a real "
                    + "AE account to capture it, then recreate infinity_local.txt.");
                return true;   // fall back to AE login so the user isn't bricked
            }

            try
            {
                var login = JsonConvert.DeserializeObject<LoginData>(File.ReadAllText(ldPath));
                string token = "local-" + u;

                login.bSuccess = true;
                login.account.unm = u;
                login.account.sToken = token;
                login.account.hasAlphaAccess = true;
                if (login.account.chars != null && login.account.chars.Count > 0)
                    login.account.chars[0].Name = u;     // show the chosen name
                login.servers = new List<ServerSelectData>
                {
                    new ServerSelectData
                    {
                        Name = "Infinity Local", IP = "127.0.0.1", Port = 5588,
                        Online = true, playerCount = 1, maxPlayers = 9999,
                        AccessLevel = 0, Level = 0, Language = "en",
                    },
                };

                SessionState.LoginData = login;
                DataManager.Token = token;
                Game.requestLogin = new RequestLogin
                {
                    Params = new List<string> { "LOCAL", u, token },
                };

                UILoginActions.JumpServer = false;
                Singleton<StateManager>.Instance.stateMachine.ChangeState("CharSelect");

                int nchars = login.account.chars?.Count ?? 0;
                int nbundles = login.bundles?.Count ?? 0;
                MelonLogger.Msg($"[LocalLogin] '{u}' accepted (chars={nchars}, bundles={nbundles}) -> CharSelect");
                __result = new WebCom();
                return false;
            }
            catch (System.Exception ex)
            {
                MelonLogger.Error("[LocalLogin] failed, falling back to AE login: " + ex);
                return true;
            }
        }
    }

    /// <summary>
    /// Always-on: after a real AE login parses LoginData, save it so local login
    /// can replay it. Runs only on genuine logins (in local mode the original
    /// onWebDataReceived never fires, since we skip the web request).
    /// </summary>
    [HarmonyPatch(typeof(UILoginActions), "onWebDataReceived")]
    public static class UILoginActionsCapturePatch
    {
        public static void Postfix()
        {
            try
            {
                var ld = SessionState.LoginData;
                if (ld == null || !ld.bSuccess) return;
                string path = Path.Combine(MelonEnvironment.UserDataDirectory, "infinity_logindata.json");
                File.WriteAllText(path, JsonConvert.SerializeObject(ld));
                MelonLogger.Msg($"[LocalLogin] captured real LoginData -> {path} "
                    + $"(bundles={ld.bundles?.Count ?? 0}, chars={ld.account?.chars?.Count ?? 0})");
            }
            catch (System.Exception ex)
            {
                MelonLogger.Error("[LocalLogin] capture failed: " + ex);
            }
        }
    }
}
