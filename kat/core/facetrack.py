# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""VRCFaceTracking "Unified Expressions" shape keys, derived from the face shapes a model already has.
Pure data and matching, no bpy.

Unified names: https://docs.vrcft.io/docs/tutorial-avatars/tutorial-avatars-extras/unified-blendshapes
The recipes are our own reading of how those shapes are described, not an official conversion table.
Rule they follow: when one source shape feeds several Unified shapes, the weights add up to 1, so all of
them at full strength reproduce the source and nothing is applied twice.
"""
import re
import unicodedata

SIDES = ("Right", "Left")

# Base shapes (the 102 on the VRCFT page). Names ending in Right/Left come in pairs.
_PAIRED = (
    "EyeLookOut EyeLookIn EyeLookUp EyeLookDown EyeClosed EyeSquint EyeWide EyeDilation EyeConstrict "
    "BrowPinch BrowLowerer BrowInnerUp BrowOuterUp NoseSneer NasalDilation NasalConstrict "
    "CheekSquint CheekPuff CheekSuck LipSuckUpper LipSuckLower LipSuckCorner LipFunnelUpper LipFunnelLower "
    "LipPuckerUpper LipPuckerLower MouthUpperUp MouthLowerDown MouthUpperDeepen MouthUpper MouthLower "
    "MouthCornerPull MouthCornerSlant MouthFrown MouthStretch MouthDimple MouthPress MouthTightener "
    "TongueTwist NeckFlex"
).split()
_SINGLE = (
    "JawOpen MouthClosed JawRight JawLeft JawForward JawBackward JawClench JawMandibleRaise MouthRaiserUpper "
    "MouthRaiserLower TongueOut TongueUp TongueDown TongueRight TongueLeft TongueRoll TongueBendDown "
    "TongueCurlUp TongueSquish TongueFlat SoftPalateClose ThroatSwallow"
).split()
UNIFIED = [name + side for name in _PAIRED for side in SIDES] + _SINGLE
OPTIONAL = {"SoftPalateClose", "ThroatSwallow", "NeckFlexRight", "NeckFlexLeft"}  # "unused by all interfaces"


def _both(name, weight=1.0):
    return [(name + "Right", weight / 2), (name + "Left", weight / 2)]


def _side(name, side, weight=1.0):
    return [(name + side, weight)]


def _each(names, weight=1.0):
    """Spread a source over a list of shapes, each getting an equal share."""
    return [(n, weight / len(names)) for n in names]


def _arkit_pair(arkit, targets):
    """ARKit has one shape per side, Unified does too: left feeds Left, right feeds Right."""
    out = []
    for side in ("Left", "Right"):
        out.append(((f"{arkit}{side}",), [(f"{t}{side}", w) for t, w in targets], f"{arkit}{side}"))
    return out


# (source aliases, [(unified shape, weight)], group). Within a group only the first source found is used, and a shape
# claimed by an earlier recipe is never claimed again, so a model that has both an ARKit set and visemes or MMD
# morphs is never mixed twice. ARKit sets come first.
RECIPES = []
for _arkit, _targets in (
    ("eyeBlink", [("EyeClosed", 1.0)]),
    ("eyeLookUp", [("EyeLookUp", 1.0)]),
    ("eyeLookDown", [("EyeLookDown", 1.0)]),
    ("eyeLookIn", [("EyeLookIn", 1.0)]),
    ("eyeLookOut", [("EyeLookOut", 1.0)]),
    ("eyeSquint", [("EyeSquint", 1.0)]),
    ("eyeWide", [("EyeWide", 1.0)]),
    ("browDown", [("BrowLowerer", 0.5), ("BrowPinch", 0.5)]),
    ("browOuterUp", [("BrowOuterUp", 1.0)]),
    ("cheekSquint", [("CheekSquint", 1.0)]),
    ("noseSneer", [("NoseSneer", 1.0)]),
    ("mouthSmile", [("MouthCornerPull", 0.5), ("MouthCornerSlant", 0.5)]),
    ("mouthFrown", [("MouthFrown", 1.0)]),
    ("mouthStretch", [("MouthStretch", 1.0)]),
    ("mouthDimple", [("MouthDimple", 1.0)]),
    ("mouthPress", [("MouthPress", 0.5), ("MouthTightener", 0.5)]),
    ("mouthLowerDown", [("MouthLowerDown", 1.0)]),
    ("mouthUpperUp", [("MouthUpperUp", 1.0)]),
):
    RECIPES += _arkit_pair(_arkit, _targets)
RECIPES += [
    (("browInnerUp",), _both("BrowInnerUp"), "browInnerUp"),
    (("cheekPuff",), _both("CheekPuff"), "cheekPuff"),
    (("jawOpen",), [("JawOpen", 1.0)], "jaw_open"),
    (("jawForward",), [("JawForward", 1.0)], "jawForward"),
    (("jawLeft",), [("JawLeft", 1.0)], "jawLeft"),
    (("jawRight",), [("JawRight", 1.0)], "jawRight"),
    (("mouthClose",), [("MouthClosed", 1.0)], "mouthClose"),
    (("mouthFunnel",), _each([n + s for n in ("LipFunnelUpper", "LipFunnelLower") for s in SIDES]), "funnel"),
    (("mouthPucker",), _each([n + s for n in ("LipPuckerUpper", "LipPuckerLower") for s in SIDES]), "pucker"),
    (("mouthRollLower",), _both("LipSuckLower"), "rollLower"),
    (("mouthRollUpper",), _both("LipSuckUpper"), "rollUpper"),
    (("mouthShrugLower",), [("MouthRaiserLower", 1.0)], "shrugLower"),
    (("mouthShrugUpper",), [("MouthRaiserUpper", 1.0)], "shrugUpper"),
    (("mouthLeft",), _each(["MouthUpperLeft", "MouthLowerLeft"]), "mouthLeft"),
    (("mouthRight",), _each(["MouthUpperRight", "MouthLowerRight"]), "mouthRight"),
    (("tongueOut",), [("TongueOut", 1.0)], "tongueOut"),
    # Models without an ARKit set: MMD morphs (English and Japanese names) and VRChat visemes.
    (("vrc.v_aa", "mth a", "a", "あ", "aa", "ah", "mouth a", "mouth_a"), [("JawOpen", 1.0)], "jaw_open"),
    (("vrc.v_oh", "mth o", "o", "お", "oh"), _each([n + s for n in ("LipFunnelUpper", "LipFunnelLower") for s in SIDES]), "funnel"),
    (("vrc.v_ou", "mth u", "u", "う", "ou"), _each([n + s for n in ("LipPuckerUpper", "LipPuckerLower") for s in SIDES]), "pucker"),
    (("vrc.v_ih", "mth i", "i", "い", "ih", "ee"), _both("MouthStretch"), "stretch"),
    (("blink", "まばたき", "eyes closed", "eyeclose"), _both("EyeClosed", 2.0), "blink"),
    (("wink", "ウィンク", "winkl", "wink l", "blink l", "blink_l"), [("EyeClosedLeft", 1.0)], "winkL"),
    (("wink r", "ウィンク右", "winkr", "blink r", "blink_r"), [("EyeClosedRight", 1.0)], "winkR"),
    (("smile", "にこり", "happy", "mouth smile", "mouth smile close1", "mouth smile open1"), _each([n + s for n in ("MouthCornerPull", "MouthCornerSlant") for s in SIDES]), "smile_both"),
    (("sad", "troubled", "困る", "brow sad"), _both("BrowInnerUp"), "browInnerUp"),
    (("angry", "怒り", "brow angry"), _each([n + s for n in ("BrowLowerer", "BrowPinch") for s in SIDES]), "browDown_both"),
    (("surprised", "驚き", "びっくり"), _both("EyeWide"), "eyeWide_both"),
]


# Koikatsu-style face shapes (Eye Close1L, Eyebrows Angry1R, Mouth WideL, plump cheekL ...), which many avatar bases
# and their conversions keep. Left/right come as a trailing L or R, written here after norm() has run.
def _kk(base, targets):
    out = []
    for side, name in (("l", "Left"), ("r", "Right")):
        out.append(((f"{base}{side}",), [(t + name, w) for t, w in targets], f"kk_{base}{side}"))
    return out


for _base, _targets in (
    ("eyeclose1", [("EyeClosed", 1.0)]),
    ("eyesmile1", [("EyeSquint", 1.0)]),
    ("eyesurprise", [("EyeWide", 1.0)]),
    ("eyebrowsangry1", [("BrowLowerer", 0.5), ("BrowPinch", 0.5)]),
    ("eyebrowssad1", [("BrowInnerUp", 1.0)]),
    ("eyebrowsup", [("BrowOuterUp", 1.0)]),
    ("eyebrowsdown", [("BrowLowerer", 1.0)]),
    ("mouthwide", [("MouthStretch", 1.0)]),
    ("plumpcheek", [("CheekPuff", 1.0)]),
):
    RECIPES += _kk(_base, _targets)
RECIPES += [
    (("eyeclose1",), _both("EyeClosed", 2.0), "kk_eyeclose1"),
    (("eyelookup",), _both("EyeLookUp"), "kk_lookup"),
    (("eyelookdown",), _both("EyeLookDown"), "kk_lookdown"),
    (("plumpcheeks",), _both("CheekPuff"), "kk_cheeks"),
    (("mouthsadclose1",), _both("MouthFrown"), "kk_frown"),
    (("mouthupperlipup",), _both("MouthUpperUp"), "kk_upperup"),
    (("mouthlowerlipdown",), _both("MouthLowerDown"), "kk_lowerdown"),
    (("tongueup",), [("TongueUp", 1.0)], "kk_tongueup"),
    (("tonguedown",), [("TongueDown", 1.0)], "kk_tonguedown"),
    (("tongueroll",), [("TongueRoll", 1.0)], "kk_tongueroll"),
]


def norm(text):
    """Compare names loosely: case, spaces, underscores and punctuation do not matter. Japanese is kept."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).lower())


def plan(key_names):
    """{unified shape: [(source key name, weight), ...]} for the keys a model has, and the groups that matched."""
    by_norm = {norm(n): n for n in key_names}
    mixes, used_groups = {}, set()
    for aliases, targets, group in RECIPES:
        # first source to claim a shape wins, so a model with an ARKit smile and a plain "smile" morph is not mixed twice
        free = [(name, weight) for name, weight in targets if name not in mixes]
        if group in used_groups or not free:
            continue
        source = next((by_norm[a] for a in map(norm, aliases) if a in by_norm), None)
        if source is None:
            continue
        used_groups.add(group)
        # Part of this recipe may already be claimed (a left cheek shape, then a both-cheeks one). Fill only the rest,
        # and for spreading recipes (weights add up to 1) scale up so the whole source is still used.
        total = sum(weight for _, weight in targets)
        scale = total / sum(weight for _, weight in free) if total <= 1.001 else 1.0
        for name, weight in free:
            mixes.setdefault(name, []).append((source, weight * scale))
    return mixes, used_groups


# Where each Unified shape lives on the face, for listing what a model still needs.
REGIONS = (
    ("Eyes", ("Eye",)),
    ("Brows", ("Brow",)),
    ("Cheeks and nose", ("Cheek", "Nose", "Nasal")),
    ("Jaw", ("Jaw",)),
    ("Mouth and lips", ("Mouth", "Lip")),
    ("Tongue", ("Tongue",)),
    ("Neck", ("Neck", "Throat", "SoftPalate")),
)
# Names that already work as sources for each region, so the list can say what to add.
HINTS = {
    "Eyes": "eyeBlinkLeft, eyeLookUpLeft, eyeSquintLeft, eyeWideLeft or Eye Close1L",
    "Brows": "browDownLeft, browInnerUp, browOuterUpLeft or Eyebrows Angry1L",
    "Cheeks and nose": "cheekPuff, cheekSquintLeft or noseSneerLeft",
    "Jaw": "jawOpen, jawForward, jawLeft or jawRight",
    "Mouth and lips": "mouthSmileLeft, mouthFrownLeft, mouthFunnel, mouthPucker or mouthRollUpper",
    "Tongue": "tongueOut, Tongue Up or Tongue Roll",
    "Neck": "",
}


def region_of(name):
    return next((label for label, prefixes in REGIONS if name.startswith(prefixes)), "Other")


def needed(key_names):
    """What a model's shape keys give the Unified set: (shapes it already has, shapes it can derive, and
    {region: shapes it can neither find nor derive}, the groups that matched). The optional four are left out."""
    mixes, groups = plan(key_names)
    present = set(key_names)
    have, can, missing = [], [], {}
    for name in UNIFIED:
        if name in OPTIONAL:
            continue
        if name in present:
            have.append(name)
        elif name in mixes:
            can.append(name)
        else:
            missing.setdefault(region_of(name), []).append(name)
    return have, can, missing, groups
