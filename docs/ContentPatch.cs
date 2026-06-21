using System.IO;
using HarmonyLib;
using MelonLoader;
using MelonLoader.Utils;

namespace Infinity_TestMod.Patches
{
    /// <summary>
    /// Hosts asset bundles (maps, armors, monsters, etc.) locally instead of the
    /// Artix CDN. AssetBundleLoader.GetUrl composes every bundle URL as
    /// Game.BaseURL + "assetbundles/...". We rewrite the contentinf.aq.com prefix
    /// to a local content mirror (server/content.py), which caches from the CDN on
    /// first touch and serves locally thereafter.
    ///
    /// Toggle: UserData/infinity_content.txt. Empty file => default
    /// http://127.0.0.1:8080/game/ ; or put a base URL on the first line.
    /// Delete the file to load bundles straight from the CDN again.
    /// </summary>
    [HarmonyPatch(typeof(AssetBundleLoader), nameof(AssetBundleLoader.GetUrl))]
    public static class AssetBundleGetUrlPatch
    {
        // Any AE game host -> rewrite the "<host>/game/" prefix to local "game/".
        private static readonly string[] CdnPrefixes =
        {
            "https://contentinf.aq.com/game/",
            "https://stageinf.aq.com/game/",
            "https://infinity.aq.com/game/",
        };
        private const string DefaultLocal = "http://127.0.0.1:8080/game/";

        public static void Postfix(ref string __result)
        {
            if (string.IsNullOrEmpty(__result)) return;
            string flag = Path.Combine(MelonEnvironment.UserDataDirectory, "infinity_content.txt");
            if (!File.Exists(flag)) return;   // opt-in

            string local = DefaultLocal;
            try
            {
                string first = File.ReadAllText(flag).Trim();
                if (first.Length > 0)
                    local = first.EndsWith("/") ? first : first + "/";
            }
            catch { }

            foreach (string cdn in CdnPrefixes)
            {
                if (__result.StartsWith(cdn))
                {
                    __result = local + __result.Substring(cdn.Length);
                    return;
                }
            }
        }
    }
}
