"""Render scored road chunks: statewide heatmap PNG + interactive regional maps.

Usage: python render.py <processed_dir> <out_dir>
"""

import sys
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import shapely
from matplotlib.collections import LineCollection

MAJOR = {"motorway", "trunk", "primary", "secondary", "tertiary"}

# Showcase regions as (west, south, east, north) in lon/lat
REGIONS = {
    "mohawk_trail": (-73.45, 42.45, -72.55, 42.80),
    "cape_cod": (-70.70, 41.55, -69.90, 42.10),
}


def line_segments(geoms) -> list[np.ndarray]:
    coords = shapely.get_coordinates(geoms)
    counts = shapely.get_num_coordinates(geoms)
    return np.split(coords, np.cumsum(counts)[:-1])


def render_png(chunks: gpd.GeoDataFrame, out_path: Path):
    simplified = chunks.geometry.simplify(25)
    major = chunks["highway"].isin(MAJOR).values
    fig, ax = plt.subplots(figsize=(18, 11), facecolor="#0b0b12")
    ax.set_facecolor("#0b0b12")

    for mask, lw in [(~major, 0.25), (major, 0.65)]:
        sub = simplified.values[mask]
        lc = LineCollection(
            line_segments(sub), cmap="viridis", linewidths=lw, capstyle="round"
        )
        lc.set_array(chunks["score"].values[mask])
        lc.set_clim(0, 10)
        ax.add_collection(lc)

    ax.autoscale()
    ax.set_aspect("equal")
    ax.axis("off")
    cbar = fig.colorbar(lc, ax=ax, shrink=0.5, pad=0.01)
    cbar.set_label("scenic score", color="#cccccc")
    cbar.ax.tick_params(colors="#cccccc")
    ax.set_title("Massachusetts — scenic score per road segment (v0)",
                 color="#eeeeee", fontsize=15, pad=12)
    fig.savefig(out_path, dpi=240, bbox_inches="tight", facecolor="#0b0b12")
    plt.close(fig)
    print(f"wrote {out_path}")


def render_region_html(chunks_4326: gpd.GeoDataFrame, bbox, out_path: Path):
    import folium
    from branca.colormap import LinearColormap

    west, south, east, north = bbox
    sub = chunks_4326.cx[west:east, south:north]
    # keep interactive maps light: every major road + any minor road that scores well
    sub = sub[sub["highway"].isin(MAJOR) | (sub["score"] >= 4.0)]
    if len(sub) > 40_000:
        sub = sub[sub["highway"].isin(MAJOR) | (sub["score"] >= 5.0)]
    print(f"{out_path.name}: {len(sub):,} features")

    cmap = LinearColormap(
        ["#3b0f70", "#8c2981", "#de4968", "#fe9f6d", "#fcfdbf"], vmin=0, vmax=10,
        caption="scenic score",
    )
    m = folium.Map(
        location=[(south + north) / 2, (west + east) / 2],
        zoom_start=11, tiles="cartodbdark_matter",
    )
    folium.GeoJson(
        sub[["name", "highway", "score", "geometry"]].to_json(),
        style_function=lambda f: {
            "color": cmap(f["properties"]["score"]),
            "weight": 3 if f["properties"]["highway"] in MAJOR else 2,
            "opacity": 0.9,
        },
        tooltip=folium.GeoJsonTooltip(fields=["name", "score"], aliases=["road", "score"]),
    ).add_to(m)
    cmap.add_to(m)
    m.save(out_path)
    print(f"wrote {out_path}")


def main(processed_dir: str, out_dir: str):
    d, out = Path(processed_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    chunks = gpd.read_parquet(d / "scored_chunks.parquet")
    render_png(chunks, out / "ma_scenic_heatmap.png")

    chunks_4326 = chunks.copy()
    chunks_4326["geometry"] = chunks_4326.geometry.simplify(15)
    chunks_4326["score"] = chunks_4326["score"].round(2)
    chunks_4326 = chunks_4326.to_crs(4326)
    for name, bbox in REGIONS.items():
        render_region_html(chunks_4326, bbox, out / f"explore_{name}.html")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
