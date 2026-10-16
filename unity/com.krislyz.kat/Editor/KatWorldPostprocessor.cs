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

        void OnPreprocessModel()
        {
            KatWorld world = KatSidecar.Load(assetPath)?.world;
            if (world == null)
                return;
            var importer = (ModelImporter)assetImporter;
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
            if (world == null)
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

        [MenuItem("KAT/World/Make selection static")]
        static void MakeSelectionStatic()
        {
            foreach (GameObject go in Selection.gameObjects)
                GameObjectUtility.SetStaticEditorFlags(go, StaticFlags);
            Debug.Log($"KAT: {Selection.gameObjects.Length} objects are static now");
        }

        [MenuItem("KAT/World/Bake occlusion culling")]
        static void BakeOcclusion()
        {
            if (StaticOcclusionCulling.Compute())
                Debug.Log("KAT: occlusion culling baked");
            else
                Debug.LogWarning("KAT: occlusion culling could not be baked. Mark the scenery static first");
        }

        [MenuItem("KAT/World/Report scene cost")]
        static void Report()
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
