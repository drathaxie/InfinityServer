// Contract doubles for lifecycle checks; these do not reproduce Unity's rendering.
using UnityEngine;
namespace HarmonyLib
{
    public class HarmonyMethod { public HarmonyMethod(Type t, string name) { } }
    public class Harmony { public void Patch(object method, HarmonyMethod prefix = null, HarmonyMethod postfix = null) { } }
    public static class AccessTools
    {
        public static System.Reflection.MethodInfo DeclaredMethod(Type t, string n) =>
            t.GetMethod(n, System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance);
        public static object Field(Type t, string n) => new object();
    }
}
namespace UnityEngine
{
    public class Object
    {
        public bool destroyed;
        public static void Destroy(Object o) { if (o != null) o.destroyed = true; }
    }
    public class GameObject : Object
    {
        readonly List<Component> components = new();
        public T AddComponent<T>() where T : Component, new()
        { var c = new T { gameObject = this }; components.Add(c); return c; }
        public T GetComponent<T>() where T : class => components.OfType<T>().FirstOrDefault();
    }
    public class Component : Object
    {
        public GameObject gameObject;
        public T GetComponent<T>() where T : class => gameObject.GetComponent<T>();
    }
    public class MonoBehaviour : Component { public void StopAllCoroutines() { } }
    public class Transform : Component { }
    public class Shader : Object { public string name; }
    public class Material : Object { public Shader shader; }
    public class SpriteRenderer : Component { public Material sharedMaterial; public Sprite sprite; }
    public struct Vector2
    {
        public float x, y;
        public static Vector2 operator /(Vector2 a, Vector2 b) => new() { x = a.x / b.x, y = a.y / b.y };
    }
    public struct Rect { public Vector2 size; }
    public enum TextureFormat { ARGB32 }
    public enum FilterMode { Point, Bilinear }
    public enum SpriteMeshType { FullRect, Tight }
    public class Texture2D : Object
    {
        public int width, height;
        public FilterMode filterMode;
        public Texture2D(int w, int h, TextureFormat f, bool mip, bool linear) { width = w; height = h; }
    }
    public class Sprite : Object
    {
        public Texture2D texture;
        public Rect rect;
        public Rect textureRect => rect;
        public Vector2 pivot;
        public float pixelsPerUnit;
        public SpriteMeshType mesh;
        public static Sprite Create(Texture2D t, Rect r, Vector2 p, float ppu, uint e, SpriteMeshType mesh)
            => new() { texture = t, rect = r, pivot = p, pixelsPerUnit = ppu, mesh = mesh };
    }
}
public class CustomizableCharacter : MonoBehaviour
{
    public enum Slot { chest, backhand }
    public bool SkipComposite;
    public Action OnSetupComplete;
    public EntityArmorColor ArmorColor = new();
}
public class CustomizableSlot : MonoBehaviour
{
    public CustomizableCharacter.Slot slot;
    public SpriteRenderer spriteRenderer => GetComponent<SpriteRenderer>();
}
public class SpriteSlot
{
    public CustomizableCharacter.Slot slot;
    public Sprite sprite;
    public Texture2D CCMask;
}
public class ItemSlots : MonoBehaviour { public List<SpriteSlot> slots = new(); }
public class EntityArmorColor { public int Base; }
public class CompositorTask { }
public class CompositorTaskArmorColorMask : CompositorTask
{
    public readonly int color;
    public CompositorTaskArmorColorMask(Rect r, Texture2D mask, Texture2D diffuse, EntityArmorColor c) { color = c.Base; }
}
public class CompositorTaskSprite : CompositorTask { public CompositorTaskSprite(Rect r, Texture2D t) { } }
public class CompositorRequest
{
    public static readonly Queue<CompositorRequest> pending = new();
    public List<CompositorTask> tasks = new();
    public Texture2D target;
    public event Action Complete;
    public CompositorRequest(Texture2D t) { target = t; }
    public void Composite() => pending.Enqueue(this);
    public void Finish() => Complete?.Invoke();
}
public static class InfinityLoaderMod
{
    public static void SafeLog(string message) => throw new Exception(message);
}
