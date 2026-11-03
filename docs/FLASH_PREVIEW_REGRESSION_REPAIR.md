# Item preview and loading spinner regression, 2026-10-13

The user reported generic item-preview icons and a loading circle that no longer
rotated. Conversion publication was stopped before native09 reached deployment;
its five prepared entries are retained. Native01-native08 remain published and
verified: 682 distinct bundles, 713 existing item records.

## Evidence

The installed loader was `077201b7df1fa145050605e9a1e9aab15e5dec991586683d274330606b5fabcb`.
The staged interactive-morph loader had not been installed. The running client
was PID 63584 when diagnosed. Its current Player.log was empty, so the diagnostic
evidence is the previous client-session log, preserved under
`outputs/flash_converter_trial/preview_regression/Player-prev.before.log`.

That log contains 9,501 instances of a missing native binding:
`UnityEngine.Quaternion.Internal_FromEulerRad_Injected`.
The affected paths include `Rotator.Update` and `PreviewSpriteImage.Update`.
Decompiled `PreviewSpriteImage.setup` also uses `Quaternion.Euler` before
`DetermineDisplayedObject`; its catch displays the generic item icon. This gives
a common failure path for the two symptoms. The reason the installed native
binding fails has not been established. No Unity engine binary was replaced.

Client binary hashes and sizes were preserved in `client_binary_preflight.json`.
The installed native player identifies itself as Unity 6000.3.17f1. The game
Assembly-CSharp SHA256 remains
`260c6d53c31d1f6bdc04df3a48ca084d31c1af0b85bd763eb23ada32d6cc4f34`.

## Repair and tests

`mod/InfinityLoader/UnityRotationCompatibility.cs` probes the native managed
wrapper through reflection. Working native installations remain unpatched. If
the probe raises the observed `MissingMethodException`, a Harmony prefix supplies
the equivalent Z-X-Y Euler conversion using managed arithmetic. The repaired
wrapper is probed again and the result is written to the loader log.

The Unity validation passed 10,004 comparisons against native rotations and
360 repeated Transform.Rotate updates, plus the preview's localEulerAngles
setter. The maximum quaternion component error was 1.1920928955078126e-7. The
working-native branch was verified to install no patch. The loader compiled
against the installed client with zero warnings and zero errors.

## Installation and proof boundary

New loader SHA256:
`ff71717154c55687518c968827a1079a51aa10d7b56c64eba72f4bcc0ab0ac2a`.

The repository distribution, public DLL/hash endpoint, and local installed DLL
were read back and match that hash. Server API/game services stayed active and
the server game PID was unchanged. Backups:

- `/opt/infinity/backups/flash-rig-preview-loader-20261013T054734Z`
- `C:/Program Files (x86)/Steam/steamapps/common/AdventureQuest Worlds Unity Playtest/InfinityServer-backup-preview-20261013T054737Z`

The user restarted the client and confirmed both symptoms were fixed: "Cool,
fixed, continue the conversions". The new client log records the failed native
probe followed by "managed Euler repair installed and verified", and its updater
reports loader `ff717171` as current. Native09 has resumed. The user's confirmation
is the real-client UI evidence; the numeric/editor checks are separate evidence.

Elemental Dracomancer was subsequently published in native10 as bundle 5091
version 5 and granted unequipped to Redux. Native09 completed 54 additional
native weapon replacements. See `FLASH_INTERACTIVE_MORPHS.md` for the observed
morph-metadata rejection and the subsequent managed JSON/input-routing repair.
The newer loader is `9c9d8bc2`; it retains the same Euler rotation repair source.
Real-client clicks on that newer loader still require a full restart and test.
