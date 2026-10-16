# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Rectangle packing for texture atlases. Pure Python, no bpy."""


def next_pow2(n):
    p = 1
    while p < n:
        p *= 2
    return p


def shelf_pack(sizes, width, height, gap=0):
    """Place rectangles left to right in rows (shelves), tallest first. `sizes` is [(w, h)]. Returns [(x, y)] in the
    order given, or None when they do not fit in width x height. `gap` pixels are kept between neighbours."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0]))
    out = [None] * len(sizes)
    x = y = row = 0
    for i in order:
        w, h = sizes[i]
        if w > width or h > height:
            return None
        if x + w > width:  # new shelf
            y += row + gap
            x, row = 0, 0
        if y + h > height:
            return None
        out[i] = (x, y)
        x += w + gap
        row = max(row, h)
    return out


def fit(sizes, atlas, gap=0, smallest=8):
    """Shrink every rectangle by the same factor (halving) until they all fit in an atlas x atlas square.
    Returns (positions, sizes after, scale) or None if even the smallest does not fit."""
    scale = 1.0
    while True:
        scaled = [(max(smallest, int(w * scale)), max(smallest, int(h * scale))) for w, h in sizes]
        spots = shelf_pack(scaled, atlas, atlas, gap)
        if spots is not None:
            return spots, scaled, scale
        scale /= 2
        if scale < 1 / 64:
            return None
