using System;
using HarmonyLib;
using Newtonsoft.Json;
using UnityEngine;

internal sealed class FlashBattlePet : MonoBehaviour
{
    private FlashPetPlayback clock;
    private MeshRenderer[] renderers;
    private Mesh[] meshes;
    private Walk walk;
    internal static void Install(Harmony harmony)
    {
        var setup = AccessTools.DeclaredMethod(typeof(HumanoidAvatar), "setupPet");
        var bounds = AccessTools.DeclaredMethod(typeof(PreviewSpriteImage), "CalculateLocalBounds");
        if (setup == null || bounds == null) throw new MissingMemberException("Pet setup/preview contract changed");
        harmony.Patch(setup, postfix: new HarmonyMethod(typeof(FlashBattlePet), nameof(AfterSetup)));
        harmony.Patch(bounds, prefix: new HarmonyMethod(typeof(FlashBattlePet), nameof(PreviewBounds)));
    }
    private static bool PreviewBounds(GameObject go, PreviewSpriteImage __instance, ref Bounds __result)
    {
        if (go == null || __instance.spriteParent == null) return true;
        bool marker = false;
        foreach (var text in go.GetComponentsInChildren<TextMesh>(true))
            if (text.gameObject.name == "FlashPetData") { marker = true; break; }
        if (!marker) return true;
        bool found = false; var result = new Bounds();
        foreach (var renderer in go.GetComponentsInChildren<MeshRenderer>(true))
        {
            if (!renderer.enabled || renderer.GetComponent<MeshFilter>() == null) continue;
            var bounds = renderer.bounds;
            foreach (var point in new[] {new Vector3(bounds.min.x,bounds.min.y,bounds.center.z),
                new Vector3(bounds.max.x,bounds.min.y,bounds.center.z),new Vector3(bounds.max.x,bounds.max.y,bounds.center.z),
                new Vector3(bounds.min.x,bounds.max.y,bounds.center.z)})
            {
                var local = __instance.spriteParent.transform.InverseTransformPoint(point);
                if (!found) { result = new Bounds(local,Vector3.zero); found = true; }
                else result.Encapsulate(local);
            }
        }
        if (!found) return true;
        __result = result; return false;
    }
    private static void AfterSetup(GameObject __0) => Attach(__0);
    internal static void Attach(GameObject root)
    {
        if (root == null) return;
        foreach (var text in root.GetComponentsInChildren<TextMesh>(true))
        {
            if (text.gameObject.name != "FlashPetData" || text.transform.parent == null) continue;
            var pet = text.transform.parent.gameObject;
            if (pet.GetComponent<FlashBattlePet>() != null) continue;
            try
            {
                var data = JsonConvert.DeserializeObject<FlashPetData>(text.text);
                var actor = pet.AddComponent<FlashBattlePet>();
                actor.clock = new FlashPetPlayback(data);
                actor.walk = pet.GetComponent<Walk>();
                actor.renderers = new MeshRenderer[3]; actor.meshes = new Mesh[3];
                var names = new[] { "Body", "Head", "Front" };
                for (int i = 0; i < 3; i++)
                {
                    var node = pet.transform.Find(names[i]);
                    actor.renderers[i] = node.GetComponent<MeshRenderer>();
                    actor.meshes[i] = node.GetComponent<MeshFilter>().mesh;
                    actor.meshes[i].MarkDynamic();
                }
                actor.Draw();
                Debug.Log("[battlepet] Flash source actor attached: " + data.name);
            }
            catch (Exception error) { Debug.LogError("[battlepet] Invalid source actor: " + error); }
        }
    }
    internal void Command(string state, float speed) { clock.Speed = speed; clock.Enter(state); Draw(); }
    internal void Follow() { clock.Enter(walk != null && walk.isWalking ? "Walk" : "Idle"); Draw(); }
    private void Update()
    {
        if (clock == null || renderers == null) return;
        clock.Advance(Time.deltaTime, walk != null && walk.isWalking); Draw();
    }
    private void Draw()
    {
        DrawMesh(0, clock.Back, null); DrawMesh(1, clock.Head, clock.HeadMatrix); DrawMesh(2, clock.Front, null);
    }
    private void DrawMesh(int layer, int index, float[] matrix)
    {
        var renderer = renderers[layer]; renderer.enabled = index >= 0;
        if (index < 0) return;
        var s = clock.Data.sprites[index]; var r = s.rect;
        float x = s.origin[0], y = s.origin[1], w = r[2] * s.tpp, h = r[3] * s.tpp;
        var points = new[] { new Vector2(x,y), new Vector2(x+w,y), new Vector2(x+w,y+h), new Vector2(x,y+h) };
        var vertices = new Vector3[4];
        for (int i = 0; i < 4; i++)
        {
            var p = points[i];
            if (matrix != null) p = new Vector2(matrix[0]*p.x+matrix[2]*p.y+matrix[4], matrix[1]*p.x+matrix[3]*p.y+matrix[5]);
            vertices[i] = new Vector3(p.x * clock.Data.twipToUnit, -p.y * clock.Data.twipToUnit, 0);
        }
        float u = r[0]/clock.Data.atlasWidth, v = r[1]/clock.Data.atlasHeight;
        float uw = r[2]/clock.Data.atlasWidth, vh = r[3]/clock.Data.atlasHeight;
        var mesh = meshes[layer]; mesh.vertices = vertices;
        mesh.uv = new[] { new Vector2(u,v+vh), new Vector2(u+uw,v+vh), new Vector2(u+uw,v), new Vector2(u,v) };
        mesh.triangles = new[] {0,1,2,0,2,3}; mesh.colors = new[] {Color.white,Color.white,Color.white,Color.white};
        mesh.RecalculateBounds();
    }
    private void OnDestroy() { if (meshes != null) foreach (var mesh in meshes) if (mesh != null) Destroy(mesh); }
}
