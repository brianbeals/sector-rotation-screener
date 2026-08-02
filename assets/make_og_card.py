#!/usr/bin/env python3
"""Build the Open Graph social card for sector.brianbeals.com.

Geometry is measured from harbor-spots/og-card.png rather than guessed, so the
three project cards read as one family in a link preview: navy panel ending at
x=498, BB mark 113px square at (64,56), accent rule 163x9 at (64,434).

Run this when the visual on the right needs refreshing. The card is committed,
not built in CI -- it is a brand asset, not a data artifact, and baking a live
number into it would make every stale share a wrong claim.
"""
import sys
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
NAVY, BLUE, WHITE = (30, 58, 95), (46, 134, 193), (255, 255, 255)
SUB = (198, 213, 230)
PANEL = 498          # navy panel ends here, matching harbor-spots
FADE = 46            # soft blend so the screenshot doesn't hard-edge

F = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf"
def font(sz, bold=True): return ImageFont.truetype(F % ("-Bold" if bold else ""), sz)

def build(visual_path, out_path):
    card = Image.new("RGB", (W, H), NAVY)

    # Right side: FIT the whole visual to the panel width, do not cover-crop it.
    # Cover-cropping a data table zooms until only five columns survive, and five
    # huge cells read as a spreadsheet. The heatmap only works as a signal when the
    # whole grid is visible and the colour becomes texture. Letterboxing into navy
    # is the cost, and navy is the background anyway so it reads as intentional.
    # Fit to WIDTH so every column of the grid survives, then take the vertical
    # slice that fills the card. That means the source has to be taller than one
    # table: it is the heatmap plus the relative-strength chart beneath it. A
    # single table fitted to width leaves a floating band with navy above and
    # below, which looks like a mistake rather than a choice.
    vis = Image.open(visual_path).convert("RGB")
    tw = W - PANEL + FADE
    vh = max(1, round(vis.height * tw / vis.width))
    vis = vis.resize((tw, vh), Image.LANCZOS)
    if vh >= H:
        vis = vis.crop((0, (vh - H) // 2, tw, (vh - H) // 2 + H))
        card.paste(vis, (PANEL - FADE, 0))
    else:
        card.paste(vis, (PANEL - FADE, (H - vh) // 2))

    # feather the seam back into the navy
    seam = Image.new("RGBA", (FADE, H), NAVY + (255,))
    px = seam.load()
    for x in range(FADE):
        a = int(255 * (1 - x / FADE) ** 0.85)
        for y in range(H):
            px[x, y] = NAVY + (a,)
    card.paste(Image.new("RGB", (PANEL - FADE, H), NAVY), (0, 0))
    card.paste(seam, (PANEL - FADE, 0), seam)

    d = ImageDraw.Draw(card)

    # BB mark
    d.rectangle([64, 56, 64 + 112, 56 + 112], fill=WHITE)
    fbb = font(66)
    bb = d.textbbox((0, 0), "BB", font=fbb)
    d.text((64 + (113 - (bb[2] - bb[0])) / 2 - bb[0],
            56 + (113 - (bb[3] - bb[1])) / 2 - bb[1]), "BB", font=fbb, fill=NAVY)

    # Title and subtitle are measured against the panel, not eyeballed. The first
    # cut used harbor's 78px and "Rotation" plus the subtitle ran out over the
    # heatmap, because "Harbor Spots" is a short phrase and this one is not.
    MAXW = PANEL - 64 - 28
    def fit(lines, start, bold=True, cap=78, floor=40):
        sz = cap
        while sz > floor:
            f = font(sz, bold)
            if all(d.textlength(l, font=f) <= MAXW for l in lines):
                return f
            sz -= 2
        return font(floor, bold)

    title = ["Sector", "Rotation"]
    ft = fit(title, 238)
    for i, l in enumerate(title):
        d.text((64, 238 + i * int(ft.size * 1.16)), l, font=ft, fill=WHITE)

    # accent rule
    d.rectangle([64, 434, 64 + 162, 434 + 8], fill=BLUE)

    # subtitle, wrapped to measured width
    words = "11 SPDR sector ETFs, scored weekly on seasonality, cycle fit, and relative strength".split()
    fs = font(25, bold=False)
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=fs) <= MAXW: cur = t
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    for i, line in enumerate(lines):
        d.text((64, 466 + i * 32), line, font=fs, fill=SUB)

    # domain
    d.text((64, 578), "sector.brianbeals.com", font=font(25), fill=BLUE)

    card.save(out_path, "PNG", optimize=True)
    print(f"wrote {out_path} {card.size}")

if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "og-card.png")
