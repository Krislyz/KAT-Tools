// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System.Linq;
using UnityEditor;
using UnityEngine;
using VRC.SDK3.Avatars.Components;
using VRC.SDKBase;

namespace KAT
{
    /// <summary>KAT > Set up VRChat avatar: add the descriptor, view position and lip sync from the .kat.json.</summary>
    public static class KatAvatarSetup
    {
        static readonly string[] VisemeOrder =
        {
            "sil", "pp", "ff", "th", "dd", "kk", "ch", "ss", "nn", "rr", "aa", "e", "ih", "oh", "ou"
        };

        [MenuItem("KAT/Set up VRChat avatar")]
        static void Setup()
        {
            GameObject root = Selection.activeGameObject;
            Animator animator = root.GetComponent<Animator>();
            Undo.IncrementCurrentGroup();

            var descriptor = root.GetComponent<VRCAvatarDescriptor>();
            if (descriptor == null)
                descriptor = Undo.AddComponent<VRCAvatarDescriptor>(root);
            Undo.RecordObject(descriptor, "KAT avatar setup");

            descriptor.ViewPosition = root.transform.InverseTransformPoint(EyePosition(animator, root));
            string note = "view position set";

            KatSidecar sidecar = KatSidecar.Load(SourceModelPath(root));
            if (sidecar != null && SetupLipSync(descriptor, root, sidecar))
                note += ", visemes set";
            EditorUtility.SetDirty(descriptor);
            Debug.Log($"KAT: {root.name}: {note}");
        }

        [MenuItem("KAT/Set up VRChat avatar", true)]
        static bool CanSetup()
        {
            GameObject root = Selection.activeGameObject;
            if (root == null || EditorUtility.IsPersistent(root))
                return false; // needs a scene object, not the FBX asset itself
            Animator animator = root.GetComponent<Animator>();
            return animator != null && animator.isHuman;
        }

        static Vector3 EyePosition(Animator animator, GameObject root)
        {
            Transform left = animator.GetBoneTransform(HumanBodyBones.LeftEye);
            Transform right = animator.GetBoneTransform(HumanBodyBones.RightEye);
            if (left != null && right != null)
                return (left.position + right.position) * 0.5f;
            Transform head = animator.GetBoneTransform(HumanBodyBones.Head);
            // no eye bones: a point a little above and in front of the head bone is close enough to adjust by hand
            return head.position + root.transform.up * 0.08f + root.transform.forward * 0.08f;
        }

        internal static string SourceModelPath(GameObject instance)
        {
            // walk up through nested prefabs and variants until we reach the model file itself
            GameObject source = PrefabUtility.GetCorrespondingObjectFromSource(instance);
            while (source != null)
            {
                string path = AssetDatabase.GetAssetPath(source);
                if (AssetImporter.GetAtPath(path) is ModelImporter)
                    return path;
                source = PrefabUtility.GetCorrespondingObjectFromSource(source);
            }
            return "";
        }

        static bool SetupLipSync(VRCAvatarDescriptor descriptor, GameObject root, KatSidecar sidecar)
        {
            foreach (SkinnedMeshRenderer smr in root.GetComponentsInChildren<SkinnedMeshRenderer>(true))
            {
                KatNames entry = sidecar.VisemesOf(smr.name);
                if (entry == null || entry.names.Length == 0)
                    continue;
                descriptor.lipSync = VRC_AvatarDescriptor.LipSyncStyle.VisemeBlendShape;
                descriptor.VisemeSkinnedMesh = smr;
                descriptor.VisemeBlendShapes = VisemeOrder
                    .Select(v => entry.names.Contains("vrc.v_" + v) ? "vrc.v_" + v : "")
                    .ToArray();
                return true;
            }
            return false;
        }
    }
}
