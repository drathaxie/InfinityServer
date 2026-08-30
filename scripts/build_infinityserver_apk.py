#!/usr/bin/env python3
"""One-step AQ2D -> InfinityServer Android build: patch, align, and sign, with no
Android SDK / JDK / apksigner dependency at all -- everything zipalign and apksigner
would normally do is reimplemented in pure Python (apk_zipalign.py, apk_v2_sign.py),
verified correct against the real tools during development but not required to run
this script or its packaged .exe.

Usage:
    python build_infinityserver_apk.py <source_apk> [output_apk]

If output_apk is omitted, the result is written next to the source APK as
"<name>-infinityserver.apk". A local signing identity is generated once (on first
run) and reused afterward, in the same directory as the output, as
"infinityserver-local.key.pem" / ".cert.pem" -- keep these if you want later
rebuilds to install as updates over an earlier one instead of being rejected as a
different app (Android ties that to the signing certificate).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from port_aq2d_android import build_patched_bytes, SERVER_URL  # noqa: E402
from apk_zipalign import zipalign_bytes  # noqa: E402
from apk_v2_sign import load_or_create_identity, sign_v2  # noqa: E402


def build_signed_apk(source_apk: Path, output_apk: Path, identity_base: Path) -> None:
    print(f"[1/3] patching {source_apk.name} ...")
    patched = build_patched_bytes(source_apk)

    print("[2/3] aligning ...")
    aligned = zipalign_bytes(patched, alignment=4, so_alignment=16384)

    print("[3/3] signing ...")
    key, cert = load_or_create_identity(identity_base)
    signed = sign_v2(aligned, key, cert)

    output_apk.parent.mkdir(parents=True, exist_ok=True)
    output_apk.write_bytes(signed)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1
    source_apk = Path(sys.argv[1])
    if not source_apk.exists():
        print(f"source APK not found: {source_apk}", file=sys.stderr)
        return 1

    output_apk = (Path(sys.argv[2]) if len(sys.argv) > 2
                 else source_apk.with_name(source_apk.stem + "-infinityserver.apk"))
    identity_base = output_apk.parent / "infinityserver-local"

    try:
        build_signed_apk(source_apk, output_apk, identity_base)
    except Exception as error:                          # noqa: BLE001 -- top-level CLI boundary
        print(f"Build failed: {error!r}", file=sys.stderr)
        return 1

    print(f"\nDone: {output_apk}")
    print(f"Web API base URL: {SERVER_URL}")
    print(f"Signing identity: {identity_base}.key.pem / .cert.pem "
         "(generated on first run, reused after -- keep it to install updates "
         "over a previous local build instead of a fresh app).")
    print("\nInstall on a device with:")
    print(f"  adb install \"{output_apk}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
