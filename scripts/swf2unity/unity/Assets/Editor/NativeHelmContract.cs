using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using Slot = CustomizableCharacter.Slot;

// Replay the installed client's setupHelm attachment sequence with test-owned
// bones and helpers. This is a loaded-bundle contract, not live gameplay proof.
public sealed class NativeHelmContract
{
    private GameObject avatar;
    private Dictionary<Slot, Transform> boneSlotMap = new Dictionary<Slot, Transform>();
    private List<Material> instantiatedMaterials = new List<Material>();
    private Transform helmPlace;

    public static GameObject Attach(GameObject prefab)
    {
        if (prefab == null) throw new Exception("Native hard-coded HelmGO prefab cannot be resolved");
        var avatar = new GameObject("NativeHelmContractAvatar");
        var harness = new NativeHelmContract { avatar = avatar };
        foreach (Slot slot in new[] {Slot.helm, Slot.fronthair, Slot.backhair})
        {
            var bone = new GameObject("Bone_" + slot).transform;
            bone.SetParent(avatar.transform, false); harness.boneSlotMap.Add(slot, bone);
        }
        harness.setupHelm(prefab);
        var helm = harness.helmPlace == null ? null : harness.helmPlace.Find("helm");
        if (helm == null || !helm.gameObject.activeInHierarchy ||
            helm.GetComponent<SpriteRenderer>() == null || !helm.GetComponent<SpriteRenderer>().enabled ||
            helm.GetComponent<SpriteRenderer>().sprite == null)
            throw new Exception("Helm is not an active rendered child after native attachment");
        if (avatar.transform.Find("HelmData").gameObject.activeSelf)
            throw new Exception("Native data root was not disabled");
        return avatar;
    }

    // Installed CustomizableCharacter.setupHelm body; Util's layer helper is
    // supplied below and empty old bones make destruction deterministic in Editor.
    public void setupHelm(GameObject helmPrefab)
    {
        if (helmPrefab == null) return;
        Transform transform = avatar.transform.Find("HelmData");
        if (transform != null) UnityEngine.Object.Destroy(transform.gameObject);
        GameObject gameObject = UnityEngine.Object.Instantiate(helmPrefab);
        ItemSlots component = gameObject.GetComponent<ItemSlots>();
        Slot[] array = {Slot.helm, Slot.fronthair, Slot.backhair};
        for (int i = 0; i < array.Length; i++)
        {
            Slot curSlot = array[i];
            Transform transform2 = gameObject.transform.Find(curSlot.ToString());
            if (transform2 == null) continue;
            if (!boneSlotMap.TryGetValue(curSlot, out var value))
                throw new Exception("No target bone found for helm slot " + curSlot);
            foreach (Transform item in value)
            {
                SpriteRenderer component2 = item.GetComponent<SpriteRenderer>();
                if (component2 != null && instantiatedMaterials.Remove(component2.sharedMaterial))
                    UnityEngine.Object.Destroy(component2.sharedMaterial);
            }
            for (int n = value.childCount - 1; n >= 0; n--)
                UnityEngine.Object.DestroyImmediate(value.GetChild(n).gameObject);
            for (int num = transform2.childCount - 1; num >= 0; num--)
                transform2.GetChild(num).SetParent(transform2.parent, worldPositionStays: true);
            transform2.SetParent(value, worldPositionStays: false);
            transform2.localPosition = Vector3.zero;
            transform2.localRotation = Quaternion.identity;
            transform2.localScale = Vector3.one;
            foreach (var child in transform2.GetComponentsInChildren<Transform>(true))
                child.gameObject.layer = value.gameObject.layer;
            if (curSlot == Slot.helm) helmPlace = value;
            SpriteSlot spriteSlot = component.slots.Where(p => p.slot == curSlot).FirstOrDefault();
            if (spriteSlot != null && spriteSlot.CCMask != null)
                throw new Exception("Unexpected native CC material replacement on converted direct-atlas helm");
        }
        gameObject.transform.SetParent(avatar.transform, worldPositionStays: false);
        gameObject.SetActive(value: false);
        gameObject.name = "HelmData";
    }
}
