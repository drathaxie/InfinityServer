// Managed lifecycle doubles. Native binding/build proof is a separate loader build.
using System.Reflection;
using Newtonsoft.Json.Linq;

namespace UnityEngine
{
    public class GameObject
    {
        readonly Dictionary<Type, object> components = new();
        public bool activeInHierarchy = true;
        public T GetComponent<T>() where T : class => components.GetValueOrDefault(typeof(T)) as T;
        public T GetComponentInChildren<T>() where T : class => GetComponent<T>();
        public T AddComponent<T>() where T : MonoBehaviour, new()
        {
            var value = new T { gameObject = this };
            components[typeof(T)] = value;
            return value;
        }
    }
    public class MonoBehaviour
    {
        public GameObject gameObject;
        public T GetComponent<T>() where T : class => gameObject.GetComponent<T>();
    }
    public class Animator : MonoBehaviour
    {
        public object runtimeAnimatorController = new();
        public float speed = 1;
        public string played;
        public HashSet<int> states = new();
        public static int StringToHash(string name) => name.GetHashCode();
        public bool HasState(int layer, int hash) => states.Contains(hash);
        public void Play(string state, int layer, float start) => played = state;
    }
    public static class Time { public static float deltaTime; }
    public static class Debug { public static void LogWarning(string text) { } }
}
namespace HarmonyLib
{
    public class Harmony
    {
        public int patches;
        public void Patch(MethodInfo method, HarmonyMethod prefix) { patches++; }
    }
    public class HarmonyMethod { public HarmonyMethod(Type type, string name) { } }
    public static class AccessTools
    {
        public static MethodInfo DeclaredMethod(Type type, string name) => type.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance);
    }
}
public class Walk { public bool isWalking; }
public class FollowerGameObject : UnityEngine.MonoBehaviour
{
    public Walk walk = new();
    public bool isWalking;
    private void UpdateWalkAnimation() { }
}
public class Entity
{
    public bool hasDied;
    public bool sameFrame = true;
    public string Frame = "Enter";
    public UnityEngine.GameObject petGO;
    public bool inSameFrame() => sameFrame;
}
public static class Node
{
    public static Dictionary<string, Action<Entity, int, JObject>> Execute = new();
}
public class FlashBattlePet : UnityEngine.MonoBehaviour { internal void Command(string state,float speed) { } internal void Follow() { } }
public class RequestCmd { public RequestCmd(string cmd,string[] args) { } }
public class AEC { public static AEC Instance = new(); public void sendRequest(RequestCmd request) { } }
