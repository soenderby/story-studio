"""Draw the Story Studio icon with Pillow: packaging/icon.ico (16-256 px) and the tray PNG.

The design is the same as src/story_studio/web/icon.svg: an open book on an indigo tile,
a picture (sun and hill) on the left page and lines of text on the right. Coordinates are
in the SVG's 64-unit space. If you change one, change the other.

Usage: python packaging/make_icon.py [--ico PATH] [--tray PATH]
The spec (story_studio.spec) draws the tray PNG itself with draw_icon(); --tray is for previews.
"""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]

TILE = (61, 58, 158, 255)      # #3d3a9e
PAGE = (255, 248, 236, 255)    # #fff8ec
ACCENT = (245, 184, 61, 255)   # #f5b83d
INK = (61, 58, 158, 255)       # same as the tile
TEXT = (140, 137, 214, 255)    # #8c89d6

LEFT_PAGE = [(9, 19), (30.5, 23), (30.5, 49), (9, 45)]
RIGHT_PAGE = [(33.5, 23), (55, 19), (55, 45), (33.5, 49)]
COVER = [(7, 46), (32, 51), (57, 46), (57, 50), (32, 55), (7, 50)]
SUN = (19, 30, 4.5)            # centre x, centre y, radius
HILL = [(11.5, 42), (19.5, 34.5), (24, 38.5), (26.5, 36.5), (29, 39), (29, 45.5), (11.5, 42.5)]
LINES = [((37, 29.3), (51, 26.7)), ((37, 34.3), (51, 31.7)), ((37, 39.3), (46, 37.6))]
DETAIL_MIN = 24                # below this size the picture and text lines would only be noise


def draw_icon(size, supersample=8):
    """Return a size x size RGBA image of the icon."""
    scale = size * supersample / 64
    big = Image.new('RGBA', (size * supersample,) * 2, (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)

    def pts(points):
        return [(x * scale, y * scale) for x, y in points]

    draw.rounded_rectangle([2 * scale, 2 * scale, 62 * scale, 62 * scale], radius=13 * scale, fill=TILE)
    draw.polygon(pts(COVER), fill=ACCENT)
    draw.polygon(pts(LEFT_PAGE), fill=PAGE)
    draw.polygon(pts(RIGHT_PAGE), fill=PAGE)
    if size >= DETAIL_MIN:
        x, y, r = SUN
        draw.ellipse([(x - r) * scale, (y - r) * scale, (x + r) * scale, (y + r) * scale], fill=ACCENT)
        draw.polygon(pts(HILL), fill=INK)
        width = round(2.6 * scale)
        for start, end in LINES:
            draw.line(pts([start, end]), fill=TEXT, width=width)
            for cx, cy in pts([start, end]):  # round caps
                draw.ellipse([cx - width / 2, cy - width / 2, cx + width / 2, cy + width / 2], fill=TEXT)
    else:
        x, y, r = SUN
        r += 1.5  # at 16-20 px a single bold dot reads as "picture"
        draw.ellipse([(x - r) * scale, (y - r) * scale, (x + r) * scale, (y + r) * scale], fill=ACCENT)
    return big.resize((size, size), Image.LANCZOS)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ico', type=Path, default=HERE / 'icon.ico')
    parser.add_argument('--tray', type=Path, help='also write the 64 px tray PNG here')
    args = parser.parse_args()
    images = [draw_icon(size) for size in ICO_SIZES]
    images[-1].save(args.ico, format='ICO', sizes=[(s, s) for s in ICO_SIZES], append_images=images[:-1])
    print(f'Wrote {args.ico}')
    if args.tray:
        args.tray.parent.mkdir(parents=True, exist_ok=True)
        draw_icon(64).save(args.tray)
        print(f'Wrote {args.tray}')


if __name__ == '__main__':
    main()
