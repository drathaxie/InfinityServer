using HarmonyLib;
using MelonLoader;
using MelonLoader.Utils;
using System.IO;

namespace Infinity_TestMod.Patches
{
    /// <summary>
    /// Redirects the client's outgoing game-server connection to a local
    /// InfinityServer emulator. AEC.connect(ipAddr, port, serverid) is invoked
    /// by UIServerButton when the user picks a server from the list; we rewrite
    /// the destination before the socket opens.
    ///
    /// Toggle = a marker file, so no env var / Steam relaunch is needed:
    ///   MelonLoader/UserData/infinity_local.txt
    /// If the file exists, every connect is redirected. Optionally put
    /// "ip:port" on the first line to override the default 127.0.0.1:5588;
    /// an empty file uses the default. Delete the file to play live again.
    /// </summary>
    [HarmonyPatch(typeof(AEC), nameof(AEC.connect))]
    public static class AECConnectRedirectPatch
    {
        private const string DefaultIp = "127.0.0.1";
        private const int DefaultPort = 5588;   // must match server/server.py

        public static void Prefix(ref string ipAddr, ref int port)
        {
            string flag = Path.Combine(MelonEnvironment.UserDataDirectory, "infinity_local.txt");
            if (!File.Exists(flag)) return;   // opt-in only

            string ip = DefaultIp;
            int p = DefaultPort;
            try
            {
                string first = File.ReadAllText(flag).Trim();
                if (first.Length > 0)
                {
                    int colon = first.LastIndexOf(':');
                    if (colon > 0)
                    {
                        ip = first.Substring(0, colon).Trim();
                        int.TryParse(first.Substring(colon + 1).Trim(), out p);
                    }
                    else ip = first;
                }
            }
            catch { /* unreadable flag — fall back to defaults */ }

            MelonLogger.Msg($"[Redirect] {ipAddr}:{port} -> {ip}:{p}");
            ipAddr = ip;
            port = p;
        }
    }
}
