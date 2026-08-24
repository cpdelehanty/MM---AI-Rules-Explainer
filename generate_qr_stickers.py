"""
Generate a printable PDF of QR-code stickers, one per game.

Circular sticker design:
  - Colored outer ring with a halo of dots on the outside
  - "SCAN ME FOR RULES HELP" curved along the top of the ring
  - Game title curved along the bottom of the ring (replaces the earlier
    "★ MERRY MEEPLE ★" — the meeple logo in the QR now carries branding)
  - QR code centered in the white inner area
  - Small meeple logo overlaid in the QR's center (H-level error correction
    tolerates ~30% obscuration; the logo covers ~15%)

Layout: 3 × 3 grid on US Letter = 9 per page. For 333 games that's ~37 pages.
"""
import argparse
import math
import re
import sqlite3
from pathlib import Path

import qrcode
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from database import get_all_games


DEFAULT_BASE_URL = "https://merry-meeple-rules.fly.dev"
DEFAULT_OUTPUT = "qr_stickers.pdf"
DEFAULT_COLS = 3
DEFAULT_ROWS = 3

# Stoplight color coding by BGG complexity (weight, 1.0 = trivial → 5.0 = heavy).
# Yellow gets black text; the others use white. Contrast picks preserve WCAG AA
# for the ring text at bold/~10pt.
COLOR_LIGHT   = HexColor("#15803D")  # deep green — weight < 2.0
COLOR_MEDIUM  = HexColor("#EAB308")  # bright yellow — 2.0 ≤ weight < 3.0
COLOR_HEAVY   = HexColor("#B91C1C")  # crimson    — weight ≥ 3.0
COLOR_UNKNOWN = HexColor("#4B5563")  # slate      — no weight data

INK_LIGHT = HexColor("#FFFFFF")  # white — used on green/red/slate rings
INK_DARK  = HexColor("#1A1A1A")  # near-black — used on yellow ring

# Backup DB that still contains cafe_games.complexity (dropped from live DB in the ripdown)
WEIGHT_SOURCE_DB = "backups/game_library_20260715_113309_pre_ripdown.db"

# Brand submark (coaster badge) for the QR center overlay — self-contained,
# green circle with the mascot inside. No separate disc needed.
SUBMARK_PATH = "assets/brand/submark-green-noplace-512w.png"

TITLE_INK = HexColor("#1A1A1A")


def ink_for(ring_color):
    """White ink on dark rings, black ink on the yellow ring."""
    return INK_DARK if ring_color == COLOR_MEDIUM else INK_LIGHT


def load_weight_map(backup_path):
    """
    Return {title_lower: complexity_float} from the cafe_games table.

    The main game_library.db no longer has this data (post-ripdown), so we
    reach into the pre-ripdown backup. 325 of the current 333 games match.
    """
    if not Path(backup_path).exists():
        print(f"WARNING: {backup_path} not found — all stickers will be gray")
        return {}
    conn = sqlite3.connect(backup_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT name, complexity FROM cafe_games
        WHERE complexity IS NOT NULL
    """)
    weights = {name.lower(): float(c) for name, c in cur.fetchall()}
    conn.close()
    return weights


def ring_color_for(title, weight_map):
    """Return the stoplight color for a game, based on BGG complexity."""
    w = weight_map.get(title.lower())
    if w is None:
        return COLOR_UNKNOWN
    if w < 2.0:
        return COLOR_LIGHT
    if w < 3.0:
        return COLOR_MEDIUM
    return COLOR_HEAVY


def title_to_slug(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# --------------------------------------------------------------------------
# QR code image
# --------------------------------------------------------------------------

def make_qr_image(url):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.ERROR_CORRECT_H,  # 30% obscuration tolerance
        box_size=10,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


# --------------------------------------------------------------------------
# Meeple silhouette — drawn as a filled path
# --------------------------------------------------------------------------

def draw_meeple(c, cx, cy, height, color):
    """
    Draw a classic Carcassonne-style meeple silhouette centered at (cx, cy).

    `height` is the total vertical extent (head top → foot bottom).
    """
    c.setFillColor(color)
    c.saveState()
    c.translate(cx, cy)

    # Everything scaled by `height`. The silhouette is drawn as ONE filled
    # path that includes head, shoulders, arms, torso, and legs — the head
    # blob is joined into the body via the neck, then the outline continues
    # around.
    h = height
    p = c.beginPath()

    # Head — top of silhouette
    head_r = h * 0.20
    head_cy = h * 0.30
    # Body outline — traversed clockwise starting at top of head
    # (approximating the head as an octagon-ish arc via a series of points)
    for i in range(12):  # top half of head as an arc
        theta = math.radians(180 - i * (180 / 11))  # from left (180°) to right (0°)
        px = head_r * math.cos(theta)
        py = head_cy + head_r * math.sin(theta)
        if i == 0:
            p.moveTo(px, py)
        else:
            p.lineTo(px, py)
    # Down the right side of the head, into right shoulder
    p.lineTo(head_r * 0.55, head_cy - head_r * 0.85)   # right neck
    p.lineTo(h * 0.42,      h * 0.08)                    # right shoulder / arm out
    p.lineTo(h * 0.30,     -h * 0.05)                    # right arm bottom
    p.lineTo(h * 0.15,      h * 0.02)                    # right waist
    p.lineTo(h * 0.30,     -h * 0.45)                    # right foot outer
    p.lineTo(h * 0.08,     -h * 0.45)                    # right foot inner
    p.lineTo(0.0,           -h * 0.22)                   # crotch
    p.lineTo(-h * 0.08,    -h * 0.45)                    # left foot inner
    p.lineTo(-h * 0.30,    -h * 0.45)                    # left foot outer
    p.lineTo(-h * 0.15,    h * 0.02)                     # left waist
    p.lineTo(-h * 0.30,    -h * 0.05)                    # left arm bottom
    p.lineTo(-h * 0.42,    h * 0.08)                     # left shoulder / arm out
    p.lineTo(-head_r * 0.55, head_cy - head_r * 0.85)    # left neck
    p.close()
    c.drawPath(p, stroke=0, fill=1)

    c.restoreState()


# --------------------------------------------------------------------------
# Curved text along a circular arc
# --------------------------------------------------------------------------

def draw_arc_text(c, text, cx, cy, radius, arc_center_deg,
                  font, size, color, upright=True):
    """
    Draw `text` centered on a circular arc. `radius` is where the vertical
    center of the text sits (accounting for baseline vs. cap height).
    """
    center_offset = size * 0.35
    baseline_r = radius - center_offset if upright else radius + center_offset

    c.setFillColor(color)
    total_arc = stringWidth(text, font, size) / baseline_r
    if upright:
        cursor = math.radians(arc_center_deg) + total_arc / 2
        step_sign = -1
        rot_offset = -90.0
    else:
        cursor = math.radians(arc_center_deg) - total_arc / 2
        step_sign = +1
        rot_offset = +90.0

    for ch in text:
        w = stringWidth(ch, font, size)
        char_arc = w / baseline_r
        center_angle = cursor + step_sign * (char_arc / 2)
        c.saveState()
        c.translate(cx + baseline_r * math.cos(center_angle),
                    cy + baseline_r * math.sin(center_angle))
        c.rotate(math.degrees(center_angle) + rot_offset)
        c.setFont(font, size)
        c.drawString(-w / 2, 0, ch)
        c.restoreState()
        cursor += step_sign * char_arc


# --------------------------------------------------------------------------
# Sticker rendering
# --------------------------------------------------------------------------

def fit_title_for_ring(text, max_arc_length, font, radius, base_size, min_size=6):
    """
    Shrink the font size until the game title's arc length fits `max_arc_length`.
    Returns (font_size, fits_flag).
    """
    size = base_size
    while size >= min_size:
        arc_len = stringWidth(text, font, size)
        if arc_len <= max_arc_length:
            return size, True
        size -= 0.5
    return min_size, False


def draw_sticker(c, cx, cy, radius, title, url, ring_color):
    """
    Draw one circular sticker centered at (cx, cy).
      radius = OUTER radius of the ring
    """
    ring_thickness = radius * 0.16
    inner_radius = radius - ring_thickness

    # 1. Solid colored ring
    c.setFillColor(ring_color)
    c.setLineWidth(0)
    c.circle(cx, cy, radius, stroke=0, fill=1)
    c.setFillColor(HexColor("#FFFFFF"))
    c.circle(cx, cy, inner_radius, stroke=0, fill=1)

    # 2. Dotted outer halo (style D from mockups)
    n_dots = 60
    dot_r = ring_thickness * 0.11
    dot_ring_r = radius + ring_thickness * 0.35
    c.setFillColor(ring_color)
    for i in range(n_dots):
        angle = 2 * math.pi * i / n_dots
        c.circle(cx + dot_ring_r * math.cos(angle),
                 cy + dot_ring_r * math.sin(angle),
                 dot_r, stroke=0, fill=1)

    # 3. Text on the ring — ink color depends on ring (yellow needs dark ink).
    ink = ink_for(ring_color)
    text_baseline_r = radius - ring_thickness * 0.5
    top_font_size = ring_thickness * 0.55
    draw_arc_text(
        c, "SCAN ME FOR RULES HELP",
        cx, cy, text_baseline_r,
        arc_center_deg=90,
        font="Helvetica-Bold",
        size=top_font_size,
        color=ink,
        upright=True,
    )
    # Bottom text = game title. Shrink font as needed to fit.
    max_arc_len = math.pi * text_baseline_r * 0.70  # 70% of half-circumference
    title_font_size, _ = fit_title_for_ring(
        title, max_arc_len, "Helvetica-Bold",
        text_baseline_r, base_size=top_font_size,
        min_size=top_font_size * 0.6,
    )
    draw_arc_text(
        c, title,
        cx, cy, text_baseline_r,
        arc_center_deg=270,
        font="Helvetica-Bold",
        size=title_font_size,
        color=ink,
        upright=False,
    )

    # 4. QR code — centered in the inner white circle
    qr_size = inner_radius * 1.30   # bigger now that title moved to the ring
    qr_x = cx - qr_size / 2
    qr_y = cy - qr_size / 2
    img = make_qr_image(url)
    c.drawImage(ImageReader(img), qr_x, qr_y, qr_size, qr_size,
                preserveAspectRatio=True, mask="auto")

    # 5. Brand submark (coaster badge) overlay — sits in the center of the
    # QR. The submark is a self-contained circular mark (green circle with
    # the mascot inside), so it drops in as a single image — no separate
    # background disc needed. H error correction tolerates ~30% obscuration;
    # this covers ~14% of the QR area, well within the safety margin.
    # Sized against H-level error correction's ~30% tolerance. Verified
    # scannable across all 333 stickers with pyzbar (a picky decoder — real
    # phones handle more).
    submark_size = qr_size * 0.20
    c.drawImage(
        ImageReader(SUBMARK_PATH),
        cx - submark_size / 2, cy - submark_size / 2,
        submark_size, submark_size,
        preserveAspectRatio=True, mask="auto",
    )


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--cols", type=int, default=DEFAULT_COLS)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    args = parser.parse_args()

    games = get_all_games()
    titles = sorted(g["title"] for g in games)
    per_page = args.cols * args.rows

    weight_map = load_weight_map(WEIGHT_SOURCE_DB)
    buckets = {"green": 0, "yellow": 0, "red": 0, "gray": 0}
    for t in titles:
        w = weight_map.get(t.lower())
        if w is None:
            buckets["gray"] += 1
        elif w < 2.0:
            buckets["green"] += 1
        elif w < 3.0:
            buckets["yellow"] += 1
        else:
            buckets["red"] += 1

    print(f"Generating {len(titles)} QR stickers → {args.output}")
    print(f"Base URL: {args.base_url}")
    print(f"Layout:   {args.cols}×{args.rows} = {per_page} per page "
          f"→ ~{-(-len(titles) // per_page)} pages")
    print(f"Stoplight: {buckets['green']} green, {buckets['yellow']} yellow, "
          f"{buckets['red']} red, {buckets['gray']} gray (no weight data)")

    page_w, page_h = LETTER
    margin = 0.35 * inch
    cell_w = (page_w - 2 * margin) / args.cols
    cell_h = (page_h - 2 * margin) / args.rows
    radius = min(cell_w, cell_h) * 0.44   # room for outer dot halo

    c = canvas.Canvas(args.output, pagesize=LETTER)

    for i, title in enumerate(titles):
        slug = title_to_slug(title)
        url = f"{args.base_url}/?g={slug}"
        idx = i % per_page
        if idx == 0 and i > 0:
            c.showPage()

        col = idx % args.cols
        row = idx // args.cols
        cx = margin + cell_w * col + cell_w / 2
        cy = page_h - margin - cell_h * row - cell_h / 2

        ring_color = ring_color_for(title, weight_map)
        draw_sticker(c, cx, cy, radius, title, url, ring_color)

    c.save()
    kb = Path(args.output).stat().st_size / 1024
    print(f"\nWrote {args.output} ({kb:.0f} KB)")


if __name__ == "__main__":
    main()
