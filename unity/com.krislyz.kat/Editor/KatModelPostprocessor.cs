// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using UnityEditor;
using UnityEngine;

namespace KAT
{
    /// <summary>
    /// Sets an FBX that has a .kat.json beside it up as a humanoid. KAT names the bones the way Unity's automatic mapping
    /// expects, so no manual avatar mapping is needed. It only does this when you ask: press the button in the KAT panel,
    /// or turn on "Set up new avatars as humanoid" to have it done on a model's first import. Nothing else is ever changed
    /// on a model, so settings you change by hand stay changed.
    /// </summary>
    public class KatModelPostprocessor : AssetPostprocessor
    {
        const string AutoKey = "KAT.AutoHumanoid";

        /// <summary>Set up a model as a humanoid the first time it is imported. Off unless you turn it on.</summary>
        public static bool Auto
        {
            get { return EditorPrefs.GetBool(AutoKey, false); }
            set { EditorPrefs.SetBool(AutoKey, value); }
        }

        /// <summary>Set the selected models up as humanoids, whatever their import settings are now. Returns how many.</summary>
        public static int SetUpSelected()
        {
            int done = 0;
            foreach (Object selected in Selection.objects)
            {
                string path = AssetDatabase.GetAssetPath(selected);
                if (!path.EndsWith(".fbx", System.StringComparison.OrdinalIgnoreCase) || !HasSkeleton(path))
                    continue;
                var importer = (ModelImporter)AssetImporter.GetAtPath(path);
                Apply(importer);
                importer.SaveAndReimport();
                done++;
            }
            Debug.Log($"KAT: set up {done} model(s) as humanoid");
            return done;
        }

        static bool HasSkeleton(string path)
        {
            KatSidecar sidecar = KatSidecar.Load(path);
            return sidecar != null && sidecar.humanoid != null && sidecar.humanoid.Length > 0; // a world or a prop has none
        }

        static void Apply(ModelImporter importer)
        {
            importer.animationType = ModelImporterAnimationType.Human;
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            importer.importBlendShapes = true;
            importer.importCameras = false;
            importer.importLights = false;
        }

        void OnPreprocessModel()
        {
            if (!(Auto && assetImporter.importSettingsMissing))
                return;
            if (!HasSkeleton(assetPath))
                return;
            Apply((ModelImporter)assetImporter);
            Debug.Log($"KAT: set up {assetPath} as a humanoid");
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
