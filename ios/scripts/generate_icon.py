"""Generate the Scenic app icon (1024x1024 PNG) with Pillow.

A winding road climbing through rolling hills under a soft sky — the scenic
drive, reduced to a clean, recognizable mark. Reproducible so the icon can be
tweaked in code rather than maintained as an opaque binary.

Run from the repo's venv:
    .venv/bin/python ios/scripts/generate_icon.py
writes ios/Sources/Assets.xcassets/AppIcon.appiconset/icon-1024.png
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
OUT = (Path(__file__).resolve().parent.parent
       / "Sources/Assets.xcassets/AppIcon.appiconset/icon-1024.png")


def lerp(a, b, t):
    """Linear blend between two RGB colors."""
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vertical_gradient(draw, top, bottom, y0, y1):
    """Fill rows y0..y1 with a smooth top->bottom color blend."""
    span = max(1, y1 - y0)
    for y in range(y0, y1):
        draw.line([(0, y), (SIZE, y)], fill=lerp(top, bottom, (y - y0) / span))


def cubic_bezier(p0, p1, p2, p3, steps=200):
    """Sample points along a cubic Bezier curve."""
    pts = []
    for i in range(steps + 1):
        t = i / steps
        mt = 1 - t
        x = (mt**3 * p0[0] + 3 * mt**2 * t * p1[0]
             + 3 * mt * t**2 * p2[0] + t**3 * p3[0])
        y = (mt**3 * p0[1] + 3 * mt**2 * t * p1[1]
             + 3 * mt * t**2 * p2[1] + t**3 * p3[1])
        pts.append((x, y))
    return pts


def stamp(draw, pts, width, fill):
    """Draw a smooth thick stroke by stamping circles along the points."""
    r = width / 2
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def main():
    img = Image.new("RGB", (SIZE, SIZE))
    draw = ImageDraw.Draw(img)

    # Sky: pale blue at top fading to a warm horizon.
    vertical_gradient(draw, (180, 224, 232), (224, 238, 220), 0, int(SIZE * 0.55))

    # Distant hills, near-to-far in deepening greens.
    hills = [
        (int(SIZE * 0.42), (140, 200, 150)),
        (int(SIZE * 0.50), (96, 178, 128)),
        (int(SIZE * 0.60), (56, 150, 104)),
    ]
    for crest, color in hills:
        draw.ellipse([-SIZE * 0.4, crest, SIZE * 0.7, crest + SIZE], fill=color)
        draw.ellipse([SIZE * 0.35, crest + 40, SIZE * 1.4, crest + SIZE], fill=color)

    # Foreground meadow.
    draw.rectangle([0, int(SIZE * 0.66), SIZE, SIZE], fill=(46, 134, 92))

    # The road: an S-curve narrowing toward the horizon (perspective).
    centerline = cubic_bezier(
        (SIZE * 0.5, SIZE * 1.02),   # bottom center, off-canvas
        (SIZE * 0.27, SIZE * 0.82),  # swings gently left
        (SIZE * 0.73, SIZE * 0.64),  # swings gently right
        (SIZE * 0.5, SIZE * 0.5),    # meets the horizon, centered
    )
    # Widths taper from wide (bottom) to narrow (far) for depth.
    n = len(centerline)

    def width_at(i):
        t = i / (n - 1)
        return 250 * (1 - t) + 26 * t

    # Draw in full passes so later stamps never cover earlier ones mid-road:
    # 1) the whole dark asphalt outline, 2) the lighter surface on top,
    # 3) the dashed center line on top of that.
    for i, pt in enumerate(centerline):
        stamp(draw, [pt], width_at(i), (60, 60, 66))
    for i, pt in enumerate(centerline):
        stamp(draw, [pt], width_at(i) * 0.86, (232, 232, 234))
    for i in range(0, n, 14):
        stamp(draw, [centerline[i]], width_at(i) * 0.06, (240, 196, 64))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
