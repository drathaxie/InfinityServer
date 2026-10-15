# Battlepet Forge: live Daimyo Commander pilot

October 14, 2026: **shipped live and equipped on Redux**. The original source item 6673 and Dragonslayer class 1932 remain unchanged. The new class uses the existing native Dragonslayer armor rig.

| Resource | Live ID |
|---|---:|
| Daimyo Commander class | 2000000 |
| Class armor item | 2000029 |
| Armored Daimyo Battlepet item | 2000030 |
| Pet AssetBundle | 2000029 |
| Five skills, slots 0–4 | 94297–94301 |

Commands: Pet Strike, Focused Strike, Daimyo Beam, Companion Mend, and Finishing Beam. Cooldowns are 2/4/6.5/10/15 seconds; mana costs are 0/5/15/20/30. Damage multipliers are 0.5/1/1.5/self-heal/2.5. These are pilot mechanics, not a balance claim or a replica of an existing AQW class. Class inventory Quantity is the server's normal maximum class points, 302500; the pet quantity is one.

Remote rollback backup: `/opt/infinity/backups/battlepet-live-20261014T122555Z/`. Exact previous code, catalogs, loader, and Redux database state remain on the host. The additive release preserved previous inventory and original records; only the prior class/pet equipped flags changed. Both services were restarted and verified active.

The separate converted-item grant added **957 missing original native items**, so Redux owns all **960 published converted item records** through native18, in addition to the new class/pet. Existing inventory/equipment remained unchanged. Grant backup: `/opt/infinity/backups/redux-converted-grant-20261014T124435Z/`. Redux alone now has 4096 bag/bank slots, stored in the server-owned `inventory_capacity:8` key; both login presentation and bank-transfer guards use the same capacity helper. Other accounts retain defaults. Three capacity/isolation tests, the existing bank suite, and all 55 combat regressions pass (58 pytest tests total). The resulting generated login wire was 997,092 bytes. Later verified batches perform a separate idempotent cumulative grant; item publication itself still preserves inventory.

The public pet is `npcs/daimyo_battlepet.unity3d`, prefab `ArmoredDaimyo_Battle`, 5,977,740 bytes, SHA256 `ec57e5ecb341e3ed087784cac1b321fc6e57c34aacf6e7a883fa0a15fe1009a8`. Published and installed loader SHA256 is `50da9b5c70e33ca2e7e44671fbb9ae21f1f4dbb5373cde7830b349e34c6bae02`. The previous installed DLL is retained in `outputs/battlepet_forge_lab/live/InfinityLoader.before-9a42a2fe94060a0dceae7e73a80180873912e134a94ddf241b8105fd3d77f0d2.dll`. **Restart the game to load the new DLL.** A running process retains its old assembly.

### Verified release behavior

- Public CDN bytes, API bundle/CRC, published loader/hash, generated inventory/class wires, five skills, original records, and listener 5588 passed.
- An actual authenticated Redux socket login passed with the token kept on the host. It verified equipped class/pet and the five-skill bar. Legacy Forge responses hide the new node/class; `bphello 1` enables the PetAnimation palette and class. The probe disconnects without moving Redux or changing credentials and skips if the account is already online.
- Server-owned equipment, exact bundle/prefab capabilities, living owner/target, room/frame, finite distance and source durations are checked before manual casts, continued casts and every sustained auto tick. Owner remains the caster for damage, aggro and kill credit.
- The loader drives the native follower's commands and preserves movement, hidden-pet settings, prior animator speed, and cancellation. Preview bounds include this marked mesh pet; ordinary item previews/spinner and Caladbolg are unchanged.
- 55 server regression tests and 38 managed lifecycle/source-clock checks pass. The loader builds with zero warnings/errors.

The Armored Daimyo converter retains source-controlled root states, masks/filters and independent head clocks. Idle has a verified 289-frame reset period; the head has 126 frames and survives the looping Walk root according to original instance lifetimes. Source comparisons have worst mean 3.1161/255 under the unchanged 25 limit. The native bundle contains an Animator, mesh layers, atlas and inert TextMesh metadata, with no serialized custom MonoBehaviour. All 31 loaded-bundle GPU captures pass: worst mean 0.4892/255 and p99 8.9392 under limits 2/24. GPU validation correctly compares premultiplied render output.

**Proof boundary:** source and native rendering, backend behavior, public delivery and authenticated socket paths are verified. Actual installed-client following, attack playback and combat appearance still need observation after restart. Damage is applied at admitted cast using authored Immediate nodes; delayed source-impact scheduling and autonomous friendly combat entities are not implemented. The node is extensible, but other pets require independently validated conversions/capabilities before being enabled.

Release scripts: `build_daimyo_pet.py`, `build_pet_bundle.py`, `verify_daimyo_gpu.py`, `battlepet_live_preflight.py`, `prepare_battlepet_release.py`, `deploy_battlepet_live.py`, `battlepet_release_host.py`, `verify_battlepet_live.py`, and `install_battlepet_loader.ps1`, under `scripts/swf2unity/`. Evidence is in `outputs/battlepet_forge_lab/live/`, including deployment result, public/socket verification, local installation, source/period comparisons and bundle/GPU contracts. Preflight exports hashes and public class metadata only; remote backups never export account inventories or credentials.

## Historical experiment notes, October 13

The notes below describe the earlier isolated prototype and its then-remaining work. The live release above supersedes its staged-only status, environment-variable gate, and unimplemented admission/conversion statements.

October 13, 2026. This is an isolated research prototype. No live skill, character, item, client assembly, or installed/published loader was changed for this experiment. Local server/loader source now contains the staged companion node described below.

The installed client loads pets through `PetLoader`, which adds `Walk` and `FollowerGameObject`. The follower switches its Animator between `Idle` and `Walk`, follows the owner, handles facing and obstacles, and fades/repositions when too far away. Its decompiled controller contains no attack-state routing. The source assembly SHA256 is `260c6d53c31d1f6bdc04df3a48ca084d31c1af0b85bd763eb23ada32d6cc4f34`; evidence is under `outputs/battlepet_forge_lab/`.

The live Forge palette has no companion node. Its `Summon` implementation creates hostile monster adds and must not be repurposed as a friendly pet. The client dispatches nodes through the extensible `Node.Execute` dictionary, so a dedicated companion node can be added through the loader without replacing the game assembly. Ordinary pet conversion currently only accepts one invariant root display list shared by Idle/Walk. Battlepet sources have distinct root states and need a state-aware conversion path.

## Source survey

Read-only catalog and mirror inspection found 178 description/name keyword matches with Flash sources present. One explicitly says it is **not** a battle pet. This is a candidate survey, not complete battlepet classification. The first six positive candidates were captured with source hashes and their actual exported scripts:

| Item | ID | Exported root | Labels |
|---|---:|---|---|
| Armored Daimyo Battlepet | 6673 | ArmoredDaimyoBattle | Ready, Idle, Walk, Attack1, Attack2, Buff |
| Skeleton Battle Pet | 8053 | Skeleton | Ready, Idle, Walk, Attack1, Attack2, Buff |
| Necrosian Battle Skullbat | 8926 | NecridBat2 | Ready, Idle, Walk, Attack1, Attack2, PowerUp |
| Necrosian Battle Walker | 8927 | NecridWalker2 | Ready, Idle, Walk, Attack1, Attack2, PowerUp |
| Baby Cellot | 9910 | battleairdragon1 | Ready, Idle, Walk, Attack1, Attack2, Buff |
| Baby Moganth | 9911 | battleearthdragon1 | Ready, Idle, Walk, Attack1, Attack2, Buff |

Armored Daimyo is the first proposed test. Its zero-based labels are Ready=0, Idle=7, Walk=16, Attack1=29, Attack2=59, Buff=78. Exported callbacks start at Idle, stop the Idle root, loop Walk while `onMove`, and return both attacks and Buff to stopped Idle. These callbacks establish animation entry/exit behavior; they do not identify an authoritative damage impact frame. Independently animated children must continue correctly when the root stops.

The source-only GIF and contact sheet show the labeled artwork. They do not prove Unity clips, script execution, independent child timing, or gameplay.

## Forge and combat prototype

`scripts/swf2unity/battlepet_forge_lab.py` forces SQLite before importing the database module, uses a unique temporary database, and creates one library-only skill through the real `sfNewLib` handler. Reopening through `sfInit` preserves its Data/ForgeData. No class slot is assigned.

The graph uses existing nodes: `OnRequest -> Cooldown(2000ms) -> Damage(0.5 multiplier)`, targeting the owner's selected enemy with the `Target` helper. A lab companion wrapper supplies admission checks and a cooldown separate from class HUD slots. It calls the real combat interpreter and records `Attack1` as a visual intent. That intent is an experiment record, **not** a supported Forge node or native network packet. The output Attack packet is not sent to a client, including its experimental Slot=-1.

Twenty-two checks pass: isolated persistence, no class-slot edits, missing/player/unregistered/dead target rejection, room/frame isolation, leash, rejected requests leaving HP/cooldown unchanged, owner attribution, selected-target damage, changed selection, independent cooldown, one lethal kill result, and dismissal on unequip/map/frame change/owner death. Damage randomness is fixed for the fixture so assertions are reproducible; the real target resolution, HP mutation and kill-list code executes. Distance and room inputs are fixtures; production must obtain them from server-owned state.

## Bridge still required

### Five-slot class prototype and staged bridge

`scripts/swf2unity/battlepet_class_lab.py` now creates **Daimyo Commander Lab** in a unique SQLite database. Its class ID is a fixture ID, not a proposed live catalog allocation. All five skills use the real `sfNew`, reopen through `sfInit`, and produce a complete `sEAct` skill bar:

| Slot | Command | Pet state | Cooldown | Experimental effect |
|---|---|---|---:|---|
| 0 | Pet Strike | Attack1 | 2 s | 0.5 multiplier; sustained owner auto |
| 1 | Focused Strike | Attack1 | 4 s | 1.0 multiplier |
| 2 | Daimyo Beam | Attack2 | 6.5 s | 1.5 multiplier |
| 3 | Companion Mend | Buff | 10 s | Heal owner |
| 4 | Finishing Beam | Attack2 | 15 s | 2.5 multiplier |

These are test mechanics, not a balanced release or a replica of an existing AQW class. The source FPS is 24; duration windows follow the compiled return callbacks: Attack1=29 ticks, Attack2=18, Buff=23. The class prototype passed **65 checks** for persistence, callback duration binding, HUD payload, owner casting, target damage, self healing, renderer parity, cooldowns, sustained auto, cancellation and invalid admission/commands. The admission capabilities are source fixtures, not yet validated native pet capabilities. Its manifest deliberately has no equipable armor rig or live catalog allocation.

`PetAnimation` is now recognized by both local server interpreters, with validated state/duration/speed. The proposed node palette exists only in the exported lab `class_forge_init.json`; the default palette is unchanged. Ordinary graphs retain their previous wire shape. Legacy Damage rendering now preserves an explicitly authored `Immediate` flag, matching the data interpreter and native client. The pilot's Damage/Heal nodes are Immediate because native queued damage normally waits for a **player** animation hit cue. Pet impact timing still needs its own server-authoritative scheduling and client synchronization; the prototype does not pretend to provide that.

`mod/InfinityLoader/BattlepetForge.cs` implements the staged visual bridge. It registers `PetAnimation` in `Node.Execute`, plays a supported state on the owner's equipped follower, prevents follower Idle/Walk animation switches from overwriting the command, restores the prior speed and current movement state on completion, and cancels on owner death/frame change/pet replacement/disable. Hidden pets stay hidden; pets lacking attack states are left alone. Following movement continues. This bridge does not create damage, equipment, a second combat entity, or a summon.

Installation is gated by `INFINITY_BATTLEPET_LAB=1` plus the existing private-server marker. The loader build against the installed client passed with **zero warnings/errors** and is staged only at `outputs/battlepet_forge_lab/loader/InfinityLoader.dll`. It was not installed or published. Managed lifecycle doubles passed **30 checks**, including independent owners and movement restoration; these are not Unity runtime tests. The server companion contracts plus existing combat/runtime regressions passed **53 tests**.

The new `scripts/swf2unity/battlepet_states.py` compiles the explicitly supported root-script subset (stop, label jumps, and the `onMove` Walk loop) and simulates it without executing arbitrary AS3. All six roots passed **36 checks** covering stopped Idle, bounded movement loops, command return behavior and rejection of injected unsupported statements. It found a concrete source edge case: Baby Cellot and Baby Moganth's Attack2/Buff entries immediately redirect to Idle under the exported callbacks. The compiler records this behavior instead of assigning a made-up clip duration. This is root control-flow proof, not child animation/native conversion proof.

### Remaining work for an equipable in-game pilot

1. Compile actual labeled pet states with script-controlled stop/loop/return behavior, masks, color customization and independent child clocks; validate source and loaded Unity output.
2. Bind server-owned equipment/native capability admission to manual casts, resumed handshakes and every sustained auto tick. The pure admission validator already exists in `server/battlepets.py`; it is not yet connected to live handlers. Recheck unequip and room changes before any pending hit. Negotiate loader capability before exposing the new palette/node to a client.
3. Allocate an isolated trial class item/armor rig and validated pet bundle, preserving existing records. Test the staged bridge and authored class through the actual client before enabling the class. Keep damage, aggro, rewards and kill credit attributed to the owner. The proposed class commands its pet; this does not grant every cosmetic pet autonomous damage.
4. Add authoritative pet impact timing, then test two owners, repeated hits, enemy death, map changes, disconnect/reconnect, unequip, owner death and hidden-pet settings through the real client. No autonomous pet AI, rewards, network synchronization or rendered attack proof exists yet.

Armored Daimyo's stopped Idle contains a 289-frame nested timeline; another source child has 126 frames. Flattening every state into a common loop can balloon clock periods. State conversion must preserve the relevant independent child lifetimes rather than treating the 102-frame root as one ordinary looping weapon.

## Reproduce and evidence

Run from the repository root with Python and the existing JPEXS/Java setup:

```powershell
C:/Python314/python.exe scripts/swf2unity/inspect_battlepets.py
C:/Python314/python.exe scripts/swf2unity/battlepet_forge_lab.py
C:/Python314/python.exe scripts/swf2unity/battlepet_states.py
C:/Python314/python.exe scripts/swf2unity/battlepet_class_lab.py
C:/Python314/python.exe scripts/swf2unity/preview_battlepet.py
dotnet run --project mod/BattlepetForgeChecks/BattlepetForgeChecks.csproj -c Release
dotnet build mod/InfinityLoader/InfinityLoader.csproj -c Release -o outputs/battlepet_forge_lab/loader --no-restore
C:/Python314/python.exe -m pytest server/test_battlepets.py server/test_combat_runtime.py server/test_review_regressions.py -q
```

The survey reads the remote catalog/mirror over the existing SSH helper; the Forge experiments are entirely local. Evidence: `source_survey.json`, `sources/`, `scripts/`, `client_contract.json`, decompiled follower/loader/NodeDamage dispatch sources, `forge_fixture.json`, `validation.json`, `state_programs.json`, `class_forge_init.json`, `class_seact.json`, `class_validation.json`, staged loader, and source preview files in `outputs/battlepet_forge_lab/`.
