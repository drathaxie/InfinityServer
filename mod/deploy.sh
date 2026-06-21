#!/usr/bin/env bash
# Build InfinityLoader and deploy our client mod into the AE:I install — no MelonLoader.
#
# AE:I ships its own HarmonyLib (0Harmony 2.4.2.0) in Managed/, which collides with
# MelonLoader's bootstrap Harmony and prevents MelonLoader from loading. So we inject with
# Unity Doorstop and patch using AE's OWN Harmony (see InfinityLoader/Entrypoint.cs).
#
# Usage: ./deploy.sh            (auto-detects the default Steam path)
#        AQWI_GAME_DIR="..." ./deploy.sh
#
# The game must be CLOSED (winhttp.dll is locked while it runs).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
G="${AQWI_GAME_DIR:-/c/Program Files (x86)/Steam/steamapps/common/AdventureQuest Worlds Unity Playtest}"
[ -d "$G" ] || { echo "game dir not found: $G  (set AQWI_GAME_DIR)"; exit 1; }

echo "[1/4] build InfinityLoader"
( cd "$HERE/InfinityLoader" && dotnet build -c Release -v minimal )

echo "[2/4] remove any MelonLoader (it can't load on this build)"
rm -f  "$G/version.dll"
rm -rf "$G/MelonLoader"
rm -rf "$G/Mods"

echo "[3/4] install Doorstop + loader"
cp "$HERE/doorstop/winhttp.dll"        "$G/winhttp.dll"
cp "$HERE/doorstop/.doorstop_version"  "$G/.doorstop_version"
cp "$HERE/doorstop/doorstop_config.ini" "$G/doorstop_config.ini"
cp "$HERE/InfinityLoader/bin/Release/InfinityLoader.dll" "$G/InfinityLoader.dll"
# Harmony 2.4.2.0 (Lib.Harmony net472) — the loader's dependency. The base game does NOT
# ship 0Harmony.dll, so it MUST be installed alongside the loader or it FATALs at startup
# with "Could not load 0Harmony, Version=2.4.2.0".
cp "$HERE/doorstop/0Harmony.dll"       "$G/0Harmony.dll"

echo "[4/4] activate the API redirect (opt-in marker)"
mkdir -p "$G/UserData"
printf 'http://127.0.0.1:8182/\n' > "$G/UserData/infinity_api.txt"

echo "done. Start the local servers (server.py + webapi.py), then launch the game."
echo "Assets stream DIRECTLY from AE's CDN (no local mirror/cache)."
echo "To play LIVE AE again: delete $G/UserData/infinity_api.txt (or set doorstop enabled=false)."
