// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace KAT
{
    /// <summary>Keeps Blender and Unity pointing at the same textures. The KAT panel has the buttons.</summary>
    public class KatSyncPostprocessor : AssetPostprocessor
    {
        const string AutoKey = "KAT.AutoSync";
        const string AutoWriteKey = "KAT.AutoWrite";
        static bool pending;

        /// <summary>Make materials from what Blender wrote, when a model with a .kat.json is imported. Off unless you turn it on.</summary>
        public static bool Auto
        {
            get { return EditorPrefs.GetBool(AutoKey, false); }
            set { EditorPrefs.SetBool(AutoKey, value); }
        }

        /// <summary>Write the sync files for Blender by itself. Off unless you ask: Send to Blender does it on request.</summary>
        public static bool AutoWrite
        {
            get { return EditorPrefs.GetBool(AutoWriteKey, false); }
            set { EditorPrefs.SetBool(AutoWriteKey, value); }
        }

        static bool IsImage(string path)
        {
            string ext = Path.GetExtension(path).ToLowerInvariant();
            return ext == ".png" || ext == ".jpg" || ext == ".jpeg" || ext == ".tga" || ext == ".psd" || ext == ".tif" || ext == ".tiff";
        }

        static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            if (!Auto)
                return;
            var newModels = new List<string>();
            bool changed = false;
            foreach (string path in imported)
            {
                string ext = Path.GetExtension(path).ToLowerInvariant();
                if (ext == ".fbx" && File.Exists(KatSidecar.PathFor(path)))
                    newModels.Add(path);
                else if (AutoWrite && (ext == ".mat" || IsImage(path)))
                    changed = true;
            }
            if (newModels.Count == 0 && !changed)
                return;
            if (pending)
                return;
            pending = true;
            // not inside the import itself: creating assets and re-importing the model has to wait until it is done
            EditorApplication.delayCall += () =>
            {
                pending = false;
                foreach (string model in newModels)
                    KatMaterialApply.Run(model, true);
                if (AutoWrite)
                    KatUnitySync.WriteAll();
            };
        }

        /// <summary>Right-click a model, a prefab or an object: write what Blender needs to show it as it looks here.</summary>
        [MenuItem("Assets/KAT/Send to Blender", false, 2000)]
        [MenuItem("GameObject/KAT/Send to Blender", false, 2000)]
        public static void SendMenu()
        {
            var models = KatUnitySync.WriteSelected();
            if (models.Count == 0)
                Debug.LogWarning("KAT: select a model file, a prefab made from one, or an object in the scene.");
            else
                Debug.Log($"KAT: wrote the materials and textures of {models.Count} model(s) for Blender, next to the model file. "
                          + "In Blender press Import in the KAT tab and pick the model: the textures come along. "
                          + "A model that is already open: Optimize > Textures > Sync textures from Unity.");
        }

        [MenuItem("Assets/KAT/Send to Blender", true)]
        [MenuItem("GameObject/KAT/Send to Blender", true)]
        public static bool SendCheck()
        {
            foreach (Object picked in Selection.objects)
                if (picked is GameObject || KatUnitySync.IsModel(AssetDatabase.GetAssetPath(picked)))
                    return true;
            return false;
        }

        public static void WriteMenu()
        {
            int n = KatUnitySync.WriteAll();
            Debug.Log($"KAT: updated {n} sync file(s) for the models that have a .kat.json.");
        }

        public static void ApplyMenu()
        {
            int total = 0;
            foreach (Object selected in Selection.objects)
            {
                string path = AssetDatabase.GetAssetPath(selected);
                if (path.ToLowerInvariant().EndsWith(".fbx"))
                    total += KatMaterialApply.Run(path, true, manual: true);
            }
            Debug.Log($"KAT: made {total} material(s). Select model files (.fbx that have a .kat.json) first.");
        }

    }
}
