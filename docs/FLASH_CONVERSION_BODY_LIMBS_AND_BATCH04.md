# Body skin, back-limb opacity, and batch04

The user confirmed batch03 generally looks good, then supplied screenshots of white body skin on Chronomancer Pirate Captain and transparent internal seams on ChronoStriker's black back limbs. Caladbolg, the working pet, and the existing loader animation hook were left alone.

## Repairs

The body converter had treated Skin as a diffuse color multiplier. The native compositor instead samples a skin gradient using diffuse red. Its `SkinGradient.SetGradient/CreateGradientTexture` implementation and the actual compositor shader's DX11 instructions were inspected locally. The native face uses grayscale 165 for normal skin and 90 for its shadow. The converter now maps sampled Flash skin shading to these native coordinates and preserves fixed ink through the mask. The comparison emulator now includes the client's 32-texel, point-sampled, linearized skin gradient. Skin shading intentionally follows the native face; it remains an approximation of Flash shade clipping.

Back silhouettes had inherited partially transparent seams between the front artwork's vector shapes. Back limbs now get separate black layer images with filled opaque interiors and retained antialiased outer boundaries. Their existing keyed transforms, frame changes, and periods are retained. The front layer artwork is unchanged by this silhouette treatment.

GPU validation now covers static body parts and feet as well as animated layers and heads. Child eye renderers remain isolated from these captures. `verify_body_limb_fix.py` checks face/body agreement for three skin palettes in both genders and checks the interiors of 78 back-layer images. Both repaired bundles passed 224 GPU captures each; worst mean errors were 0.226/255 for ChronoStriker and 0.047/255 for Captain. These checks do not replace observation in the running game.

| Existing trial | ID | Published version | SHA256 |
|---|---:|---:|---|
| ChronoStriker | 2000005 | 3 | 24d5c0affa63fcbb689b5d88514141166185aa6582218aa603043874eadaa834 |
| Chronomancer Pirate Captain | 2000007 | 2 | ebe5306c5ab52e7f0dc3dfaff54d4ee33ecfdb7bc8db8feb4590a02fee43d739 |

Repair evidence: `outputs/flash_converter_trial/body_limb_fix/`. Backup: `/opt/infinity/backups/armor-repair-20261012T141840Z`. Public binary hashes, API versions/CRCs, and inventory versions passed. Inventory was preserved, both services are active, and the game retained PID 542558. Publication changes only these trials' versioned bundle metadata and files; old versions remain available. The loader was not rebuilt.

## Batch04 selection

The next batch contains four armors and eight swords. Sources were captured and screened before rendering; source hashes, comparison results, Unity builds, independent audits, and publication results live under `outputs/flash_converter_trial/batch04/`.

Armors: Chrono Vanguard, DragonLord Naval Commander, Resplendant Dragonlord, Obsidian Highlord.

Swords: Skyguard Recruit Blade, Mechanical Sword, BoneCrypt Blade, Galanoth's Bladehaven Blade, Dragon Trophy Blade, ShadowSlice Energy Blade, J-Sixth Blade, Ascended Overfiend Blade of Nulgath.

ChronoZ was held because an animated mask would be ignored. The weapon survey also held missing catalog linkages and an unsupported long loop. Held candidates have no new live trial. All selected armor rigs passed screening without unresolved notes.

## Published batch04

All 12 trials are published as version 1 and granted once, unequipped, to redux. Inventory increased from 208 to 220 rows; existing inventory, equipment, source records, and prior catalogs were preserved. Backup: `/opt/infinity/backups/conversion-batch04-20261012T142519Z`. Both services are active and the game retained PID 542558.

| Trial | Item/bundle ID | Bytes |
|---|---:|---:|
| Chrono Vanguard | 2000013 | 219456 |
| DragonLord Naval Commander | 2000014 | 245592 |
| Resplendant Dragonlord | 2000015 | 235622 |
| Obsidian Highlord | 2000016 | 307887 |
| Skyguard Recruit Blade | 2000017 | 61262 |
| Mechanical Sword | 2000018 | 109358 |
| BoneCrypt Blade | 2000019 | 92233 |
| Galanoth's Bladehaven Blade | 2000020 | 39283 |
| Dragon Trophy Blade | 2000021 | 41894 |
| ShadowSlice Energy Blade | 2000022 | 69432 |
| J-Sixth Blade | 2000023 | 70317 |
| Ascended Overfiend Blade of Nulgath | 2000024 | 207336 |

The batch passed 1,444 GPU captures and independent bundle audits. Worst GPU mean error was 0.195/255; worst sampled Flash-source mean was 15.18/255 on the female DragonLord Naval chest skin region. No source clipping exceeded the existing gate. Mechanical Sword retains a 50-frame loop, ShadowSlice an 18-frame loop, J-Sixth a 2-frame loop, and Ascended Overfiend 150-frame layers. The new armor body artwork is static and uses the native locomotion/combat skeleton.

All public downloads matched their build hashes. Public API metadata, inventory wire, and original records passed readback. The twelve downloads total 1,699,672 bytes. Evidence: `deployment_result.json`, `live_verification.json`, `summary.json`, and each item's `bundle_audit.json` under the batch04 output directory.

Restart the client to load Captain version 2, ChronoStriker version 3, and the new inventory. Check the repaired skin and back limbs in both genders, then preview/equip the four new armors and eight swords. Actual client appearance and combat behavior remain the player-observed proof boundary; no new loader installation is needed.
