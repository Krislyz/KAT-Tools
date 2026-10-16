// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using UnityEditor;
using UnityEngine;
using VRC.SDKBase;

namespace KAT
{
    /// <summary>The VRChat side of world props: KAT > World > Add VRChat pickup gives the selected props a VRC_Pickup.</summary>
    public static class KatWorldVRChat
    {
        [MenuItem("KAT/World/Add VRChat pickup to selection")]
        static void AddPickups()
        {
            int added = 0;
            foreach (GameObject go in Selection.gameObjects)
            {
                if (EditorUtility.IsPersistent(go))
                    continue; // the model asset itself: use the instance in the scene
                if (go.GetComponent<Rigidbody>() == null)
                {
                    var body = Undo.AddComponent<Rigidbody>(go);
                    body.useGravity = true;
                }
                if (go.GetComponentInChildren<Collider>() == null)
                    Undo.AddComponent<BoxCollider>(go);
                if (go.GetComponent<VRC_Pickup>() == null)
                {
                    Undo.AddComponent<VRC_Pickup>(go);
                    added++;
                }
            }
            Debug.Log($"KAT: {added} VRChat pickups added");
        }
    }
}
