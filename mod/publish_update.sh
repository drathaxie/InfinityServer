#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA_MOD="$HERE/../data/mod"
DLL="$HERE/InfinityLoader/bin/Release/InfinityLoader.dll"

( cd "$HERE/InfinityLoader" && dotnet build -c Release -v minimal )
mkdir -p "$DATA_MOD"
HASH=$(sha256sum "$DLL" | cut -d' ' -f1)
cp "$DLL" "$DATA_MOD/InfinityLoader.dll"
printf '%s\n' "$HASH" > "$DATA_MOD/InfinityLoader.dll.sha256"
echo "published InfinityLoader.dll ($HASH)"
