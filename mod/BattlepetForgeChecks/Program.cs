using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEngine;

int passed = 0;
void Check(string name, bool condition)
{
    if (!condition) throw new Exception(name);
    passed++;
}
object Call(string method, object instance, params object[] args) =>
    typeof(BattlepetForge).GetMethod(method, BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance)!.Invoke(instance, args);
(Entity owner, FollowerGameObject follower, Animator animator) Pet(params string[] states)
{
    var go = new GameObject();
    var follower = go.AddComponent<FollowerGameObject>();
    var animator = go.AddComponent<Animator>();
    foreach (var state in states) animator.states.Add(Animator.StringToHash(state));
    return (new Entity { petGO = go }, follower, animator);
}
JObject Command(string state = "Attack1", float duration = 1, float speed = 1) =>
    JObject.FromObject(new { Animation = state, Duration = duration, Speed = speed });
void Tick(Entity owner, float seconds)
{
    Time.deltaTime = seconds;
    Call("Update", owner.petGO.GetComponent<BattlepetForge>());
}
bool Allows(FollowerGameObject follower) => (bool)Call("AllowFollowAnimation", null, follower);

var harmony = new HarmonyLib.Harmony();
BattlepetForge.Install(harmony);
Check("node registered", Node.Execute.ContainsKey("PetAnimation") && harmony.patches == 1);
try { BattlepetForge.Install(harmony); throw new Exception("duplicate accepted"); }
catch (InvalidOperationException) { Check("duplicate does not patch twice", harmony.patches == 1); }
foreach (var state in new[] { "Attack1", "Attack2", "Buff", "PowerUp" })
    Check("known command", BattlepetForge.ValidCommand(state, 1, 1));
foreach (var state in new[] { "Idle", "Walk", "", "Die", "Attack1,Attack2" })
    Check("unknown command", !BattlepetForge.ValidCommand(state, 1, 1));
Check("NaN rejected", !BattlepetForge.ValidCommand("Attack1", float.NaN, 1));
Check("infinity rejected", !BattlepetForge.ValidCommand("Attack1", 1, float.PositiveInfinity));
Check("zero speed rejected", !BattlepetForge.ValidCommand("Attack1", 1, 0));
Check("duration bounded", !BattlepetForge.ValidCommand("Attack1", 11, 1));

BattlepetForge.Execute(new Entity(), 1, Command());
Check("absent pet harmless", true);
var idleOnly = Pet("Idle", "Walk");
BattlepetForge.Execute(idleOnly.owner, 1, Command());
Check("ordinary pet left alone", idleOnly.owner.petGO.GetComponent<BattlepetForge>() == null && Allows(idleOnly.follower));
var first = Pet("Idle", "Walk", "Attack1", "Attack2", "Buff");
first.animator.speed = .75f;
Check("ordinary follower runs", Allows(first.follower));
BattlepetForge.Execute(first.owner, 1, Command(speed: 2));
Check("attack plays at requested speed", first.animator.played == "Attack1" && first.animator.speed == 2);
Check("walking cannot overwrite attack", !Allows(first.follower));
Tick(first.owner, .25f);
Check("speed scales duration", !Allows(first.follower));
Tick(first.owner, .25f);
Check("idle restored", first.animator.played == "Idle" && first.animator.speed == .75f && Allows(first.follower));
first.follower.walk.isWalking = true;
BattlepetForge.Execute(first.owner, 2, Command("Attack2"));
Tick(first.owner, 1);
Check("walk restored if moving", first.animator.played == "Walk" && first.follower.isWalking);

var second = Pet("Idle", "Walk", "Attack1");
BattlepetForge.Execute(first.owner, 3, Command());
BattlepetForge.Execute(second.owner, 4, Command());
first.owner.Frame = "Other";
Tick(first.owner, .1f);
Check("frame change cancels first pet only", Allows(first.follower) && !Allows(second.follower));
second.owner.hasDied = true;
Tick(second.owner, .1f);
Check("owner death cancels", Allows(second.follower));
second.owner.hasDied = false;
BattlepetForge.Execute(second.owner, 5, Command());
Call("OnDisable", second.owner.petGO.GetComponent<BattlepetForge>());
Check("disable restores speed and follower", Allows(second.follower) && second.animator.speed == 1);
second.owner.petGO.activeInHierarchy = false;
BattlepetForge.Execute(second.owner, 6, Command());
Check("hidden pet stays hidden", Allows(second.follower) && !second.owner.petGO.activeInHierarchy);
second.owner.petGO.activeInHierarchy = true;
BattlepetForge.Execute(second.owner, 7, Command());
var oldPet = second.owner.petGO;
second.owner.petGO = new GameObject();
Time.deltaTime = .1f;
Call("Update", oldPet.GetComponent<BattlepetForge>());
Check("replaced pet is released", Allows(second.follower));
first.owner.Frame = "Enter";
BattlepetForge.Execute(first.owner, 8, Command());
Tick(first.owner, .8f);
BattlepetForge.Execute(first.owner, 9, Command("Attack2"));
Tick(first.owner, .4f);
Check("new command replaces old timer", !Allows(first.follower) && first.animator.played == "Attack2");
Tick(first.owner, .6f);
Check("replacement command completes", Allows(first.follower));
Console.WriteLine($"Battlepet managed lifecycle: {passed} checks passed; no Unity runtime proof.");
var petData = Newtonsoft.Json.JsonConvert.DeserializeObject<FlashPetData>(File.ReadAllText("outputs/battlepet_forge_lab/live/pet/pet.json"));
var clock = new FlashPetPlayback(petData);
clock.Advance(289d/24,false);
Check("source Idle wraps at validated reset", clock.State=="Idle" && clock.Frame==0);
clock.Enter("Walk");clock.Advance(12d/24,true);
Check("walking root loops independently",clock.Frame==0 && clock.HeadAge==12);
clock.Enter("Attack1");
Check("retained source head instance keeps its clock",clock.HeadAge==12);
clock.Advance(3d/24,false);
Check("source expression replacement hides old head",clock.Head<0);
clock.Advance(24d/24,false);
Check("new source head instance starts fresh",clock.Frame==27 && clock.HeadAge==0);
clock.Advance(2d/24,false);
Check("attack returns to following",clock.State=="Idle");
clock.Enter("Walk");clock.Advance(251d/24,true);
Check("late independent child phase",clock.Frame==11 && clock.HeadAge==125);
clock.Speed=2;clock.Enter("Attack2");clock.Advance(18d/48,true);
Check("speed-scaled source callback returns to Walk",clock.State=="Walk");
Console.WriteLine($"Battlepet bridge and source clocks: {passed} checks passed.");
