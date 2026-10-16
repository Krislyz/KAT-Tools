# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Find the texture files a model asked for. Pure Python, no bpy, with a hard time limit."""
import difflib
import os
import re
import time
from collections import deque

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff", ".webp", ".dds", ".exr", ".hdr"}
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", "$recycle.bin", "system volume information", "windows",
             "program files", "program files (x86)", "appdata"}

# Name endings that say what a map is for. Base colour maps first, then maps that are not colour.
COLOUR_TAGS = ("diffuse", "albedo", "basecolor", "basecolour", "color", "colour", "col", "d", "dif", "base", "tex")
OTHER_TAGS = ("normal", "nrm", "norm", "nor", "n", "spec", "specular", "s", "rough", "roughness", "r", "metal",
              "metallic", "metalness", "m", "ao", "occlusion", "mask", "alpha", "opacity", "height", "bump", "disp",
              "emission", "emissive", "emit", "e", "gloss", "glossiness", "orm", "mrao", "shadow", "toon", "sph")


def norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def stem(path):
    return os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]


def role(name):
    """('colour' | 'other' | 'plain', the name without its role ending)."""
    parts = re.split(r"[_\-. ]+", stem(name).lower())
    while len(parts) > 1 and parts[-1] in COLOUR_TAGS + OTHER_TAGS + ("2k", "4k", "1k", "8k", "hd", "01", "1"):
        tag = parts.pop()
        if tag in OTHER_TAGS:
            return "other", norm("".join(parts))
        if tag in COLOUR_TAGS:
            return "colour", norm("".join(parts))
    return "plain", norm("".join(parts))


def scan(roots, seconds=8.0, max_files=60000, max_depth=5):
    """Image files under `roots`, nearest first (breadth first, roots in the order given).
    Stops at the time limit, so a huge folder tree can never hold the user up."""
    deadline = time.monotonic() + seconds
    found, seen = [], set()
    queue = deque((root, 0) for root in roots if root and os.path.isdir(root))
    while queue and time.monotonic() < deadline and len(found) < max_files:
        folder, depth = queue.popleft()
        key = os.path.normcase(os.path.abspath(folder))
        if key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth < max_depth and entry.name.lower() not in SKIP_DIRS:
                                queue.append((entry.path, depth + 1))
                        elif os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS:
                            found.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def match_reference(reference, paths):
    """The file `reference` (a path the model recorded, possibly from another computer) most likely means."""
    base = os.path.basename(reference.replace("\\", "/")).lower()
    want_stem, want_norm = stem(reference).lower(), norm(stem(reference))
    for rule in (
        lambda p: os.path.basename(p).lower() == base,
        lambda p: stem(p).lower() == want_stem,
        lambda p: norm(stem(p)) == want_norm,
    ):
        hit = next((p for p in paths if rule(p)), None)
        if hit:
            return hit
    close = [p for p in paths if want_norm and (want_norm in norm(stem(p)) or norm(stem(p)) in want_norm)
             and len(norm(stem(p))) >= 3]
    if close:
        best = max(close, key=lambda p: difflib.SequenceMatcher(None, want_norm, norm(stem(p))).ratio())
        if difflib.SequenceMatcher(None, want_norm, norm(stem(best))).ratio() >= 0.7:
            return best
    return None


def match_material(material_name, paths):
    """A base colour image for a material that has none, from the material's name. Normal, roughness and
    other non-colour maps are never picked."""
    want = norm(re.sub(r"\.\d{3}$", "", material_name))
    if len(want) < 3:
        return None
    best, best_score = None, 0
    for p in paths:
        kind, base = role(p)
        if kind == "other" or not base or (base != want and want not in base and base not in want):
            continue
        score = (4 if base == want else 2) + (1 if kind == "colour" else 0)
        if score > best_score:
            best, best_score = p, score
    return best
