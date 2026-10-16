# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Work out which humanoid bone a rig bone is, from its name. Pure Python, no bpy.

Canonical keys are Unity's HumanBodyBones names (Hips, LeftUpperArm, RightIndexProximal...),
so the same key means the same thing in Blender, the Unity sidecar and VRChat.
"""
import re
import unicodedata

SINGLE = ["Hips", "Spine", "Chest", "UpperChest", "Neck", "Head", "Jaw"]
PAIRED = ["Shoulder", "UpperArm", "LowerArm", "Hand", "UpperLeg", "LowerLeg", "Foot", "Toes", "Eye"]
FINGERS = ["Thumb", "Index", "Middle", "Ring", "Little"]
JOINTS = ["Proximal", "Intermediate", "Distal"]
SIDES = ["Left", "Right"]

KEYS = (
    SINGLE
    + [s + p for s in SIDES for p in PAIRED]
    + [s + f + j for s in SIDES for f in FINGERS for j in JOINTS]
)

# Unity needs these 15 for a humanoid avatar; everything else is optional.
REQUIRED = ["Hips", "Spine", "Head"] + [
    s + p for s in SIDES for p in ("UpperLeg", "LowerLeg", "Foot", "UpperArm", "LowerArm", "Hand")
]

_SPINE_PARENTS = ("UpperChest", "Chest", "Spine", "Hips")


def parent_candidates(key):
    """Keys that may be this bone's parent, best first."""
    side = next((s for s in SIDES if key.startswith(s)), "")
    part = key[len(side):]
    if not side:
        return {
            "Hips": (),
            "Spine": ("Hips",),
            "Chest": ("Spine", "Hips"),
            "UpperChest": ("Chest", "Spine", "Hips"),
            "Neck": _SPINE_PARENTS,
            "Head": ("Neck",) + _SPINE_PARENTS,
            "Jaw": ("Head",),
        }[part]
    if part == "Shoulder":
        return _SPINE_PARENTS
    if part == "UpperArm":
        return (side + "Shoulder",) + _SPINE_PARENTS
    if part == "LowerArm":
        return (side + "UpperArm",)
    if part == "Hand":
        return (side + "LowerArm",)
    if part == "UpperLeg":
        return ("Hips",)
    if part == "LowerLeg":
        return (side + "UpperLeg",)
    if part == "Foot":
        return (side + "LowerLeg",)
    if part == "Toes":
        return (side + "Foot",)
    if part == "Eye":
        return ("Head",)
    for f in FINGERS:
        if part.startswith(f):
            joint = part[len(f):]
            before = JOINTS[JOINTS.index(joint) - 1] if joint != "Proximal" else None
            return (side + f + before,) if before else (side + "Hand",)
    return ()


# Whole-name aliases, after side/prefix stripping. Lower case, digits without leading zeros.
ALIASES = {
    "Hips": {"hips", "hip", "pelvis", "下半身", "lowerbody"},
    "Spine": {"spine", "abdomen", "upperbody", "上半身", "torso"},
    "Chest": {"chest", "spine1", "上半身2", "upperbody2"},
    "UpperChest": {"upperchest", "spine2", "上半身3", "upperbody3"},
    "Neck": {"neck", "neck1", "首"},
    "Head": {"head", "頭"},
    "Jaw": {"jaw", "chin", "顎"},
    "Shoulder": {"shoulder", "clavicle", "collar", "collarbone", "肩"},
    "UpperArm": {"upperarm", "arm", "armupper", "uparm", "腕"},
    "LowerArm": {"lowerarm", "forearm", "elbow", "armlower", "ひじ", "肘"},
    "Hand": {"hand", "wrist", "手首"},
    "UpperLeg": {"upperleg", "thigh", "upleg", "legupper", "足"},
    "LowerLeg": {"lowerleg", "calf", "shin", "knee", "leglower", "ひざ", "膝"},
    "Foot": {"foot", "ankle", "足首"},
    "Toes": {"toes", "toe", "toebase", "ball", "つま先", "toe0"},
    "Eye": {"eye", "eyeball", "目"},
}
LEG_ALIASES = {"leg"}  # upper leg normally, lower leg in rigs that also have an upleg/thigh bone

# Name pieces that carry no meaning for us. They are dropped before matching.
NOISE = {"j", "bip", "c", "def", "mch", "org", "tweak", "mixamorig", "bip01", "bip001", "armature", "f"}

JP_FINGERS = {"親指": "thumb", "人差指": "index", "人指": "index", "中指": "middle", "薬指": "ring", "小指": "little"}
FINGER_RE = re.compile(r"^(?:hand)?(thumb|index|middle|ring|little|pinky)(\d+|proximal|intermediate|distal)?$")


def _tokens(name):
    """Split a bone name into (side, tokens). side is 'Left', 'Right' or None."""
    text = unicodedata.normalize("NFKC", name)
    thumb_jp = "親指" in text
    for jp, en in JP_FINGERS.items():
        text = text.replace(jp, f" {en} ")
    text = text.replace("左", " L ").replace("右", " R ")
    parts = re.split(r"[^0-9A-Za-z぀-ヿ一-鿿]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", text)
    tokens = [t.lower() for t in parts if t]
    side = None
    kept = []
    for i, t in enumerate(tokens):
        if t in ("l", "left") and side is None and (len(tokens) > 1):
            side = "Left"
        elif t in ("r", "right") and side is None and (len(tokens) > 1):
            side = "Right"
        elif t not in NOISE:
            kept.append(t)
    if side is None and kept:
        for word, s in (("left", "Left"), ("right", "Right")):  # "leftarm" with no case change to split on
            if kept[0].startswith(word) and len(kept[0]) > len(word):
                side, kept[0] = s, kept[0][len(word):]
            elif kept[-1].endswith(word) and len(kept[-1]) > len(word):
                side, kept[-1] = s, kept[-1][:-len(word)]
    kept = [re.sub(r"^0+(?=\d)", "", t) for t in kept]
    return side, kept, thumb_jp


def _core(kept, thumb_jp, leg_is_lower):
    """Map stripped tokens to a part name like 'UpperArm' or 'IndexProximal'."""
    joined = "".join(kept)
    m = FINGER_RE.match(joined)
    if m:
        finger = {"pinky": "Little"}.get(m.group(1), m.group(1).capitalize())
        joint = m.group(2)
        if joint is None:
            return None
        if joint.isdigit():
            n = int(joint) if thumb_jp and finger == "Thumb" else int(joint) - 1
            joint = JOINTS[n] if 0 <= n < 3 else None
        else:
            joint = joint.capitalize()
        return finger + joint if joint else None
    for part, names in ALIASES.items():
        if joined in names:
            return part
    if joined in LEG_ALIASES:
        return "LowerLeg" if leg_is_lower else "UpperLeg"
    return None


def resolve_all(names):
    """{bone name: canonical key or None}. Sided parts without a side come back None."""
    parsed = {n: _tokens(n) for n in names}
    leg_is_lower = any(
        "".join(k) in ALIASES["UpperLeg"] - {"足"} for _, k, _ in parsed.values()
    )
    out = {}
    for name, (side, kept, thumb_jp) in parsed.items():
        part = _core(kept, thumb_jp, leg_is_lower)
        if part is None:
            out[name] = None
        elif part in SINGLE:
            out[name] = part
        else:
            out[name] = side + part if side else None
    if any(n.startswith("DEF-") for n in names):
        for name in names:  # Rigify's deform spine counts from the hips: DEF-spine is the hips bone
            m = re.fullmatch(r"DEF-spine(?:\.0*(\d+))?", name)
            if m:
                out[name] = RIGIFY_SPINE.get(m.group(1) or "")
    else:
        _shift_numbered_spine(parsed, out)
    return out


RIGIFY_SPINE = {"": "Hips", "1": "Spine", "2": "Chest", "3": "UpperChest", "4": "Neck", "6": "Head"}


def _shift_numbered_spine(parsed, out):
    """Unreal-style spine_01, spine_02, spine_03 with no plain 'spine': they are Spine, Chest, UpperChest."""
    cores = {n: "".join(k) for n, (_, k, _) in parsed.items()}
    if "spine" in cores.values():
        return
    for name, core in cores.items():
        m = re.fullmatch(r"spine(\d+)", core)
        if m:
            i = int(m.group(1))
            out[name] = ("Spine", "Chest", "UpperChest")[i - 1] if 1 <= i <= 3 else None


def style_name(key, style):
    """Render a canonical key as a bone name. Styles: 'unity' (LeftUpperArm) or 'blender' (UpperArm.L)."""
    if style == "unity":
        return key
    if style == "blender":
        side = next((s for s in SIDES if key.startswith(s)), None)
        return f"{key[len(side):]}.{side[0]}" if side else key
    raise ValueError(f"unknown naming style: {style}")
