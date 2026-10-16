# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""Plain numpy helpers for spreading a per-vertex value over a mesh. No bpy, so they test anywhere."""
import numpy as np


def neighbour_mean(values, a, b):
    """Mean of each vertex's neighbours. `a`, `b` are the two ends of every edge. Lone vertices keep their value."""
    total = np.zeros(len(values), dtype=np.float64)
    count = np.zeros(len(values), dtype=np.float64)
    np.add.at(total, a, values[b])
    np.add.at(total, b, values[a])
    np.add.at(count, a, 1.0)
    np.add.at(count, b, 1.0)
    return np.where(count > 0, total / np.maximum(count, 1.0), values)


def spread(need, a, b, length, slope=0.35, soften=2, max_hops=80):
    """Spread a per-vertex push over the neighbours so a pushed patch blends into what is around it.
    `length` is each edge's length. Away from a vertex that needs `x`, the push falls by `slope` for every unit of
    distance, so a 10 mm push fades out over about 3 cm whatever the mesh density. The result is never below `need`:
    every vertex that needed room still gets all of it."""
    need = np.asarray(need, dtype=np.float64)
    drop = slope * np.asarray(length, dtype=np.float64)
    out = need.copy()
    for _ in range(max_hops):  # grows the patch one edge at a time until nothing changes
        grown = out.copy()
        np.maximum.at(grown, a, out[b] - drop)
        np.maximum.at(grown, b, out[a] - drop)
        if np.allclose(grown, out, rtol=0.0, atol=1e-9):
            break
        out = grown
    for _ in range(soften):
        out = 0.5 * out + 0.5 * neighbour_mean(out, a, b)
    return np.maximum(out, need)
