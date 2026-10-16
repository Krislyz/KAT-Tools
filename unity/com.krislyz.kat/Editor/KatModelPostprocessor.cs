// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using UnityEditor;
using UnityEngine;

namespace KAT
{
    /// <summary>
    /// First import of an FBX that has a .kat.json beside it: import it as a humanoid.
    /// KAT names the bones the way Unity's automatic mapping expects, so no manual avatar mapping is needed.
    /// Later imports are left alone, so settings you change by hand stay changed.
    /// </summary>
    public class KatModelPostprocessor : AssetPostprocessor
    {
        void OnPreprocessModel()
        {
            if (!assetImporter.importSettingsMissing)
                return;
            KatSidecar sidecar = KatSidecar.Load(assetPath);
            if (sidecar == null || sidecar.humanoid == null || sidecar.humanoid.Length == 0)
                return; // a world or a prop has no skeleton to map

            var importer = (ModelImporter)assetImporter;
            importer.animationType = ModelImporterAnimationType.Human;
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            importer.importBlendShapes = true;
            importer.importCameras = false;
            importer.importLights = false;
            importer.isReadable = false;
            int mapped = sidecar.humanoid == null ? 0 : sidecar.humanoid.Length;
            Debug.Log($"KAT: imported {assetPath} as a humanoid ({mapped} bones mapped in Blender)");
        }

        void OnPostprocessModel(GameObject root)
        {
            KatSidecar sidecar = KatSidecar.Load(assetPath);
            if (sidecar == null || sidecar.humanoid == null)
                return;
            foreach (KatBone entry in sidecar.humanoid)
            {
                if (root.transform.Find(entry.bone) == null && FindDeep(root.transform, entry.bone) == null)
                    Debug.LogWarning($"KAT: {assetPath} is missing bone '{entry.bone}' listed for {entry.key}");
            }
        }

        static Transform FindDeep(Transform parent, string name)
        {
            foreach (Transform child in parent)
            {
                if (child.name == name)
                    return child;
                Transform hit = FindDeep(child, name);
                if (hit != null)
                    return hit;
            }
            return null;
        }
    }
}
