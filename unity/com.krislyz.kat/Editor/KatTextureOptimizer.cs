// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace KAT
{
    /// <summary>
    /// KAT > Optimize > Textures of selection: caps the import size of the textures the selected objects, materials or
    /// folders use, turns on compression (crunch for colour maps), and says how much memory that saves.
    /// The texture files themselves are never changed, only their import settings.
    /// </summary>
    public static class KatTextureOptimizer
    {
        public struct Result
        {
            public int changed, total;
            public long before, after; // estimated bytes in memory with the textures compressed
        }

        /// <summary>Every texture asset the objects, materials and folders use, once each.</summary>
        public static List<string> Collect(IEnumerable<Object> objects)
        {
            var materials = new HashSet<Material>();
            var paths = new HashSet<string>();
            foreach (Object o in objects)
            {
                if (o is GameObject go)
                    foreach (Renderer r in go.GetComponentsInChildren<Renderer>(true))
                        foreach (Material m in r.sharedMaterials)
                            if (m != null)
                                materials.Add(m);
                if (o is Material mat)
                    materials.Add(mat);
                if (o is Texture)
                    paths.Add(AssetDatabase.GetAssetPath(o));
                string path = AssetDatabase.GetAssetPath(o);
                if (!string.IsNullOrEmpty(path) && AssetDatabase.IsValidFolder(path))
                    foreach (string guid in AssetDatabase.FindAssets("t:Texture2D", new[] { path }))
                        paths.Add(AssetDatabase.GUIDToAssetPath(guid));
            }
            foreach (Material m in materials)
                foreach (string name in m.GetTexturePropertyNames())
                    if (m.GetTexture(name) is Texture t)
                        paths.Add(AssetDatabase.GetAssetPath(t));
            return paths.Where(p => !string.IsNullOrEmpty(p) && AssetImporter.GetAtPath(p) is TextureImporter).ToList();
        }

        static long Bytes(int width, int height, int cap, bool mips)
        {
            double scale = System.Math.Min(1.0, (double)cap / System.Math.Max(width, height));
            double pixels = System.Math.Max(1, width * scale) * System.Math.Max(1, height * scale);
            return (long)(pixels * 1.0 * (mips ? 4.0 / 3.0 : 1.0)); // block compression: about a byte per pixel
        }

        public static Result Apply(IEnumerable<string> paths, int maxSize, bool crunch, bool dryRun = false)
        {
            var result = new Result();
            foreach (string path in paths)
            {
                var importer = (TextureImporter)AssetImporter.GetAtPath(path);
                importer.GetSourceTextureWidthAndHeight(out int w, out int h);
                result.total++;
                result.before += Bytes(w, h, int.MaxValue, importer.mipmapEnabled);
                result.after += Bytes(w, h, maxSize, importer.mipmapEnabled);
                bool normal = importer.textureType == TextureImporterType.NormalMap;
                bool wantCrunch = crunch && !normal && importer.textureType == TextureImporterType.Default;
                bool needs = importer.maxTextureSize > maxSize || importer.textureCompression == TextureImporterCompression.Uncompressed
                             || importer.crunchedCompression != wantCrunch;
                if (!needs)
                    continue;
                result.changed++;
                if (dryRun)
                    continue;
                importer.maxTextureSize = System.Math.Min(importer.maxTextureSize, maxSize);
                if (importer.textureCompression == TextureImporterCompression.Uncompressed)
                    importer.textureCompression = TextureImporterCompression.Compressed;
                importer.crunchedCompression = wantCrunch;
                if (wantCrunch)
                    importer.compressionQuality = 50;
                importer.SaveAndReimport();
            }
            return result;
        }

        [MenuItem("KAT/Optimize/Textures of selection")]
        static void Run()
        {
            List<string> paths = Collect(Selection.objects);
            if (paths.Count == 0)
            {
                Debug.LogWarning("KAT: select objects, materials, textures or a folder first");
                return;
            }
            Result r = Apply(paths, 2048, true);
            Debug.Log($"KAT: {r.changed} of {r.total} textures changed, about {r.before / 1048576f:F0} MB to {r.after / 1048576f:F0} MB in memory");
        }

        [MenuItem("KAT/Optimize/Textures of selection", true)]
        static bool CanRun()
        {
            return Selection.objects.Length > 0;
        }
    }
}
