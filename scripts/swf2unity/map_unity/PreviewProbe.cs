using System;
using System.Collections.Generic;
using UnityEngine;

// Local prototype adapter only. Never attached to the exported map prefab.
public class PreviewProbe : MonoBehaviour
{
    public GameObject map;
    public Camera view;
    public string currentCell = "Enter", status = "";
    public Transform marker;
    public float speed = 8;
    private Queue<Vector2> route = new Queue<Vector2>();
    private float transitionCooldown;

    public bool CanStand(Vector2 point)
    {
        var cell = map.transform.Find(currentCell);
        bool ground = false;
        foreach (var collider in cell.GetComponentsInChildren<PolygonCollider2D>())
        {
            if (collider.GetComponent<MapBlocker>() != null && collider.OverlapPoint(point)) return false;
            if (collider.GetComponent<ClickableWalk>() != null && collider.OverlapPoint(point)) ground = true;
        }
        return ground;
    }

    public void Enter(string cell, string pad)
    {
        route.Clear();
        foreach (Transform child in map.transform) child.gameObject.SetActive(child.name == cell);
        currentCell = cell;
        var target = map.transform.Find(cell + "/Spawns/" + pad);
        if (target == null) throw new Exception("Missing spawn " + cell + "/" + pad);
        marker.position = new Vector3(target.position.x, target.position.y, -2);
        transitionCooldown = 0.5f;
        Physics2D.SyncTransforms();
        if (!CanStand(marker.position)) throw new Exception("Spawn is blocked " + cell + "/" + pad);
    }

    public bool TryTransition()
    {
        foreach (var exit in map.transform.Find(currentCell).GetComponentsInChildren<MapGoToCell>())
            foreach (var collider in exit.GetComponents<PolygonCollider2D>())
                if (collider.OverlapPoint(marker.position)) { Enter(exit.TargetCell, exit.TargetSpawnPad); return true; }
        return false;
    }

    // Fine grid is only a probe convenience; this is not the game's pathfinder.
    public List<Vector2> FindPath(Vector2 start, Vector2 end)
    {
        const float step = 0.2f;
        Func<Vector2, Vector2Int> key = p => new Vector2Int(Mathf.RoundToInt(p.x/step), Mathf.RoundToInt(p.y/step));
        var first = key(start); var last = key(end);
        var previous = new Dictionary<Vector2Int, Vector2Int>();
        var todo = new Queue<Vector2Int>(); todo.Enqueue(first); previous[first] = first;
        var directions = new[] { Vector2Int.up, Vector2Int.down, Vector2Int.left, Vector2Int.right };
        while (todo.Count > 0 && previous.Count < 100000)
        {
            var node = todo.Dequeue();
            if (node == last)
            {
                var result = new List<Vector2> { end };
                while (node != first) { result.Add(new Vector2(node.x*step, node.y*step)); node = previous[node]; }
                result.Add(start); result.Reverse(); return result;
            }
            foreach (var dir in directions)
            {
                var next = node + dir;
                var point = new Vector2(next.x*step, next.y*step);
                if (previous.ContainsKey(next) || !CanStand(point)) continue;
                // Sweep between nodes so a narrow source blocker cannot be jumped.
                if (!CanStand((point + new Vector2(node.x*step, node.y*step))*0.5f)) continue;
                previous[next] = node; todo.Enqueue(next);
            }
        }
        return new List<Vector2>();
    }

    private void Start() { Enter("Enter", "Spawn"); }
    private void Update()
    {
        transitionCooldown = Mathf.Max(0, transitionCooldown-Time.deltaTime);
        if (Input.GetMouseButtonDown(0))
        {
            var point = (Vector2)view.ScreenToWorldPoint(Input.mousePosition);
            var path = CanStand(point) ? FindPath(marker.position, point) : new List<Vector2>();
            route = new Queue<Vector2>(path);
            status = path.Count == 0 ? "Outside reachable walking ground" : "";
        }
        var delta = new Vector2(Input.GetAxisRaw("Horizontal"), Input.GetAxisRaw("Vertical"));
        if (delta.sqrMagnitude > 0)
        {
            route.Clear();
            var next = (Vector2)marker.position + delta.normalized * Mathf.Min(Time.deltaTime, 0.04f)*speed;
            bool valid = true;
            for (int i=1; i<=8; i++) if (!CanStand(Vector2.Lerp(marker.position, next, i/8f))) valid = false;
            if (valid) marker.position = new Vector3(next.x,next.y,-2);
        }
        else if (route.Count > 0)
        {
            var next = Vector2.MoveTowards(marker.position,route.Peek(),speed*Mathf.Min(Time.deltaTime,0.04f));
            if (CanStand(next)) marker.position = new Vector3(next.x,next.y,-2);
            else route.Clear();
            if (route.Count > 0 && Vector2.Distance(marker.position,route.Peek()) < 0.01f) route.Dequeue();
        }
        if (transitionCooldown <= 0) TryTransition();
    }

    private void OnGUI()
    {
        GUI.Box(new Rect(8,8,650,50), "Shadowfall structural prototype — " + currentCell + "\nClick ground or use arrows/WASD. Yellow marker is the navigation probe.");
        GUI.Box(new Rect(8,Screen.height-52,760,44), "Source snapshots: animation, NPC interactions and quest logic remain pending.\n" + status);
    }
}
