// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace KAT
{
    /// <summary>
    /// Blender to Unity: make material assets for a model from the texture list in its .kat.json and use them
    /// in place of the FBX's own materials. Materials KAT made on an earlier import are replaced or kept as you choose
    /// (asked once per export, Replace by default), and textures that live outside the project are only copied in after you say yes.
    /// </summary>
    public enum KatCopyPolicy { Ask, Copy, Skip }

    /// <summary>What to do with materials KAT made on an earlier import. Ask shows a dialog, or replaces when there is no screen.</summary>
    public enum KatReplacePolicy { Ask, Replace, Keep }

    public static class KatMaterialApply
    {
        /// <summary>Ask shows a dialog (and skips when there is no screen to show it on). Copy and Skip decide without asking.</summary>
        public static KatCopyPolicy Policy = KatCopyPolicy.Ask;

        public static KatReplacePolicy ReplacePolicy = KatReplacePolicy.Ask;

        static readonly HashSet<string> Running = new HashSet<string>();

        static string Sanitize(string name)
        {
            foreach (char c in Path.GetInvalidFileNameChars())
                name = name.Replace(c, '_');
            return name;
        }

        /// <summary>Where `relative` (from the .kat.json, relative to the model) really is, as a full path.</summary>
        static string FullPath(string modelPath, string relative)
        {
            string folder = Path.Combine(KatUnitySync.ProjectRoot, Path.GetDirectoryName(modelPath));
            return Path.GetFullPath(Path.IsPathRooted(relative) ? relative : Path.Combine(folder, relative)).Replace('\\', '/');
        }

        static string InsideProject(string full)
        {
            string assets = Application.dataPath.Replace('\\', '/') + "/";
            return full.StartsWith(assets) ? "Assets/" + full.Substring(assets.Length) : null;
        }

        static string PropertyFor(string role)
        {
            return role == "baseColor" ? "_MainTex" : role == "normal" ? "_BumpMap" : role == "emission" ? "_EmissionMap" : null;
        }

        static void Apply(Material material, string role, Texture2D texture)
        {
            string property = PropertyFor(role);
            if (property == null || !material.HasProperty(property))
                return;
            material.SetTexture(property, texture);
            if (role == "normal")
            {
                if (material.HasProperty("_UseBumpMap")) material.SetFloat("_UseBumpMap", 1f);
                else material.EnableKeyword("_NORMALMAP");
            }
            else if (role == "emission")
            {
                if (material.HasProperty("_UseEmission")) material.SetFloat("_UseEmission", 1f);
                else material.EnableKeyword("_EMISSION");
                if (material.HasProperty("_EmissionColor")) material.SetColor("_EmissionColor", Color.white);
            }
        }

        /// <summary>Returns how many materials were created.</summary>
        public static int Run(string modelPath, bool interactive, bool manual = false)
        {
            if (!Running.Add(modelPath))
                return 0;
            try
            {
                return Create(modelPath, interactive, manual);
            }
            finally
            {
                Running.Remove(modelPath);
            }
        }

        static int Create(string modelPath, bool interactive, bool manual)
        {
            KatSidecar sidecar = KatSidecar.Load(modelPath);
            var importer = AssetImporter.GetAtPath(modelPath) as ModelImporter;
            if (sidecar == null || sidecar.materialTextures == null || sidecar.materialTextures.Length == 0 || importer == null)
                return 0;

            string folder = Path.GetDirectoryName(modelPath).Replace('\\', '/');
            var remapped = importer.GetExternalObjectMap();
            var fresh = new List<KatMaterialTextures>();    // no material yet
            var existing = new List<KatMaterialTextures>(); // KAT made one on an earlier import
            foreach (KatMaterialTextures entry in sidecar.materialTextures)
            {
                var id = new AssetImporter.SourceAssetIdentifier(typeof(Material), entry.name);
                if (File.Exists(Path.Combine(KatUnitySync.ProjectRoot, MaterialPath(folder, entry))))
                    existing.Add(entry);
                else if (!(remapped.TryGetValue(id, out Object assigned) && assigned != null))
                    fresh.Add(entry);                       // a material you assigned yourself is never touched
            }

            // Materials from an earlier export: one question per export, and Replace is the default
            bool replace = existing.Count > 0 && ShouldReplace(modelPath, existing.Count, interactive, manual);
            var work = new List<KatMaterialTextures>(fresh);
            if (replace)
                work.AddRange(existing);
            if (work.Count == 0)
                return 0;
            MarkDealtWith(modelPath); // the re-import that follows making materials must not ask about them

            var outside = new List<string>(); // full paths of textures that are not in the project
            foreach (KatMaterialTextures entry in work)
                foreach (KatTexture t in entry.textures)
                {
                    string full = FullPath(modelPath, t.path);
                    if (File.Exists(full) && InsideProject(full) == null && !outside.Contains(full))
                        outside.Add(full);
                }

            bool copy = false;
            if (outside.Count > 0)
            {
                if (Policy == KatCopyPolicy.Copy)
                    copy = true;
                else if (Policy == KatCopyPolicy.Ask && interactive && !Application.isBatchMode)
                {
                    var list = new StringBuilder();
                    foreach (string f in outside.GetRange(0, Mathf.Min(8, outside.Count)))
                        list.AppendLine(Path.GetFileName(f));
                    copy = EditorUtility.DisplayDialog("KAT: textures outside the project",
                        $"{outside.Count} texture(s) used by {Path.GetFileName(modelPath)} are not in this project:\n\n{list}\nCopy them into {folder}/Textures?",
                        "Copy", "Skip");
                }
                if (!copy)
                    Debug.Log($"KAT: {outside.Count} texture(s) are outside the project and were not copied: {Path.GetFileName(outside[0])} ...");
            }

            Directory.CreateDirectory(Path.Combine(KatUnitySync.ProjectRoot, folder + "/Materials"));
            Shader shader = Shader.Find("lilToon") ?? Shader.Find("Standard");
            int created = 0, replaced = 0;
            foreach (KatMaterialTextures entry in work)
            {
                var material = new Material(shader) { name = entry.name };
                foreach (KatTexture t in entry.textures)
                {
                    string full = FullPath(modelPath, t.path);
                    string asset = InsideProject(full);
                    if (asset == null && copy && File.Exists(full))
                        asset = CopyIn(full, folder + "/Textures", t.role == "normal");
                    if (asset != null)
                        AssetDatabase.ImportAsset(asset); // a PNG written a moment ago may not be imported yet
                    var texture = asset == null ? null : AssetDatabase.LoadAssetAtPath<Texture2D>(asset);
                    if (texture != null)
                        Apply(material, t.role, texture);
                }
                string path = MaterialPath(folder, entry);
                var old = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (old != null)
                {
                    EditorUtility.CopySerialized(material, old); // same asset, so scenes and prefabs keep pointing at it
                    old.name = entry.name;
                    EditorUtility.SetDirty(old);
                    Object.DestroyImmediate(material);
                    material = old;
                    replaced++;
                }
                else
                {
                    AssetDatabase.CreateAsset(material, path);
                    created++;
                }
                importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), entry.name), material);
            }
            AssetDatabase.SaveAssets();
            importer.SaveAndReimport();
            Debug.Log($"KAT: made {created} and replaced {replaced} material(s) for {Path.GetFileName(modelPath)}");
            return created + replaced;
        }

        static string MaterialPath(string folder, KatMaterialTextures entry)
        {
            return folder + "/Materials/" + Sanitize(entry.name) + ".mat";
        }

        /// <summary>
        /// Asked once for every export of the model (so not again when Unity merely re-imports the same files).
        /// Replace is the default; it rewrites KAT's own materials in place, so nothing that uses them loses its link.
        /// Without a screen to ask on, it replaces.
        /// </summary>
        static string StampKey(string modelPath)
        {
            return "KAT.materials." + KatUnitySync.ProjectRoot + "|" + modelPath;
        }

        static string Stamp(string modelPath)
        {
            return File.GetLastWriteTimeUtc(KatSidecar.PathFor(modelPath)).Ticks.ToString();
        }

        static void MarkDealtWith(string modelPath)
        {
            EditorPrefs.SetString(StampKey(modelPath), Stamp(modelPath));
        }

        static bool ShouldReplace(string modelPath, int count, bool interactive, bool manual)
        {
            if (!manual && EditorPrefs.GetString(StampKey(modelPath), "") == Stamp(modelPath))
                return false; // this export has been dealt with
            bool replace = true;
            if (ReplacePolicy == KatReplacePolicy.Keep)
                replace = false;
            else if (ReplacePolicy == KatReplacePolicy.Ask && interactive && !Application.isBatchMode)
                replace = EditorUtility.DisplayDialog("KAT: materials already exist",
                    $"{count} material(s) for {Path.GetFileName(modelPath)} were made on an earlier import.\n\n" +
                    "Replace them with new ones made from this export, or keep them as they are?",
                    "Replace", "Keep");
            MarkDealtWith(modelPath);
            return replace;
        }

        static string CopyIn(string full, string targetFolder, bool isNormal)
        {
            string dir = Path.Combine(KatUnitySync.ProjectRoot, targetFolder);
            Directory.CreateDirectory(dir);
            string dest = Path.Combine(dir, Path.GetFileName(full));
            if (!File.Exists(dest))
                File.Copy(full, dest);
            string asset = targetFolder + "/" + Path.GetFileName(full);
            AssetDatabase.ImportAsset(asset);
            if (isNormal && AssetImporter.GetAtPath(asset) is TextureImporter importer && importer.textureType != TextureImporterType.NormalMap)
            {
                importer.textureType = TextureImporterType.NormalMap;
                importer.SaveAndReimport();
            }
            return asset;
        }
    }
}
