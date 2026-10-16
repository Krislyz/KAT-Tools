# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Face tracking shapes a model has no matching shape for, approximated from the shapes it does have, plus small
geometric moves of the mouth, jaw and tongue regions. Pure numpy, no bpy.

These are starting points, not sculpts: each is made from a source shape (or a region found from where the source
shapes move) so it points the right way and has a sensible size. Sculpt over them where they look wrong.
"""
import numpy as np

SIDES = (("Left", 1.0), ("Right", -1.0))  # the character's left is +X: avatars face -Y


def norm_len(delta):
    return np.linalg.norm(delta, axis=1)


def support(delta):
    """0..1 per vertex: how much the shape moves it, relative to the most moved vertex."""
    if delta is None:
        return None
    m = norm_len(delta)
    top = m.max(initial=0.0)
    return m / top if top > 1e-9 else np.zeros(len(m))


def side_split(delta, x, x0):
    """(mass on the character's left, mass on the right) of a shape, by the x of the vertices it moves."""
    m = norm_len(delta)
    return float(m[x > x0].sum()), float(m[x < x0].sum())


def is_symmetric(delta, x, x0, share=0.25):
    """True when the shape moves both sides of the face (so it has to be cut in half to make one-sided shapes)."""
    a, b = side_split(delta, x, x0)
    total = a + b
    return total > 0 and min(a, b) / total >= share


def side_masks(x, x0, width):
    """Soft left and right masks that add up to 1 everywhere, blending across a narrow band at the centre line."""
    left = np.clip((x - x0) / (0.08 * width) + 0.5, 0.0, 1.0)
    return {"Left": left, "Right": 1.0 - left}


def rotate_about_y(points, centre, degrees):
    a = np.radians(degrees)
    c, s = np.cos(a), np.sin(a)
    p = points - centre
    out = np.stack([c * p[:, 0] + s * p[:, 2], p[:, 1], -s * p[:, 0] + c * p[:, 2]], axis=1)
    return out + centre - points


def build(basis, get, wanted):
    """{shape name: (delta, how it was made)} for the names in `wanted` that can be approximated.
    basis: (N, 3) rest positions. get(*names): the first existing source shape's delta (N, 3), or None."""
    x, y, z = basis[:, 0], basis[:, 1], basis[:, 2]
    x0 = float((x.min() + x.max()) / 2)
    u = float(x.max() - x.min())  # head width: every size below is a fraction of it
    masks = side_masks(x, x0, u)
    out = {}
    n = len(basis)

    def put(name, delta, how):
        if name in wanted and delta is not None and norm_len(delta).max(initial=0.0) > 1e-7:
            out[name] = (delta.astype(np.float32), how)

    def move(weights, dx=0.0, dy=0.0, dz=0.0):
        """weights (N,) times a move of (dx, dy, dz) head widths."""
        return weights[:, None] * (np.array([dx, dy, dz]) * u)[None, :]

    def combo(*parts):
        total, used = None, []
        for name, weight in parts:
            d = get(name)
            if d is not None:
                total = d * weight if total is None else total + d * weight
                used.append(name)
        return total, used

    zero = np.zeros(n)
    jaw_src = get("Mouth Open Big1", "Mouth A")
    dz = -jaw_src[:, 2] if jaw_src is not None else zero
    w_jaw = np.clip(dz / dz.max(), 0, 1) if jaw_src is not None and dz.max() > 1e-9 else zero
    upper = support(get("Mouth UpperLipUP", "Mouth UpperLipDown"))
    lower = support(get("Mouth LowerLipDown", "Mouth LowerLipUP"))
    corner = support(get("Mouth Wide", "Mouth Smile Close1"))
    tongue = support(get("Tongue Out", "Tongue UP"))
    upper = zero if upper is None else upper
    lower = zero if lower is None else lower
    corner = zero if corner is None else corner
    tongue = zero if tongue is None else tongue
    # the top part of the upper lip's reach is where the nose wings are
    reach = upper > 0.3
    nose = upper * (z >= np.percentile(z[reach], 50)) if reach.any() else zero

    look_left, look_right = get("Eye Look Left"), get("Eye Look Right")
    for side, sign in SIDES:
        m = masks[side]
        mine, other = (look_left, look_right) if side == "Left" else (look_right, look_left)
        put(f"EyeLookOut{side}", None if mine is None else mine * m[:, None], "Eye Look Left/Right, one eye")
        put(f"EyeLookIn{side}", None if other is None else other * m[:, None], "Eye Look Left/Right, one eye")
        big, used = combo(("Eye Pupil Big", 1.0))
        put(f"EyeDilation{side}", None if big is None else big * m[:, None], "Eye Pupil Big, one eye")
        small, used = combo(("Eye Pupil Small", 1.0))
        put(f"EyeConstrict{side}", None if small is None else small * m[:, None], "Eye Pupil Small, one eye")
        squint, used = combo(("Lower Eyelid Up", 0.6), ("Eye Smile1", 0.4))
        put(f"CheekSquint{side}", None if squint is None else squint * m[:, None], "lower eyelid and eye smile, one side")
        puff = get("plump cheeks")
        put(f"CheekSuck{side}", None if puff is None else -0.7 * puff * m[:, None], "plump cheeks pulled the other way")
        lip, used = combo(("Mouth UpperLipUP", 0.6))
        put(f"NoseSneer{side}", None if lip is None else lip * (m * nose / np.maximum(upper, 1e-9))[:, None],
            "upper lip raise, nose part")
        put(f"NasalDilation{side}", move(m * nose, dx=0.012 * sign, dz=0.004), "nose wing moved outwards")
        put(f"NasalConstrict{side}", move(m * nose, dx=-0.012 * sign, dz=-0.002), "nose wing moved inwards")
        put(f"LipSuckUpper{side}", move(m * upper, dy=0.025, dz=-0.006), "upper lip pulled in")
        put(f"LipSuckLower{side}", move(m * lower, dy=0.025, dz=0.006), "lower lip pulled in")
        put(f"LipSuckCorner{side}", move(m * corner, dx=-0.02 * sign, dy=0.02), "mouth corner pulled in")
        put(f"MouthUpperDeepen{side}", move(m * upper, dy=0.01, dz=-0.01), "upper lip down and back")
        put(f"MouthUpper{side}", move(m * upper, dx=0.03 * sign), "upper lip sideways")
        put(f"MouthLower{side}", move(m * lower, dx=0.03 * sign), "lower lip sideways")
        put(f"MouthDimple{side}", move(m * corner, dy=0.015), "mouth corner pushed in")
        put(f"MouthPress{side}", move(m * upper, dz=-0.006, dy=0.01) + move(m * lower, dz=0.006, dy=0.01), "lips pressed")
        put(f"MouthTightener{side}", move(m * corner, dx=-0.03 * sign), "mouth corner drawn in")
        put(f"Jaw{side}", move(w_jaw, dx=0.04 * sign), "jaw moved sideways")
        twist = rotate_about_y(basis, np.array([x0, y.mean(), z[tongue > 0.3].mean() if (tongue > 0.3).any() else z.mean()]),
                               35.0 * sign)
        put(f"TongueTwist{side}", twist * tongue[:, None], "tongue rolled over its length")
        put(f"Tongue{side}", move(tongue, dx=0.1 * sign), "tongue moved sideways")

    closed, used = combo(("Mouth Flat", 1.0), ("Mouth Small", 0.5))
    put("MouthClosed", closed, "Mouth Flat and Small")
    up, used = combo(("Mouth UpperLipUP", 0.5))
    put("MouthRaiserUpper", up, "upper lip raise")
    lowup, used = combo(("Mouth LowerLipUP", 1.0))
    put("MouthRaiserLower", lowup, "lower lip raise")
    put("JawForward", move(w_jaw, dy=-0.05), "jaw moved forward")
    put("JawBackward", move(w_jaw, dy=0.05), "jaw moved back")
    put("JawClench", move(w_jaw, dz=0.01), "jaw tightened")
    put("JawMandibleRaise", move(w_jaw, dz=0.03), "jaw raised")
    bend, used = combo(("Tongue RollDown", 1.0))
    put("TongueBendDown", bend if bend is not None else move(tongue, dz=-0.06), "tongue bent down")
    curl, used = combo(("Tongue RollUp", 1.0))
    put("TongueCurlUp", curl if curl is not None else move(tongue, dz=0.06), "tongue curled up")
    squish, used = combo(("Tongue Wide", 0.8))
    put("TongueSquish", squish, "Tongue Wide")
    flat, used = combo(("Tongue Wide", 0.5), ("Tongue Down", 0.3))
    put("TongueFlat", flat, "Tongue Wide and Down")
    return out
