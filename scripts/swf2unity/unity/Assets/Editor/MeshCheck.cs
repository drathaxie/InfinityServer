using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

// Reproduces the client's in-game sprite creation for converted armors:
//   Sprite.Create(destTex, original.rect, original.pivot / original.rect.size, original.pixelsPerUnit)
// (CustomizableCharacter.CreateFromOriginal, default Tight mesh) on a composited texture, then measures how much
// of each part's footprint (every pixel with alpha > 0, including the faint coverage alpha that marks where
// animated layers can move) lies outside the generated mesh.
//   Unity.exe -batchmode -quit -projectPath <proj> -executeMethod MeshCheck.Run -meshInput <json> -meshOutput <json>
public static class MeshCheck
{
    [Serializable] class Input { public string texture; public Part[] parts; }
    [Serializable] class Part { public string name; public float x, y, w, h, px, py, ppu; }
    [Serializable] class Result { public string name; public int footprint; public int outside; public int vertices; }
    [Serializable] class Output { public Result[] results; }

    public static void Run()
    {
        try
        {
            var input = JsonUtility.FromJson<Input>(File.ReadAllText(Arg("-meshInput")));
            var tex = new Texture2D(2, 2, TextureFormat.ARGB32, true, false);
            tex.LoadImage(File.ReadAllBytes(input.texture));
            var results = new List<Result>();
            foreach (var p in input.parts)
            {
                var rect = new Rect(p.x, p.y, p.w, p.h);
                var sprite = Sprite.Create(tex, rect, new Vector2(p.px, p.py), p.ppu);  // the client's overload
                var verts = sprite.vertices;
                var tris = sprite.triangles;
                int w = (int)p.w, h = (int)p.h;
                var inside = new bool[w * h];
                // vertices are in sprite-local units: pixel = vertex * ppu + pivot * size
                var pts = new Vector2[verts.Length];
                for (int i = 0; i < verts.Length; i++)
                    pts[i] = new Vector2(verts[i].x * p.ppu + p.px * w, verts[i].y * p.ppu + p.py * h);
                for (int t = 0; t < tris.Length; t += 3)
                {
                    Vector2 a = pts[tris[t]], b = pts[tris[t + 1]], c = pts[tris[t + 2]];
                    int x0 = Mathf.Max(0, Mathf.FloorToInt(Mathf.Min(a.x, Mathf.Min(b.x, c.x))));
                    int x1 = Mathf.Min(w - 1, Mathf.CeilToInt(Mathf.Max(a.x, Mathf.Max(b.x, c.x))));
                    int y0 = Mathf.Max(0, Mathf.FloorToInt(Mathf.Min(a.y, Mathf.Min(b.y, c.y))));
                    int y1 = Mathf.Min(h - 1, Mathf.CeilToInt(Mathf.Max(a.y, Mathf.Max(b.y, c.y))));
                    for (int y = y0; y <= y1; y++)
                        for (int x = x0; x <= x1; x++)
                            if (InTri(new Vector2(x + 0.5f, y + 0.5f), a, b, c)) inside[y * w + x] = true;
                }
                var px = tex.GetPixels32();
                int foot = 0, outside = 0;
                for (int y = 0; y < h; y++)
                    for (int x = 0; x < w; x++)
                    {
                        var c = px[((int)p.y + y) * tex.width + (int)p.x + x];
                        if (c.a == 0) continue;
                        foot++;
                        if (!inside[y * w + x]) outside++;
                    }
                results.Add(new Result { name = p.name, footprint = foot, outside = outside, vertices = verts.Length });
            }
            File.WriteAllText(Arg("-meshOutput"), JsonUtility.ToJson(new Output { results = results.ToArray() }, true));
            Debug.Log("MESH_CHECK_DONE");
            EditorApplication.Exit(0);
        }
        catch (Exception e)
        {
            Debug.LogError("MESH_CHECK_FAILED " + e);
            EditorApplication.Exit(1);
        }
    }

    static bool InTri(Vector2 p, Vector2 a, Vector2 b, Vector2 c)
    {
        float d1 = (p.x - b.x) * (a.y - b.y) - (a.x - b.x) * (p.y - b.y);
        float d2 = (p.x - c.x) * (b.y - c.y) - (b.x - c.x) * (p.y - c.y);
        float d3 = (p.x - a.x) * (c.y - a.y) - (c.x - a.x) * (p.y - a.y);
        bool neg = d1 < 0 || d2 < 0 || d3 < 0, pos = d1 > 0 || d2 > 0 || d3 > 0;
        return !(neg && pos);
    }

    static string Arg(string name)
    {
        var args = Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1];
        throw new Exception("missing " + name);
    }
}
