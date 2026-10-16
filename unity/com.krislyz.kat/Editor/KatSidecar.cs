// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System;
using System.IO;
using UnityEngine;

namespace KAT
{
    [Serializable]
    public class KatBone
    {
        public string key;  // Unity HumanBodyBones name, e.g. LeftUpperArm
        public string bone; // name of the bone in the FBX
    }

    [Serializable]
    public class KatNames
    {
        public string mesh;
        public string[] names;
    }

    [Serializable]
    public class KatTexture
    {
        public string role;     // baseColor, normal, emission (or other, from Unity)
        public string property; // the shader property, only written by Unity
        public string path;     // relative to the model for Blender's file, an Assets/ path for Unity's
    }

    [Serializable]
    public class KatMaterialTextures
    {
        public string name;
        public KatTexture[] textures;
    }

    [Serializable]
    public class KatOutfit
    {
        public string mesh;
        public int layer; // 0 is the layer next to the skin
    }

    [Serializable]
    public class KatFit
    {
        public string mesh;   // the mesh that has the blend shape
        public string shape;  // KAT.fit.<what it clears>
        public string[] when; // outfit meshes that must all be worn for the shape to be on
    }

    [Serializable]
    public class KatCollider
    {
        public string mesh;   // the proxy mesh, named <mesh>_collision
        public string target; // the mesh it belongs to
        public string kind;   // BOX or CONVEX
    }

    [Serializable]
    public class KatLodGroup
    {
        public string group;
        public string[] levels;
    }

    [Serializable]
    public class KatWorld
    {
        public string[] meshes;
        public string[] @static;
        public string[] dynamic;
        public string[] pickups;
        public string[] lightmap; // meshes that already have a second UV map for lightmaps
        public KatCollider[] colliders;
        public KatLodGroup[] lods;
    }

    /// <summary>What the Blender plugin wrote next to the FBX (model.kat.json).</summary>
    [Serializable]
    public class KatSidecar
    {
        public const string Extension = ".kat.json";

        public int kat;
        public string blender;
        public KatBone[] humanoid;
        public string[] meshes;
        public KatNames[] blendshapes;
        public KatNames[] visemes;
        public string[] materials;
        public KatMaterialTextures[] materialTextures; // version 2 and later
        public string skin;         // version 3 and later: the first body mesh the clothes sit on
        public string[] skins;      // all the body meshes (head, body, hands ...)
        public KatOutfit[] outfits; // the meshes that switch on and off
        public KatFit[] fits;       // anti-clip blend shapes and when each one is on
        public KatWorld world;      // worlds and props: colliders, static flags, pickups, lightmap UVs

        public static string PathFor(string modelAssetPath)
        {
            return Path.ChangeExtension(modelAssetPath, null) + Extension;
        }

        public static KatSidecar Load(string modelAssetPath)
        {
            string path = PathFor(modelAssetPath);
            if (!File.Exists(path))
                return null;
            try
            {
                return JsonUtility.FromJson<KatSidecar>(File.ReadAllText(path));
            }
            catch (ArgumentException e)
            {
                Debug.LogWarning($"KAT: could not read {path}: {e.Message}");
                return null;
            }
        }

        public KatNames VisemesOf(string mesh)
        {
            return Array.Find(visemes ?? new KatNames[0], v => v.mesh == mesh);
        }
    }
}
