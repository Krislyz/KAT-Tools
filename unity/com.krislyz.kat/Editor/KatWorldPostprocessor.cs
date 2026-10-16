// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace KAT
{
    /// <summary>
    /// Imports a world or props FBX that came with a .kat.json "world" section: collision proxies become colliders,
    /// static meshes get static flags, pickups get a rigidbody, and meshes without a lightmap UV map get one made.
    /// LOD groups need nothing: Unity builds them from the Name_LOD0, Name_LOD1 names.
    /// </summary>
    public class KatWorldPostprocessor : AssetPostprocessor
    {
        public const StaticEditorFlags StaticFlags = StaticEditorFlags.ContributeGI | StaticEditorFlags.OccluderStatic
            | StaticEditorFlags.OccludeeStatic | StaticEditorFlags.BatchingStatic | StaticEditorFlags.ReflectionProbeStatic;

        const string AutoKey = "KAT.AutoWorld";

        /// <summary>Set up a world or props model when it is first imported. Off unless you turn it on.</summary>
        public static bool Auto
        {
            get { return EditorPrefs.GetBool(AutoKey, false); }
            set { EditorPrefs.SetBool(AutoKey, value); }
        }

        /// <summary>Set the selected world or props models up (import settings, colliders, static flags, pickups). Returns how many.</summary>
        public static int SetUpSelected()
        {
            int done = 0;
            foreach (Object selected in Selection.objects)
            {
                string path = AssetDatabase.GetAssetPath(selected);
                KatWorld world = path.EndsWith(".fbx", System.StringComparison.OrdinalIgnoreCase) ? KatSidecar.Load(path)?.world : null;
                if (world == null || !world.Present)
                    continue;
                var importer = (ModelImporter)AssetImporter.GetAtPath(path);
                Apply(importer, world);
                forced.Add(path);
                try { importer.SaveAndReimport(); }
                finally { forced.Remove(path); }
                done++;
            }
            Debug.Log($"KAT: set up {done} world or props model(s)");
            return done;
        }

        static readonly HashSet<string> forced = new HashSet<string>(); // models being set up because you asked

        bool Wanted(KatWorld world)
        {
            return world != null && world.Present && (forced.Contains(assetPath) || (Auto && assetImporter.importSettingsMissing));
        }

        void OnPreprocessModel()
        {
            KatWorld world = KatSidecar.Load(assetPath)?.world;
            if (!Wanted(world))
                return; // a model is only set up when you ask, or on its first import with the option on
            Apply((ModelImporter)assetImporter, world);
        }

        static void Apply(ModelImporter importer, KatWorld world)
        {
            int statics = world.@static == null ? 0 : world.@static.Length;
            int lightmapped = world.lightmap == null ? 0 : world.lightmap.Length;
            // Blender made lightmap UVs for some meshes; for the rest let Unity unwrap them
            importer.generateSecondaryUV = statics > lightmapped;
            importer.importCameras = false;
            importer.importLights = false;
            importer.isReadable = false;        // the mesh data stays out of memory at runtime
            importer.optimizeMeshPolygons = true;
            importer.optimizeMeshVertices = true;
        }

        void OnPostprocessModel(GameObject root)
        {
            KatWorld world = KatSidecar.Load(assetPath)?.world;
            if (!Wanted(world))
                return;
            var byName = new Dictionary<string, Transform>();
            foreach (Transform t in root.GetComponentsInChildren<Transform>(true))
                byName[t.name] = t;

            foreach (KatCollider entry in world.colliders ?? new KatCollider[0])
                if (byName.TryGetValue(entry.mesh, out Transform proxy))
                    MakeCollider(proxy.gameObject, entry.kind);

            foreach (string name in world.@static ?? new string[0])
                if (byName.TryGetValue(name, out Transform t))
                    GameObjectUtility.SetStaticEditorFlags(t.gameObject, StaticFlags);

            foreach (string name in world.pickups ?? new string[0])
                if (byName.TryGetValue(name, out Transform t) && t.GetComponent<Rigidbody>() == null)
                {
                    var body = t.gameObject.AddComponent<Rigidbody>();
                    body.useGravity = true;
                    body.isKinematic = false;
                }
        }

        static void MakeCollider(GameObject go, string kind)
        {
            var filter = go.GetComponent<MeshFilter>();
            Mesh mesh = filter == null ? null : filter.sharedMesh;
            if (mesh == null)
                return;
            if (kind == "BOX")
            {
                var box = go.AddComponent<BoxCollider>();
                box.center = mesh.bounds.center;
                box.size = mesh.bounds.size;
                Object.DestroyImmediate(go.GetComponent<MeshRenderer>());
                Object.DestroyImmediate(filter);
            }
            else
            {
                var hull = go.AddComponent<MeshCollider>();
                hull.sharedMesh = mesh;
                hull.convex = true;
                Object.DestroyImmediate(go.GetComponent<MeshRenderer>());
            }
        }

        public static void MakeSelectionStatic()
        {
            foreach (GameObject go in Selection.gameObjects)
                GameObjectUtility.SetStaticEditorFlags(go, StaticFlags);
            Debug.Log($"KAT: {Selection.gameObjects.Length} objects are static now");
        }

        public static void BakeOcclusion()
        {
            if (StaticOcclusionCulling.Compute())
                Debug.Log("KAT: occlusion culling baked");
            else
                Debug.LogWarning("KAT: occlusion culling could not be baked. Mark the scenery static first");
        }

        public static void Report()
        {
            var renderers = Object.FindObjectsOfType<MeshRenderer>();
            long tris = renderers.Select(r => r.GetComponent<MeshFilter>()).Where(f => f != null && f.sharedMesh != null)
                .Sum(f => Enumerable.Range(0, f.sharedMesh.subMeshCount).Sum(i => (long)f.sharedMesh.GetIndexCount(i)) / 3);
            int materials = renderers.SelectMany(r => r.sharedMaterials).Where(m => m != null).Distinct().Count();
            int statics = renderers.Count(r => (GameObjectUtility.GetStaticEditorFlags(r.gameObject) & StaticEditorFlags.BatchingStatic) != 0);
            Debug.Log($"KAT: {renderers.Length} renderers ({statics} static), {tris:N0} triangles, {materials} materials");
        }
    }
}
