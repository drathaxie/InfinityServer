using UnityEngine;

// Stub of AQW:I's Assembly-CSharp class. Only the Slot enum is used (by CustomizableSlot / SpriteSlot fields);
// the client binds bundle scripts by class name, so names and serialized fields must match the real ones.
public class CustomizableCharacter : MonoBehaviour
{
    public enum Slot
    {
        backhairskin = 0, backhair = 1, cape = 2, backweapon = 3, backshoulder = 4, backhand = 5, backrobe = 6,
        backfoot = 7, backthigh = 8, backshin = 9, chestskin = 10, chest = 11, hipskin = 12, hip = 13,
        frontthighskin = 14, frontthigh = 15, frontshinskin = 16, frontshin = 17, idlefootskin = 18, idlefoot = 19,
        frontfootskin = 20, frontfoot = 21, robe = 22, head = 23, headskin = 25, fronthairskin = 26, fronthair = 27,
        earskin = 28, frontshoulderskin = 29, frontshoulder = 30, fronthandskin = 31, fronthand = 32, weapon = 33,
        eyes = 34, eyescolor = 35, eyeslight = 36, helm = 37, cape_walk = 38, ear = 39, backearskin = 40,
        backear = 41, eyesblink = 42
    }
}
