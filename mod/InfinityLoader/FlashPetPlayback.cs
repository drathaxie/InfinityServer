using System;
using System.Collections.Generic;

// Shared by the native bundle validator and the shipped runtime. No Unity calls.
[Serializable] public sealed class FlashPetData
{
    public string name, sourceSha256;
    public int fps, atlasWidth, atlasHeight, idlePeriod, headPeriod;
    public float twipToUnit;
    public FlashPetSprite[] sprites;
    public FlashPetGroup[] groups;
    public FlashPetState[] states;
}
[Serializable] public sealed class FlashPetSprite { public float[] rect, origin; public float tpp; }
[Serializable] public sealed class FlashPetGroup { public string name; public int[] indices; }
[Serializable] public sealed class FlashPetMatrix { public float[] v; }
[Serializable] public sealed class FlashPetState
{
    public string name; public int frames;
    public FlashPetMatrix[] headMatrices;
    public int[] headChars, headTokens;
}
public sealed class FlashPetPlayback
{
    public readonly FlashPetData Data;
    private readonly Dictionary<string, int[]> groups = new Dictionary<string, int[]>();
    private readonly Dictionary<string, FlashPetState> states = new Dictionary<string, FlashPetState>();
    public string State { get; private set; } = "Idle";
    public int Frame { get; private set; }
    public int HeadAge { get; private set; }
    private int headChar = -1, headToken = -1;
    private double phase;
    public float Speed = 1;
    public bool IsCombat => State != "Idle" && State != "Walk";
    public FlashPetPlayback(FlashPetData data)
    {
        if (data == null || data.fps != 24 || data.sprites == null || data.groups == null || data.states == null)
            throw new ArgumentException("Invalid native Flash pet data");
        Data = data;
        foreach (var g in data.groups) groups.Add(g.name, g.indices);
        foreach (var s in data.states) states.Add(s.name, s);
        if (groups["Idle"].Length != data.idlePeriod || groups["Head33"].Length != data.headPeriod)
            throw new ArgumentException("Pet clock contract mismatch");
    }
    public void Enter(string state)
    {
        if (state != "Idle" && !states.ContainsKey(state)) throw new ArgumentException("Unsupported pet state");
        State = state; Frame = 0; phase = 0; ReconcileHead(false);
    }
    private void ReconcileHead(bool tick)
    {
        int cid = -1, token = -1;
        if (State != "Idle") { cid = states[State].headChars[Frame]; token = states[State].headTokens[Frame]; }
        if (cid != headChar || token != headToken) HeadAge = 0;
        else if (tick && cid >= 0) HeadAge = (HeadAge + 1) % Data.headPeriod;
        headChar = cid; headToken = token;
    }
    public void Advance(double seconds, bool walking)
    {
        if (seconds < 0 || double.IsNaN(seconds) || double.IsInfinity(seconds)) throw new ArgumentException("Invalid pet time");
        if (!IsCombat && State != (walking ? "Walk" : "Idle")) Enter(walking ? "Walk" : "Idle");
        phase += seconds * Data.fps * (IsCombat ? Speed : 1);
        while (phase >= 1)
        {
            phase -= 1; Frame++;
            int length = State == "Idle" ? Data.idlePeriod : states[State].frames;
            if (Frame >= length)
            {
                if (IsCombat) { Enter(walking ? "Walk" : "Idle"); continue; }
                Frame = 0;
            }
            ReconcileHead(true);
        }
    }
    public int Back => State == "Idle" ? groups["Idle"][Frame] : groups[State + "_back"][Frame];
    public int Front => State == "Idle" ? -1 : groups[State + "_front"][Frame];
    public int Head => headChar < 0 ? -1 : groups["Head33"][HeadAge];
    public float[] HeadMatrix => State == "Idle" ? null : states[State].headMatrices[Frame].v;
}
