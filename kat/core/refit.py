# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Krislyz
"""The numbers behind refitting clothes onto another body. numpy only, no bpy, so it tests anywhere.

Three jobs:
  * warp: move a garment from its own skeleton onto the proportions of the target skeleton, bone by bone;
  * diffuse: spread the push that a few vertices need (they sit inside the body or float off it) over the whole
    garment, so it moves as one piece and keeps its folds, seams and details;
  * weights: dense per-vertex bone weights (n x bones), blended, limited and made rigid where a part must not bend.
"""
import numpy as np


def unit(v):
    n = float(np.linalg.norm(v))
    return np.asarray(v, dtype=np.float64) / n if n > 1e-12 else np.asarray(v, dtype=np.float64)


def rotation_between(a, b):
    """The shortest 3x3 rotation that turns direction a onto direction b."""
    a, b = unit(a), unit(b)
    c = float(np.dot(a, b))
    if c > 1.0 - 1e-9:
        return np.eye(3)
    if c < -1.0 + 1e-9:
        axis = unit(np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]))
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    v = np.cross(a, b)
    s2 = float(np.dot(v, v))
    vx = np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
    return np.eye(3) + vx + vx @ vx * ((1.0 - c) / s2)


def bone_matrix(src_head, src_tail, dst_head, dst_tail, limits=(0.4, 2.5)):
    """4x4 that carries a point near the source bone to the matching spot near the target bone: the bone is turned onto the
    target's direction, scaled to its length, and its head put on the target's head."""
    src_head, src_tail = np.asarray(src_head, dtype=np.float64), np.asarray(src_tail, dtype=np.float64)
    dst_head, dst_tail = np.asarray(dst_head, dtype=np.float64), np.asarray(dst_tail, dtype=np.float64)
    ds, dd = src_tail - src_head, dst_tail - dst_head
    s = float(np.clip(np.linalg.norm(dd) / max(np.linalg.norm(ds), 1e-9), *limits))
    rs = rotation_between(ds, dd) * s
    m = np.eye(4)
    m[:3, :3] = rs
    m[:3, 3] = dst_head - rs @ src_head
    return m


def warp(points, idx, w, mats):
    """Linear blend skinning: each point goes through its bones' matrices, blended by weight.
    points (n,3), idx (n,K) bone numbers, w (n,K) weights, mats (bones,4,4)."""
    points = np.asarray(points, dtype=np.float64)
    out = np.zeros_like(points)
    total = np.maximum(w.sum(axis=1), 1e-12)
    for k in range(idx.shape[1]):
        m = mats[idx[:, k]]
        moved = np.einsum("nij,nj->ni", m[:, :3, :3], points) + m[:, :3, 3]
        out += (w[:, k] / total)[:, None] * moved
    return out


def edge_arrays(edge_pairs):
    """Unique undirected edges from an (m,2) array."""
    e = np.sort(np.asarray(edge_pairs, dtype=np.int64).reshape(-1, 2), axis=1)
    e = np.unique(e, axis=0)
    return e[:, 0], e[:, 1]


def _neighbour_sum(x, a, b, n):
    s = np.empty_like(x)
    for ax in range(x.shape[1]):
        s[:, ax] = np.bincount(a, weights=x[b, ax], minlength=n) + np.bincount(b, weights=x[a, ax], minlength=n)
    return s


def weld(x, digits=5):
    """For each vertex, the first vertex at the same place. File formats split vertices along UV seams and hard edges;
    the pieces must move as one or the seam opens."""
    keys = np.round(np.asarray(x, dtype=np.float64) * 10 ** digits).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    return first[inverse.reshape(-1)]


def diffuse(a, b, n, hold, target, anchor=1e-3, iters=500, tol=1e-12, start=None, bend=False):
    """Smooth field u over a mesh graph that is pinned to `target` where `hold` is large:
    minimise  sum over edges |u_i - u_j|^2  +  sum hold_i |u_i - target_i|^2  +  anchor |u|^2.
    The pinned vertices decide, the rest follow them and fade out over about 1/sqrt(anchor) edges. With `bend` the cloth
    resists bending instead of stretching (the energy is the squared Laplacian): a pinned vertex raises a round swell
    with no point at the top, the way a thin sheet lies over a bump."""
    hold = np.asarray(hold, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64).reshape(n, -1)
    deg = (np.bincount(a, minlength=n) + np.bincount(b, minlength=n)).astype(np.float64)
    diag = deg + hold + anchor

    def laplace(x):
        return deg[:, None] * x - _neighbour_sum(x, a, b, n)

    def apply(x):
        if bend:
            return laplace(laplace(x)) + (hold + anchor)[:, None] * x
        return laplace(x) + (hold + anchor)[:, None] * x

    if bend:
        diag = deg * deg + deg + hold + anchor

    rhs = hold[:, None] * target
    x = np.zeros_like(rhs) if start is None else np.asarray(start, dtype=np.float64).copy()
    r = rhs - apply(x)
    z = r / diag[:, None]
    p = z.copy()
    rz = float((r * z).sum())
    stop = tol * max(float((rhs * rhs).sum()), 1e-30)
    for _ in range(iters * (3 if bend else 1)):
        if float((r * r).sum()) <= stop:
            break
        ap = apply(p)
        alpha = rz / max(float((p * ap).sum()), 1e-30)
        x += alpha * p
        r -= alpha * ap
        z = r / diag[:, None]
        rz_new = float((r * z).sum())
        p = z + (rz_new / max(rz, 1e-30)) * p
        rz = rz_new
    return x


def components(n, a, b):
    """Connected pieces of a mesh graph: an (n,) array of piece numbers, 0.. in order of first vertex."""
    label = np.arange(n)
    for _ in range(n):
        low = np.minimum(label[a], label[b])
        new = label.copy()
        np.minimum.at(new, a, low)
        np.minimum.at(new, b, low)
        if np.array_equal(new, label):
            break
        label = new
    _, inverse = np.unique(label, return_inverse=True)
    return inverse


def fall(d, reach):
    """1 closer than `reach`, fading to 0 at twice `reach`."""
    return np.clip(2.0 - np.asarray(d, dtype=np.float64) / max(reach, 1e-9), 0.0, 1.0)


def limit_weights(w, keep=4):
    """Keep the `keep` strongest weights of every row and make each row add up to 1."""
    w = np.asarray(w, dtype=np.float64)
    if w.shape[1] > keep:
        cut = np.argpartition(-w, keep - 1, axis=1)[:, keep:]
        w = w.copy()
        np.put_along_axis(w, cut, 0.0, axis=1)
    total = w.sum(axis=1, keepdims=True)
    return np.where(total > 1e-12, w / np.maximum(total, 1e-12), w)


def smooth_weights(w, a, b, n, rows, passes=2, strength=0.5):
    """Average each of `rows` (boolean mask) with its neighbours' weights, a few passes."""
    w = np.asarray(w, dtype=np.float64).copy()
    deg = np.maximum(np.bincount(a, minlength=n) + np.bincount(b, minlength=n), 1).astype(np.float64)
    for _ in range(passes):
        mean = _neighbour_sum(w, a, b, n) / deg[:, None]
        w[rows] = (1.0 - strength) * w[rows] + strength * mean[rows]
    return w


def fix_outliers(w, a, b, n, limit=0.8, rounds=3):
    """A vertex whose weights are nothing like its neighbours' (a stray one weighted to the other leg) takes theirs; it
    would be pulled across the body in a pose and show as a spike. `limit` is the difference in summed weight."""
    w = np.asarray(w, dtype=np.float64).copy()
    deg = np.maximum(np.bincount(a, minlength=n) + np.bincount(b, minlength=n), 1).astype(np.float64)
    for _ in range(rounds):
        mean = _neighbour_sum(w, a, b, n) / deg[:, None]
        odd = np.abs(w - mean).sum(axis=1) > limit
        if not odd.any():
            break
        w[odd] = mean[odd]
    return w


def rigid_pieces(w, piece):
    """Give every vertex of a piece the piece's average weights, so the piece moves as one block and cannot be bent or
    stretched by the skin. Pieces are an (n,) array of piece numbers; rows with piece < 0 are left alone."""
    w = np.asarray(w, dtype=np.float64).copy()
    for p in np.unique(piece[piece >= 0]):
        rows = piece == p
        w[rows] = w[rows].mean(axis=0)
    return w
