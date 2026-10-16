// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Krislyz
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

namespace KAT
{
    /// <summary>
    /// Builds the animator that switches outfits: one bool per outfit, one layer per outfit that shows or hides its
    /// mesh, and one layer per pair of outfits that turns on the anti-clip blend shape when both are worn.
    /// Needs nothing from the VRChat SDK. KatOutfitMenu (VRChat folder) wires the result into the avatar.
    /// </summary>
    public static class KatOutfitClips
    {
        public const string ParameterPrefix = "KAT_";
        const float FullWeight = 100f;

        public class Result
        {
            public AnimatorController controller;
            public List<string> parameters = new List<string>(); // bool parameters, in outfit order
            public List<bool> defaults = new List<bool>();       // worn when the avatar loads
            public List<string> skipped = new List<string>();    // outfits with no matching mesh in the avatar
        }

        public static string ParameterOf(string mesh)
        {
            var clean = new string(mesh.Where(char.IsLetterOrDigit).ToArray());
            return ParameterPrefix + (clean.Length == 0 ? "outfit" : clean);
        }

        static SkinnedMeshRenderer Find(GameObject root, string mesh)
        {
            return root.GetComponentsInChildren<SkinnedMeshRenderer>(true).FirstOrDefault(r => r.name == mesh);
        }

        static string PathOf(GameObject root, Component c)
        {
            return AnimationUtility.CalculateTransformPath(c.transform, root.transform);
        }

        static AnimationClip MakeClip(string assetPath, Action<AnimationClip> fill)
        {
            var clip = new AnimationClip { name = Path.GetFileNameWithoutExtension(assetPath) };
            fill(clip);
            AssetDatabase.CreateAsset(clip, assetPath);
            return clip;
        }

        static void Constant(AnimationClip clip, EditorCurveBinding binding, float value)
        {
            // two keys: Unity drops a clip's curve when it has a single key and nothing else
            AnimationUtility.SetEditorCurve(clip, binding, new AnimationCurve(new Keyframe(0f, value), new Keyframe(1f / 60f, value)));
        }

        static void Active(GameObject root, AnimationClip clip, SkinnedMeshRenderer smr, bool on)
        {
            Constant(clip, EditorCurveBinding.DiscreteCurve(PathOf(root, smr), typeof(GameObject), "m_IsActive"), on ? 1f : 0f);
        }

        static void Shape(GameObject root, AnimationClip clip, SkinnedMeshRenderer smr, string shape, bool on)
        {
            Constant(clip, EditorCurveBinding.FloatCurve(PathOf(root, smr), typeof(SkinnedMeshRenderer), "blendShape." + shape),
                on ? FullWeight : 0f);
        }

        static AnimatorState AddState(AnimatorStateMachine machine, string name, Motion motion)
        {
            AnimatorState state = machine.AddState(name);
            state.motion = motion;
            state.writeDefaultValues = true;
            return state;
        }

        static AnimatorStateTransition Link(AnimatorState from, AnimatorState to)
        {
            AnimatorStateTransition t = from.AddTransition(to);
            t.hasExitTime = false;
            t.hasFixedDuration = true;
            t.duration = 0f;
            return t;
        }

        static string Safe(string name)
        {
            return new string(name.Select(c => char.IsLetterOrDigit(c) ? c : '_').ToArray());
        }

        /// <summary>
        /// Make the clips under `folder` (an Assets/ path) and the layers in `into`, or in a new controller when that
        /// is null. Layers and parameters KAT made earlier are replaced; everything else in `into` stays.
        /// </summary>
        public static Result Build(GameObject root, KatSidecar sidecar, string folder, string name, AnimatorController into = null)
        {
            var result = new Result();
            if (sidecar == null || sidecar.outfits == null || sidecar.outfits.Length == 0)
                return result;
            Directory.CreateDirectory(folder);
            AnimatorController controller = into;
            if (controller == null)
            {
                string controllerPath = AssetDatabase.GenerateUniqueAssetPath($"{folder}/{Safe(name)}_Outfits.controller");
                controller = AnimatorController.CreateAnimatorControllerAtPath(controllerPath);
            }
            else
            {
                for (int i = controller.layers.Length - 1; i >= 0; i--)
                    if (controller.layers[i].name.StartsWith("KAT "))
                        controller.RemoveLayer(i);
                foreach (AnimatorControllerParameter p in controller.parameters.Where(p => p.name.StartsWith(ParameterPrefix)).ToArray())
                    controller.RemoveParameter(p);
            }
            result.controller = controller;
            KatFit[] fits = sidecar.fits ?? new KatFit[0];
            var worn = new Dictionary<string, string>(); // outfit mesh -> parameter

            foreach (KatOutfit outfit in sidecar.outfits.OrderBy(o => o.layer))
            {
                SkinnedMeshRenderer smr = Find(root, outfit.mesh);
                if (smr == null)
                {
                    result.skipped.Add(outfit.mesh);
                    continue;
                }
                string parameter = ParameterOf(outfit.mesh);
                bool wornAtStart = smr.gameObject.activeSelf;
                worn[outfit.mesh] = parameter;
                result.parameters.Add(parameter);
                result.defaults.Add(wornAtStart);
                controller.AddParameter(new AnimatorControllerParameter
                {
                    name = parameter, type = AnimatorControllerParameterType.Bool, defaultBool = wornAtStart
                });

                // shapes that are on whenever this outfit is worn by itself: its own fit, and the skin's shrink for it
                KatFit[] alone = fits.Where(f => f.when != null && f.when.Length == 1 && f.when[0] == outfit.mesh).ToArray();
                string stem = $"{folder}/{Safe(outfit.mesh)}";
                AnimationClip off = MakeClip(AssetDatabase.GenerateUniqueAssetPath(stem + "_off.anim"), clip =>
                {
                    Active(root, clip, smr, false);
                    foreach (KatFit f in alone)
                        if (Find(root, f.mesh) is SkinnedMeshRenderer target)
                            Shape(root, clip, target, f.shape, false);
                });
                AnimationClip on = MakeClip(AssetDatabase.GenerateUniqueAssetPath(stem + "_on.anim"), clip =>
                {
                    Active(root, clip, smr, true);
                    foreach (KatFit f in alone)
                        if (Find(root, f.mesh) is SkinnedMeshRenderer target)
                            Shape(root, clip, target, f.shape, true);
                });
                AddLayer(controller, "KAT " + outfit.mesh, off, on, wornAtStart,
                    new[] { (parameter, true) }, new[] { new[] { (parameter, false) } });
            }

            foreach (KatFit fit in fits.Where(f => f.when != null && f.when.Length == 2))
            {
                SkinnedMeshRenderer target = Find(root, fit.mesh);
                if (target == null || !worn.ContainsKey(fit.when[0]) || !worn.ContainsKey(fit.when[1]))
                    continue;
                string stem = $"{folder}/{Safe(fit.mesh)}_{Safe(fit.shape)}";
                AnimationClip off = MakeClip(AssetDatabase.GenerateUniqueAssetPath(stem + "_off.anim"), clip => Shape(root, clip, target, fit.shape, false));
                AnimationClip on = MakeClip(AssetDatabase.GenerateUniqueAssetPath(stem + "_on.anim"), clip => Shape(root, clip, target, fit.shape, true));
                string a = worn[fit.when[0]], b = worn[fit.when[1]];
                bool bothAtStart = result.defaults[result.parameters.IndexOf(a)] && result.defaults[result.parameters.IndexOf(b)];
                AddLayer(controller, $"KAT fit {fit.when[0]} + {fit.when[1]}", off, on, bothAtStart,
                    new[] { (a, true), (b, true) }, new[] { new[] { (a, false) }, new[] { (b, false) } });
            }

            AssetDatabase.SaveAssets();
            return result;
        }

        /// <summary>A layer with Off and On. `toOn` conditions must all hold to go on; any one of `toOff` goes off.</summary>
        static void AddLayer(AnimatorController controller, string name, AnimationClip off, AnimationClip on, bool startOn,
            (string name, bool value)[] toOn, (string name, bool value)[][] toOff)
        {
            var layer = new AnimatorControllerLayer
            {
                name = name, defaultWeight = 1f, stateMachine = new AnimatorStateMachine { name = name, hideFlags = HideFlags.HideInHierarchy }
            };
            AssetDatabase.AddObjectToAsset(layer.stateMachine, controller);
            AnimatorState stateOff = AddState(layer.stateMachine, "Off", off);
            AnimatorState stateOn = AddState(layer.stateMachine, "On", on);
            layer.stateMachine.defaultState = startOn ? stateOn : stateOff;
            AnimatorStateTransition up = Link(stateOff, stateOn);
            foreach ((string n, bool v) in toOn)
                up.AddCondition(v ? AnimatorConditionMode.If : AnimatorConditionMode.IfNot, 0f, n);
            foreach ((string n, bool v)[] any in toOff)
            {
                AnimatorStateTransition down = Link(stateOn, stateOff);
                foreach ((string n, bool v) in any)
                    down.AddCondition(v ? AnimatorConditionMode.If : AnimatorConditionMode.IfNot, 0f, n);
            }
            controller.AddLayer(layer);
        }
    }
}
