// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDK3.Avatars.ScriptableObjects;
using static VRC.SDK3.Avatars.Components.VRCAvatarDescriptor;

namespace KAT
{
    /// <summary>
    /// KAT > Set up outfit toggles: builds the outfit layers from the .kat.json, adds one saved bool per outfit to the
    /// expression parameters and puts a toggle for each in an Outfits menu. Run it again after changing the outfits:
    /// what KAT made before is replaced, everything else in the controller, parameters and menu stays.
    /// </summary>
    public static class KatOutfitMenu
    {
        const string MenuName = "Outfits";
        const int MenuLimit = 8; // a VRChat menu holds eight controls

        [MenuItem("KAT/Set up outfit toggles")]
        static void Setup()
        {
            GameObject root = Selection.activeGameObject;
            string model = KatAvatarSetup.SourceModelPath(root);
            KatSidecar sidecar = model.Length == 0 ? null : KatSidecar.Load(model);
            if (sidecar == null || sidecar.outfits == null || sidecar.outfits.Length == 0)
            {
                EditorUtility.DisplayDialog("KAT", "This model's .kat.json lists no outfits. Add them in Blender's Outfits panel and export again.", "OK");
                return;
            }
            Undo.IncrementCurrentGroup();
            var descriptor = root.GetComponent<VRCAvatarDescriptor>();
            if (descriptor == null)
                descriptor = Undo.AddComponent<VRCAvatarDescriptor>(root);
            Undo.RecordObject(descriptor, "KAT outfit toggles");
            string folder = Path.GetDirectoryName(model).Replace('\\', '/') + "/KAT_" + root.name;
            Directory.CreateDirectory(folder);

            KatOutfitClips.Result made = KatOutfitClips.Build(root, sidecar, folder, root.name, ExistingFx(descriptor));
            if (made.parameters.Count == 0)
            {
                Debug.LogWarning("KAT: none of the outfit meshes were found in " + root.name);
                return;
            }
            AssignFx(descriptor, made.controller);
            AddParameters(descriptor, made, folder, root.name);
            AddMenu(descriptor, made, folder, root.name);
            EditorUtility.SetDirty(descriptor);
            string skipped = made.skipped.Count > 0 ? ", no mesh for " + string.Join(", ", made.skipped) : "";
            Debug.Log($"KAT: {root.name}: {made.parameters.Count} outfit toggles ready{skipped}");
        }

        [MenuItem("KAT/Set up outfit toggles", true)]
        static bool CanSetup()
        {
            GameObject root = Selection.activeGameObject;
            return root != null && !EditorUtility.IsPersistent(root) && root.GetComponentInChildren<SkinnedMeshRenderer>(true) != null;
        }

        static AnimatorController ExistingFx(VRCAvatarDescriptor d)
        {
            if (!d.customizeAnimationLayers || d.baseAnimationLayers == null)
                return null;
            CustomAnimLayer fx = d.baseAnimationLayers.FirstOrDefault(l => l.type == AnimLayerType.FX && !l.isDefault);
            return fx.animatorController as AnimatorController;
        }

        static void AssignFx(VRCAvatarDescriptor d, AnimatorController controller)
        {
            d.customizeAnimationLayers = true;
            if (d.baseAnimationLayers == null || d.baseAnimationLayers.Length == 0)
                return;
            for (int i = 0; i < d.baseAnimationLayers.Length; i++)
            {
                if (d.baseAnimationLayers[i].type != AnimLayerType.FX)
                    continue;
                CustomAnimLayer layer = d.baseAnimationLayers[i];
                layer.isDefault = false;
                layer.animatorController = controller;
                d.baseAnimationLayers[i] = layer;
            }
        }

        static void AddParameters(VRCAvatarDescriptor d, KatOutfitClips.Result made, string folder, string name)
        {
            VRCExpressionParameters asset = d.expressionParameters;
            if (asset == null)
            {
                asset = ScriptableObject.CreateInstance<VRCExpressionParameters>();
                asset.parameters = new VRCExpressionParameters.Parameter[0];
                AssetDatabase.CreateAsset(asset, AssetDatabase.GenerateUniqueAssetPath($"{folder}/{name}_Parameters.asset"));
                d.expressionParameters = asset;
            }
            var list = asset.parameters.Where(p => !p.name.StartsWith(KatOutfitClips.ParameterPrefix)).ToList();
            for (int i = 0; i < made.parameters.Count; i++)
                list.Add(new VRCExpressionParameters.Parameter
                {
                    name = made.parameters[i],
                    valueType = VRCExpressionParameters.ValueType.Bool,
                    saved = true,
                    defaultValue = made.defaults[i] ? 1f : 0f
                });
            asset.parameters = list.ToArray();
            EditorUtility.SetDirty(asset);
            AssetDatabase.SaveAssets();
        }

        static void AddMenu(VRCAvatarDescriptor d, KatOutfitClips.Result made, string folder, string name)
        {
            VRCExpressionsMenu main = d.expressionsMenu;
            if (main == null)
            {
                main = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
                AssetDatabase.CreateAsset(main, AssetDatabase.GenerateUniqueAssetPath($"{folder}/{name}_Menu.asset"));
                d.expressionsMenu = main;
            }
            main.controls.RemoveAll(c => c.name == MenuName || c.name.StartsWith(MenuName + " "));
            // up to eight toggles per menu, more go in a second one
            for (int start = 0, page = 1; start < made.parameters.Count; start += MenuLimit, page++)
            {
                var sub = ScriptableObject.CreateInstance<VRCExpressionsMenu>();
                sub.controls = new List<VRCExpressionsMenu.Control>();
                foreach (string parameter in made.parameters.Skip(start).Take(MenuLimit))
                    sub.controls.Add(new VRCExpressionsMenu.Control
                    {
                        name = parameter.Substring(KatOutfitClips.ParameterPrefix.Length),
                        type = VRCExpressionsMenu.Control.ControlType.Toggle,
                        parameter = new VRCExpressionsMenu.Control.Parameter { name = parameter },
                        value = 1f
                    });
                string title = page == 1 ? MenuName : MenuName + " " + page;
                AssetDatabase.CreateAsset(sub, AssetDatabase.GenerateUniqueAssetPath($"{folder}/{name}_{title}.asset"));
                main.controls.Add(new VRCExpressionsMenu.Control
                {
                    name = title,
                    type = VRCExpressionsMenu.Control.ControlType.SubMenu,
                    subMenu = sub
                });
            }
            EditorUtility.SetDirty(main);
            AssetDatabase.SaveAssets();
        }
    }
}
