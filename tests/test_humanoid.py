# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Run: blender -b --factory-startup --python tests/test_humanoid.py"""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("kat_humanoid", os.path.join(HERE, "..", "kat", "core", "humanoid.py"))
hum = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hum)

MIXAMO = {
    "mixamorig:Hips": "Hips", "mixamorig:Spine": "Spine", "mixamorig:Spine1": "Chest",
    "mixamorig:Spine2": "UpperChest", "mixamorig:Neck": "Neck", "mixamorig:Head": "Head",
    "mixamorig:LeftShoulder": "LeftShoulder", "mixamorig:LeftArm": "LeftUpperArm",
    "mixamorig:LeftForeArm": "LeftLowerArm", "mixamorig:LeftHand": "LeftHand",
    "mixamorig:LeftHandThumb1": "LeftThumbProximal", "mixamorig:LeftHandThumb3": "LeftThumbDistal",
    "mixamorig:LeftHandThumb4": None, "mixamorig:RightHandPinky2": "RightLittleIntermediate",
    "mixamorig:LeftUpLeg": "LeftUpperLeg", "mixamorig:LeftLeg": "LeftLowerLeg",
    "mixamorig:LeftFoot": "LeftFoot", "mixamorig:LeftToeBase": "LeftToes",
    "mixamorig:RightUpLeg": "RightUpperLeg", "mixamorig:RightLeg": "RightLowerLeg",
}
MMD = {
    "下半身": "Hips", "上半身": "Spine", "上半身2": "Chest", "首": "Neck", "頭": "Head",
    "左肩": "LeftShoulder", "左腕": "LeftUpperArm", "左ひじ": "LeftLowerArm", "左手首": "LeftHand",
    "右腕": "RightUpperArm", "左親指０": "LeftThumbProximal", "左親指２": "LeftThumbDistal",
    "左人指１": "LeftIndexProximal", "右中指３": "RightMiddleDistal", "左薬指２": "LeftRingIntermediate",
    "左小指１": "LeftLittleProximal", "左足": "LeftUpperLeg", "左ひざ": "LeftLowerLeg",
    "左足首": "LeftFoot", "左つま先": "LeftToes", "右目": "RightEye", "センター": None, "全ての親": None,
}
BLENDER = {
    "Spine": "Spine", "upper_arm.L": "LeftUpperArm", "forearm.R": "RightLowerArm", "hand.L": "LeftHand",
    "thigh.R": "RightUpperLeg", "shin.L": "LeftLowerLeg", "foot.R": "RightFoot", "toe.L": "LeftToes",
    "DEF-upper_arm.L": "LeftUpperArm", "DEF-f_index.01.L": "LeftIndexProximal", "DEF-thumb.02.R": "RightThumbIntermediate",
    "DEF-shoulder.L": "LeftShoulder", "DEF-thigh.L": "LeftUpperLeg",
}
VRM = {
    "J_Bip_C_Hips": "Hips", "J_Bip_C_Spine": "Spine", "J_Bip_C_Head": "Head",
    "J_Bip_L_UpperArm": "LeftUpperArm", "J_Bip_R_LowerArm": "RightLowerArm",
    "J_Bip_L_Index1": "LeftIndexProximal", "J_Bip_R_Thumb3": "RightThumbDistal",
}
UNITY = {k: k for k in hum.KEYS}
SPACED_NAMES = {
    "Left arm": "LeftUpperArm", "Left elbow": "LeftLowerArm", "Left wrist": "LeftHand",
    "Left leg": "LeftUpperLeg", "Left knee": "LeftLowerLeg", "Left ankle": "LeftFoot", "Left toe": "LeftToes",
    "Right shoulder": "RightShoulder", "Hips": "Hips", "Chest": "Chest",
}
IGNORED = ["Armature", "Root", "mixamorig:LeftEyeLid", "hair_01", "tail", "Skirt.L", "IK_foot.L", "L", "ear.R"]


class HumanoidTest(unittest.TestCase):
    def check(self, table):
        got = hum.resolve_all(list(table))
        for name, want in table.items():
            self.assertEqual(got[name], want, name)

    def test_mixamo(self):
        self.check(MIXAMO)

    def test_mmd(self):
        self.check(MMD)

    def test_blender_and_rigify(self):
        self.check(BLENDER)

    def test_vrm(self):
        self.check(VRM)

    def test_rigify_deform_spine_counts_from_the_hips(self):
        got = hum.resolve_all(["DEF-spine", "DEF-spine.001", "DEF-spine.002", "DEF-spine.003", "DEF-spine.004",
                               "DEF-spine.005", "DEF-spine.006", "DEF-upper_arm.L", "DEF-thigh.R"])
        self.assertEqual(got["DEF-spine"], "Hips")
        self.assertEqual(got["DEF-spine.001"], "Spine")
        self.assertEqual(got["DEF-spine.003"], "UpperChest")
        self.assertEqual(got["DEF-spine.004"], "Neck")
        self.assertIsNone(got["DEF-spine.005"])
        self.assertEqual(got["DEF-spine.006"], "Head")
        self.assertEqual(got["DEF-upper_arm.L"], "LeftUpperArm")

    def test_unreal_numbered_spine_shifts_down(self):
        got = hum.resolve_all(["pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "head"])
        self.assertEqual([got[n] for n in ("pelvis", "spine_01", "spine_02", "spine_03")],
                         ["Hips", "Spine", "Chest", "UpperChest"])

    def test_numbered_spine_with_a_plain_spine_is_left_alone(self):
        got = hum.resolve_all(["mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Spine2"])
        self.assertEqual(list(got.values()), ["Spine", "Chest", "UpperChest"])

    def test_unity_names_resolve_to_themselves(self):
        self.check(UNITY)

    def test_spaced_names_leg_is_upper(self):
        self.check(SPACED_NAMES)

    def test_leg_is_lower_when_rig_has_upleg(self):
        got = hum.resolve_all(["LeftUpLeg", "LeftLeg"])
        self.assertEqual(got, {"LeftUpLeg": "LeftUpperLeg", "LeftLeg": "LeftLowerLeg"})

    def test_non_humanoid_bones_stay_unmapped(self):
        for name in IGNORED:
            self.assertIsNone(hum.resolve_all([name])[name], name)

    def test_sided_part_without_a_side_is_unmapped(self):
        self.assertIsNone(hum.resolve_all(["UpperArm"])["UpperArm"])

    def test_style_names(self):
        self.assertEqual(hum.style_name("LeftUpperArm", "blender"), "UpperArm.L")
        self.assertEqual(hum.style_name("RightIndexProximal", "blender"), "IndexProximal.R")
        self.assertEqual(hum.style_name("Hips", "blender"), "Hips")
        self.assertEqual(hum.style_name("LeftHand", "unity"), "LeftHand")
        with self.assertRaises(ValueError):
            hum.style_name("Hips", "nope")

    def test_every_key_has_a_parent_chain_ending_at_hips(self):
        for key in hum.KEYS:
            node, hops = key, 0
            while node != "Hips":
                parents = hum.parent_candidates(node)
                self.assertTrue(parents, f"{key} has no way up from {node}")
                node = parents[-1] if node in ("Neck", "Head") else parents[0]
                hops += 1
                self.assertLess(hops, 12)

    def test_55_bones(self):
        self.assertEqual(len(hum.KEYS), 7 + 2 * 9 + 30)


if __name__ == "__main__":
    unittest.main(argv=["kat"], exit=False)
