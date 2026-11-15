"""Procedural live-oak-like tree: tapered trunk + curved limbs (bark) and alpha-card leaf clusters.

Usage: uv run python scripts/block_trees/tree.py <out_dir>   (writes live_oak_{a,b,c}.npz + variants_meta.json)
Generated for worldgen.block_combined (tree models). Inputs: Poly Haven CC0 (see out/block_compare/cache/trees/LICENSE.json).
"""

import json
import sys

import numpy as np

OUT = sys.argv[1]
TEX_W_M = 0.9  # bark texture width in metres (1024x2048 image -> 0.9 x 1.8 m)
TEX_H_M = 1.8


def bezier(P, t):
    t = t[:, None]
    return ((1 - t) ** 3) * P[0] + 3 * ((1 - t) ** 2) * t * P[1] + 3 * (1 - t) * t * t * P[2] + t**3 * P[3]


def frames(pts):
    T = np.gradient(pts, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    ref = np.array([1.0, 0, 0]) if abs(T[0, 1]) > 0.9 else np.array([0, 1.0, 0])
    N = np.cross(T[0], ref)
    N /= np.linalg.norm(N)
    Ns = [N]
    for i in range(1, len(T)):  # parallel transport
        n = Ns[-1] - T[i] * np.dot(Ns[-1], T[i])
        n /= np.linalg.norm(n)
        Ns.append(n)
    Ns = np.array(Ns)
    Bs = np.cross(T, Ns)
    return T, Ns, Bs


class Mesh:
    def __init__(s):
        s.p, s.n, s.uv, s.i = [], [], [], []
        s.nv = 0

    def add(s, p, n, uv, idx):
        s.p.append(p)
        s.n.append(n)
        s.uv.append(uv)
        s.i.append(idx + s.nv)
        s.nv += len(p)

    def arrays(s):
        return (
            np.concatenate(s.p).astype(np.float32),
            np.concatenate(s.n).astype(np.float32),
            np.concatenate(s.uv).astype(np.float32),
            np.concatenate(s.i).astype(np.int32),
        )


def tube(mesh, pts, radii, sides, cap_ring_flare=None, u0=0.0):
    T, N, B = frames(pts)
    seg = np.r_[0, np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))]
    circ = 2 * np.pi * radii.max()
    urep = max(1, int(round(circ / TEX_W_M)))
    ang = np.linspace(0, 2 * np.pi, sides + 1)
    P, Nn, UV = [], [], []
    for k in range(len(pts)):
        dirs = np.cos(ang)[:, None] * N[k] + np.sin(ang)[:, None] * B[k]
        P.append(pts[k] + radii[k] * dirs)
        Nn.append(dirs)
        UV.append(np.c_[u0 + ang / (2 * np.pi) * urep, np.full(sides + 1, seg[k] / TEX_H_M)])
    P = np.concatenate(P)
    Nn = np.concatenate(Nn)
    UV = np.concatenate(UV)
    idx = []
    R = sides + 1
    for k in range(len(pts) - 1):
        for j in range(sides):
            a, b, c, d = k * R + j, k * R + j + 1, (k + 1) * R + j, (k + 1) * R + j + 1
            idx += [[a, b, c], [b, d, c]]
    mesh.add(P, Nn, UV, np.array(idx))


def build(params, seed):
    r = np.random.default_rng(seed)
    bark, leaf = Mesh(), Mesh()
    H = params["H"]
    # ---- trunk
    th = params["trunk_h"]
    lean = np.array([r.normal(0, 0.15), 0, r.normal(0, 0.15)])
    tp = np.array(
        [[0, 0, 0], [0, th * 0.35, 0] + lean * 0.3, [0, th * 0.7, 0] + lean * 0.7, [0, th, 0] + lean]
    )
    t = np.linspace(0, 1, 7)
    pts = bezier(tp, t)
    rb = params["trunk_r"]
    radii = rb * (1.0 - 0.3 * t) * (1 + 0.45 * np.exp(-t * 9))  # root flare
    tube(bark, pts, radii, 9)
    top = pts[-1]
    # ---- limbs
    nl = params["limbs"]
    az0 = r.uniform(0, 2 * np.pi)
    tips = []  # (points along branch, t-values) for foliage seeding
    for li in range(nl):
        az = az0 + li * 2 * np.pi / nl + r.normal(0, 0.25)
        d = np.array([np.cos(az), 0, np.sin(az)])
        reach = params["spread"] * r.uniform(0.75, 1.05)
        h0 = top[1] - r.uniform(0, 0.25 * th)
        p0 = np.array([top[0], h0, top[2]]) + d * rb * 0.3
        elev = params["limb_rise"] * r.uniform(0.8, 1.2)
        p1 = p0 + d * reach * 0.25 + np.array([0, elev * 0.55, 0])
        p2 = p0 + d * reach * 0.7 + np.array([0, elev * 0.85, 0])
        p3 = p0 + d * reach + np.array([0, elev + r.uniform(-0.6, 0.8), 0])
        side = np.cross(d, [0, 1, 0]) * r.normal(0, 0.12) * reach
        LP = np.array([p0, p1 + side, p2 + side * 1.4, p3 + side])
        tl = np.linspace(0, 1, 9)
        lp = bezier(LP, tl)
        lr = rb * params["limb_r"] * (1 - 0.78 * tl)
        tube(bark, lp, lr, 7)
        tips.append(lp[-1])
        # secondary branches
        for bi in range(params["subs"]):
            ts = r.uniform(0.3, 0.92)
            q0 = bezier(LP, np.array([ts]))[0]
            baz = az + r.choice([-1, 1]) * r.uniform(0.5, 1.3)
            bd = np.array([np.cos(baz), 0, np.sin(baz)])
            bl = r.uniform(0.25, 0.45) * params["spread"]
            up = r.uniform(0.3, 1.0) * params["sub_rise"]
            Q = np.array(
                [
                    q0,
                    q0 + bd * bl * 0.35 + [0, up * 0.4, 0],
                    q0 + bd * bl * 0.75 + [0, up * 0.8, 0],
                    q0 + bd * bl + [0, up, 0],
                ]
            )
            tq = np.linspace(0, 1, 5)
            qp = bezier(Q, tq)
            qr = (rb * params["limb_r"] * (1 - 0.78 * ts)) * 0.55 * (1 - 0.75 * tq)
            tube(bark, qp, qr, 5)
            for tt in (0.5, 0.8, 1.0):
                tips.append(bezier(Q, np.array([tt]))[0])
        for tt in (0.45, 0.6, 0.75, 0.9):
            tips.append(bezier(LP, np.array([tt]))[0])
    tips = np.array(tips)
    # ---- foliage: cluster centres = branch points + fill in crown envelope
    cy = np.median(tips[:, 1])
    a_h = np.percentile(np.linalg.norm(tips[:, [0, 2]], axis=1), 95) + 0.6
    b_v = (tips[:, 1].max() - tips[:, 1].min()) / 2 + 1.3
    centre = np.array([0, cy, 0])
    cents = list(tips + r.normal(0, 0.35, tips.shape))
    nfill = params["fill"]
    while nfill > 0:
        p = r.uniform(-1, 1, 3)
        if np.sum(p**2) > 1 or np.sum(p**2) < 0.35:
            continue
        q = centre + p * np.array([a_h, b_v, a_h])
        if q[1] < params["crown_floor"] * H:
            continue
        cents.append(q)
        nfill -= 1
    cents = np.array(cents)
    atlas = [(0, 0), (0.5, 0), (0, 0.5), (0.5, 0.5)]
    for c in cents:
        for _k in range(params["cards_per"]):
            # random card orientation, biased to face outward/up
            g = (c - centre) / np.array([a_h**2, b_v**2, a_h**2])
            g /= np.linalg.norm(g) + 1e-9
            nrm = r.normal(0, 1, 3) + g * 0.6 + np.array([0, 0.3, 0])
            nrm /= np.linalg.norm(nrm)
            upv = np.array([0, 1.0, 0]) - nrm * nrm[1]
            if np.linalg.norm(upv) < 0.2:
                upv = np.array([1.0, 0, 0]) - nrm * nrm[0]
            upv /= np.linalg.norm(upv)
            rot = r.uniform(-0.6, 0.6)
            rt = np.cross(upv, nrm)
            u_ = np.cos(rot) * rt + np.sin(rot) * upv
            v_ = -np.sin(rot) * rt + np.cos(rot) * upv
            sz = params["card"] * r.uniform(0.8, 1.2)
            pc = c + r.normal(0, 0.25, 3)
            # quad corners: card bottom-centre is where the twig enters, so shift down a bit
            P = np.array(
                [
                    pc - u_ * sz / 2 - v_ * sz * 0.45,
                    pc + u_ * sz / 2 - v_ * sz * 0.45,
                    pc + u_ * sz / 2 + v_ * sz * 0.55,
                    pc - u_ * sz / 2 + v_ * sz * 0.55,
                ]
            )
            # normals: blend crown ellipsoid normal with card normal (soft volumetric shading)
            Nv = []
            for q in P:
                gq = (q - centre) / np.array([a_h**2, b_v**2, a_h**2])
                gq /= np.linalg.norm(gq) + 1e-9
                nb = 0.75 * gq + 0.25 * nrm * np.sign(np.dot(nrm, gq) + 1e-6)
                Nv.append(nb / np.linalg.norm(nb))
            Nv = np.array(Nv)
            ox, oy = atlas[r.integers(4)]
            flip = r.random() < 0.5
            us = [0.002, 0.498, 0.498, 0.002] if not flip else [0.498, 0.002, 0.002, 0.498]
            UV = np.array(
                [
                    [ox + us[0], oy + 0.498],
                    [ox + us[1], oy + 0.498],
                    [ox + us[2], oy + 0.002],
                    [ox + us[3], oy + 0.002],
                ]
            )  # glTF v=0 top
            # two-sided: front (CCW seen from +nrm) and back (reversed winding), same outward normals
            idx = np.array([[0, 1, 2], [0, 2, 3], [0, 2, 1], [0, 3, 2]])
            leaf.add(P, Nv, UV, idx)
    bp, bn, bu, bi = bark.arrays()
    lp_, ln, lu, li_ = leaf.arrays()
    # normalise: height = 1, base centre at origin
    h = max(bp[:, 1].max(), lp_[:, 1].max())
    bp /= h
    lp_ /= h
    hd = np.linalg.norm(lp_[:, [0, 2]], axis=1)
    meta = dict(
        crown_base=float(np.percentile(lp_[:, 1], 1)),
        crown_radius=float(np.percentile(hd, 98)),
        crown_center_xz=[float(lp_[:, 0].mean()), float(lp_[:, 2].mean())],
    )
    np.savez_compressed(
        f"{OUT}/{params['name']}.npz",
        bark_pos=bp,
        bark_nrm=bn,
        bark_uv=bu,
        bark_idx=bi,
        leaf_pos=lp_,
        leaf_nrm=ln,
        leaf_uv=lu,
        leaf_idx=li_,
        crown_base=np.float32(meta["crown_base"]),
        crown_radius=np.float32(meta["crown_radius"]),
        source_height_m=np.float32(h),
    )
    print(
        params["name"],
        "bark tris",
        len(bi),
        "leaf tris",
        len(li_),
        "total",
        len(bi) + len(li_),
        "h_m",
        round(float(h), 2),
        meta,
    )
    return meta


VARIANTS = [
    dict(
        name="live_oak_a",
        H=12,
        trunk_h=2.8,
        trunk_r=0.5,
        limbs=5,
        spread=7.5,
        limb_rise=4.5,
        limb_r=0.6,
        subs=3,
        sub_rise=1.8,
        fill=150,
        cards_per=4,
        card=2.2,
        crown_floor=0.3,
    ),
    dict(
        name="live_oak_b",
        H=12,
        trunk_h=3.4,
        trunk_r=0.42,
        limbs=4,
        spread=5.5,
        limb_rise=5.5,
        limb_r=0.62,
        subs=3,
        sub_rise=2.4,
        fill=140,
        cards_per=4,
        card=2.0,
        crown_floor=0.33,
    ),
    dict(
        name="live_oak_c",
        H=10,
        trunk_h=2.2,
        trunk_r=0.55,
        limbs=6,
        spread=8.5,
        limb_rise=3.2,
        limb_r=0.55,
        subs=2,
        sub_rise=1.4,
        fill=150,
        cards_per=4,
        card=2.2,
        crown_floor=0.25,
    ),
]
res = {}
for i, v in enumerate(VARIANTS):
    res[v["name"]] = build(v, 11 + i)
json.dump(res, open(f"{OUT}/variants_meta.json", "w"), indent=1)
