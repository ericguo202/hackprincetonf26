"""Plot a model's Matterport room vectors (walls black, invisible edges cyan, doors red, windows blue).

Run from worldgen/:  uv run --with matplotlib python scripts/preview/plot_plan.py <model_id> <out.png>
Reads out/cache/matterport/<model_id>.json (written by build_house / sources.matterport.fetch_model).
Coordinates are Matterport plan (x, y); house-local is (x, -y)."""
import json, sys, math
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
mid = sys.argv[1]
m = json.load(open(f"out/cache/matterport/{mid}.json"))
fig, ax = plt.subplots(figsize=(14, 12))
cols = {'wall': 'k', 'invisible': 'c'}
for i, r in enumerate(m['floors'][0]['rooms']):
    lab = (r.get('classifications') or [{}])[0].get('label')
    xs, ys = [], []
    for e in (r.get('boundary') or {}).get('edges', []):
        (a, b) = [(v['position']['x'], v['position']['y']) for v in e['vertices']]
        ax.plot([a[0], b[0]], [a[1], b[1]], color=cols.get(e['type'], 'm'), lw=1 + 8 * (e.get('thickness') or 0))
        L = math.dist(a, b)
        for o in e.get('openings') or []:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            ax.plot(mx, my, 'o', color='r' if o['type'] == 'doorway' else 'b')
            ax.annotate(f"{o['type'][0]}{o.get('width',0):.2f}/{L:.2f}", (mx, my), fontsize=6)
            print(lab, e['id'][:5], e['type'], o['type'], o.get('label'), 'w', o.get('width'), 'edge', round(L, 2))
        xs += [a[0], b[0]]; ys += [a[1], b[1]]
    if xs:
        ax.text(sum(xs) / len(xs), sum(ys) / len(ys), f"{i}:{lab}\n{(r.get('dimensionEstimates') or {}).get('area',0):.1f}", fontsize=9, color='g', ha='center')
ax.set_aspect('equal'); ax.grid(True, lw=0.3)
plt.savefig(sys.argv[2], dpi=90, bbox_inches='tight')
