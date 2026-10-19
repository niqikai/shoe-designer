"""Figure: the last placed in a template's envelope, shortfall coloured and annotated.

Side view, top view and four cross-sections. matplotlib is not a project dependency:

    SHOE_ASSETS_ROOT=~/Downloads uv run --extra fast --with matplotlib \\
        python scripts/plot_fit.py dlp_solid_44_right out.png
"""
import sys, numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from shoe_designer import pose as pz
from shoe_designer.assets import load_asset, load_catalog
from shoe_designer.envelope import envelope

sid = sys.argv[1]; out = sys.argv[2]
cat = load_catalog(); last = load_asset("sneaker_last", cat).mesh; shoe = load_asset(sid, cat).mesh
env = envelope(shoe, h=1.0, closing_radius=12.0)
env_mesh = env.to_mesh()[0]
res = pz.fit_pose(env.phi, env.grid, env.mask_below(), last, ease=1.0, wall=2.0)
g = res.gap; posed = last.copy(); posed.apply_transform(res.transform)
cm = LinearSegmentedColormap.from_list("d", ["#cfd8dc", "#ffe08a", "#f4a259", "#d62828", "#6a040f"])
P = g.points; d = np.where(g.counted, g.deficit, 0.0)
order = np.argsort(d)

def sec(mesh, origin, normal):
    try:
        s = mesh.section(plane_origin=origin, plane_normal=normal)
    except Exception:
        return []
    return [] if s is None else s.discrete

fig = plt.figure(figsize=(16, 14), layout="constrained"); gs = fig.add_gridspec(3, 4, height_ratios=[1.05, 1.0, 1.15])
def scatter(ax, i, j, title):
    sc = ax.scatter(P[order, i], P[order, j], c=d[order], s=3, cmap=cm, vmin=0, vmax=6, linewidths=0, rasterized=True)
    ax.set_aspect("equal"); ax.set_title(title, fontsize=11); return sc
# side view with midline sections
ax = fig.add_subplot(gs[0, :])
sc = scatter(ax, 0, 2, "Side view (x–z): last surface coloured by shortfall, blue = envelope section at y = 0")
for pl in sec(env_mesh, [0, 0, 0], [0, 1, 0]): ax.plot(pl[:, 0], pl[:, 2], color="#1d3557", lw=1.4)
ax.axhline(0, color="#999", lw=0.8)
# top view with section at z=40
ax2 = fig.add_subplot(gs[1, :])
scatter(ax2, 0, 1, "Top view (x–y): envelope outline at z = 40 mm (blue) and z = 8 mm (dashed)")
for z, ls in ((40, "-"), (8, "--")):
    for pl in sec(env_mesh, [0, 0, z], [0, 0, 1]): ax2.plot(pl[:, 0], pl[:, 1], color="#1d3557", lw=1.3, ls=ls)
# annotations from the worst cells
cells = g.summary()
def note(ax, xy, text, xyt, i=0, j=2):
    ax.annotate(text, xy=xy, xytext=xyt, fontsize=10, color="#6a040f", arrowprops=dict(arrowstyle="->", color="#6a040f"))
w = max(range(len(d)), key=lambda k: d[k]); wp = P[w]
note(ax, (wp[0], wp[2]), f"worst: {d[w]:.1f} mm", (wp[0] - 60, wp[2] + 55))
note(ax2, (wp[0], wp[1]), f"worst: {d[w]:.1f} mm", (100, 54), 0, 1)
ax.annotate("toe tip: skin too close", xy=(278, 28), xytext=(215, 120), fontsize=10, color="#6a040f", arrowprops=dict(arrowstyle="->", color="#6a040f"))
ax.annotate("heel collar", xy=(15, 97), xytext=(30, 128), fontsize=10, color="#6a040f", arrowprops=dict(arrowstyle="->", color="#6a040f"))
ax.annotate("ball/forefoot side walls", xy=(190, 22), xytext=(110, 70), fontsize=10, color="#6a040f", arrowprops=dict(arrowstyle="->", color="#6a040f"))
ax2.annotate("side walls too narrow", xy=(215, -50), xytext=(150, -58), fontsize=10, color="#6a040f")
# cross sections
xs = [30, 110, 190, 260]
for k, x in enumerate(xs):
    a = fig.add_subplot(gs[2, k])
    for pl in sec(env_mesh, [x, 0, 0], [1, 0, 0]): a.plot(pl[:, 1], pl[:, 2], color="#1d3557", lw=1.5)
    for pl in sec(posed, [x, 0, 0], [1, 0, 0]): a.plot(pl[:, 1], pl[:, 2], color="#2a9d8f", lw=1.5)
    m = np.abs(P[:, 0] - x) < 4
    a.scatter(P[m & (d > 0.5), 1], P[m & (d > 0.5), 2], c=d[m & (d > 0.5)], cmap=cm, vmin=0, vmax=6, s=14, zorder=5)
    a.set_aspect("equal"); a.set_title(f"cross-section x = {x} mm\nblue envelope / green last", fontsize=9)
    a.set_xlim(-70, 70); a.set_ylim(-5, 125)
cb = fig.colorbar(sc, ax=fig.axes, shrink=0.4, pad=0.01); cb.set_label("shortfall (mm): extra skin push needed for 1 mm ease + 2 mm wall")
fig.suptitle(f"sneaker_last in {sid}  |  shortfall > 0.5 mm over {np.mean(d[g.counted] > 0.5)*100:.1f}% of surface below the collar, max {d.max():.1f} mm", fontsize=13)
fig.savefig(out, dpi=110)
print("saved", out)
