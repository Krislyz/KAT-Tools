// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace KAT
{
    [Serializable]
    public class KatSlot
    {
        public string material; // name of the Unity material
        public string asset;    // its .mat path
        public string mode;     // opaque, cutout or blend: whether the texture's alpha counts
        public KatTexture[] textures;
    }

    [Serializable]
    public class KatRenderer
    {
        public string name;
        public KatSlot[] slots;
    }

    /// <summary>What Unity writes for Blender (model.kat-unity.json): the textures each material slot really uses.</summary>
    [Serializable]
    public class KatUnityFile
    {
        public const string Extension = ".kat-unity.json";

        public int kat = 1;
        public string project;
        public string model;
        public KatRenderer[] renderers;

        public static string PathFor(string modelAssetPath)
        {
            return Path.ChangeExtension(modelAssetPath, null) + Extension;
        }
    }

    public static class KatUnitySync
    {
        public static string ProjectRoot
        {
            get { return Path.GetDirectoryName(Application.dataPath).Replace('\\', '/'); }
        }

        /// <summary>Slots are matched by renderer name and slot number, so the Unity material may be named anything.</summary>
        public static string RoleOf(string property)
        {
            string p = property.ToLowerInvariant();
            if (p.Contains("2nd") || p.Contains("3rd") || p.Contains("detail") || p.Contains("mask"))
                return "other";
            if (p == "_maintex" || p == "_basemap" || p == "_basecolormap" || p == "_basecolortex" || p == "_albedo")
                return "baseColor";
            if (p.Contains("bump") || p.Contains("normal"))
                return "normal";
            if (p.Contains("emission"))
                return "emission";
            return "other";
        }

        static KatSlot Describe(Material material)
        {
            var slot = new KatSlot { material = material == null ? "" : material.name, asset = "", textures = new KatTexture[0] };
            if (material == null || material.shader == null)
                return slot;
            slot.asset = AssetDatabase.GetAssetPath(material);
            string renderType = material.GetTag("RenderType", false, "Opaque");
            slot.mode = renderType == "TransparentCutout" || (renderType == "Opaque" && material.renderQueue >= 2450 && material.renderQueue < 3000)
                ? "cutout" : renderType != "Opaque" || material.renderQueue >= 3000 ? "blend" : "opaque";
            var found = new List<KatTexture>();
            Shader shader = material.shader;
            for (int i = 0; i < ShaderUtil.GetPropertyCount(shader); i++)
            {
                if (ShaderUtil.GetPropertyType(shader, i) != ShaderUtil.ShaderPropertyType.TexEnv)
                    continue;
                string property = ShaderUtil.GetPropertyName(shader, i);
                Texture texture = material.GetTexture(property);
                string path = texture == null ? "" : AssetDatabase.GetAssetPath(texture);
                if (!path.StartsWith("Assets/")) // built-in and package textures are no use to Blender
                    continue;
                found.Add(new KatTexture { role = RoleOf(property), property = property, path = path });
            }
            slot.textures = found.ToArray();
            return slot;
        }

        public static bool IsModel(string path)
        {
            string ext = Path.GetExtension(path ?? "").ToLowerInvariant();
            return ext == ".fbx" || ext == ".obj" || ext == ".dae" || ext == ".blend" || ext == ".3ds";
        }

        static string ModelOf(Renderer renderer)
        {
            Mesh mesh = null;
            var skinned = renderer as SkinnedMeshRenderer;
            if (skinned != null)
                mesh = skinned.sharedMesh;
            else if (renderer.GetComponent<MeshFilter>() != null)
                mesh = renderer.GetComponent<MeshFilter>().sharedMesh;
            return mesh == null ? "" : AssetDatabase.GetAssetPath(mesh);
        }

        /// <summary>Write the sync file for whatever is selected: model files, prefabs, or objects in the scene. For a
        /// prefab or an object the materials are the ones on it now, so a swapped material comes across too.</summary>
        public static List<string> WriteSelected()
        {
            var byModel = new Dictionary<string, List<Renderer>>();
            foreach (UnityEngine.Object picked in Selection.objects)
            {
                string path = AssetDatabase.GetAssetPath(picked);
                if (IsModel(path))
                {
                    if (!byModel.ContainsKey(path))
                        byModel[path] = null; // the model's own materials
                    continue;
                }
                var go = picked as GameObject;
                if (go == null)
                    continue;
                foreach (Renderer renderer in go.GetComponentsInChildren<Renderer>(true))
                {
                    string model = ModelOf(renderer);
                    if (!IsModel(model))
                        continue;
                    if (!byModel.ContainsKey(model) || byModel[model] == null)
                        byModel[model] = new List<Renderer>();
                    byModel[model].Add(renderer);
                }
            }
            var written = new List<string>();
            foreach (var pair in byModel)
            {
                WriteFor(pair.Key, pair.Value, true);
                written.Add(pair.Key);
            }
            return written;
        }

        /// <summary>Write model.kat-unity.json for one model. Returns false when nothing changed. `only` are the
        /// renderers to describe (an instance of the model); null means the model's own. `force` writes even if the
        /// file is the same, so Blender sees a fresh time stamp.</summary>
        public static bool WriteFor(string modelPath, IEnumerable<Renderer> only = null, bool force = false)
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(modelPath);
            if (model == null)
                return false;
            var renderers = new List<KatRenderer>();
            foreach (Renderer renderer in only ?? model.GetComponentsInChildren<Renderer>(true))
            {
                var slots = new List<KatSlot>();
                foreach (Material material in renderer.sharedMaterials)
                    slots.Add(Describe(material));
                renderers.Add(new KatRenderer { name = renderer.gameObject.name, slots = slots.ToArray() });
            }
            var file = new KatUnityFile { project = ProjectRoot, model = modelPath, renderers = renderers.ToArray() };
            string json = JsonUtility.ToJson(file, true);
            string disk = Path.Combine(ProjectRoot, KatUnityFile.PathFor(modelPath));
            if (!force && File.Exists(disk) && File.ReadAllText(disk) == json)
                return false; // nothing new, and no needless re-import
            File.WriteAllText(disk, json);
            AssetDatabase.ImportAsset(KatUnityFile.PathFor(modelPath));
            return true;
        }

        /// <summary>Every model in the project that has a .kat.json beside it.</summary>
        public static List<string> KatModels()
        {
            var models = new List<string>();
            foreach (string sidecar in Directory.GetFiles(Application.dataPath, "*" + KatSidecar.Extension, SearchOption.AllDirectories))
            {
                string stem = sidecar.Substring(0, sidecar.Length - KatSidecar.Extension.Length).Replace('\\', '/');
                foreach (string ext in new[] { ".fbx", ".FBX" })
                {
                    string candidate = stem + ext;
                    if (File.Exists(candidate))
                    {
                        models.Add("Assets" + candidate.Substring(Application.dataPath.Replace('\\', '/').Length));
                        break;
                    }
                }
            }
            return models;
        }

        public static int WriteAll()
        {
            int written = 0;
            foreach (string model in KatModels())
                if (WriteFor(model))
                    written++;
            return written;
        }
    }
}
