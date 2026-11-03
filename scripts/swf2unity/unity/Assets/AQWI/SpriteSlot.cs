using System;
using UnityEngine;

// Stub of AQW:I's SpriteSlot: one composited sprite rect, optionally colour-custom through CCMask.
[Serializable]
public class SpriteSlot
{
    public CustomizableCharacter.Slot slot;
    public Sprite sprite;
    public Texture2D CCMask;
}
