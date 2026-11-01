"""Render the Instagram card (1080x1350 PNG) for every post in queue/.

Usage: python scripts/render_card.py            (all queue files without a card)
       python scripts/render_card.py queue/x.json (one file)
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from common import MEDIA, QUEUE, ROOT, load_json, log, now_almaty, save_json, settings

W, H = 1080, 1350
PAD = 72
PHOTO_H = 760

FONT_CANDIDATES = [
    Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]
FONT_REGULAR_CANDIDATES = [
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


def _font(size, bold=True):
    for p in FONT_CANDIDATES if bold else FONT_REGULAR_CANDIDATES:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def wrap(draw, text, font, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=font) <= max_w:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def fit_text(draw, text, max_w, max_lines, start, minimum, bold=True):
    size = start
    while size >= minimum:
        f = _font(size, bold)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        size -= 4
    f = _font(minimum, bold)
    lines = wrap(draw, text, f, max_w)[:max_lines]
    if lines:
        lines[-1] = lines[-1].rstrip(".,") + "..."
    return f, lines


def _mix(a, b, t):
    return tuple(int(x * t + y * (1 - t)) for x, y in zip(a, b))


def _load_logo(rel):
    if rel and (ROOT / rel).exists():
        return Image.open(ROOT / rel).convert("RGBA")
    return None


def render(post, out_path):
    s = settings()
    d = s.get("design", {})
    bg = _hex(d.get("background", "#CCAAFF"))
    hl = _hex(d.get("highlight", "#FFC91D"))
    fg = _hex(d.get("text", "#111111"))
    muted = _hex(d.get("muted", "#2B1A45"))
    heart = _load_logo(d.get("logo_file"))
    wordmark = _load_logo(d.get("wordmark_file"))

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    photo_path = post.get("image")
    has_photo = bool(photo_path) and (ROOT / photo_path).exists()

    if has_photo:
        photo = Image.open(ROOT / photo_path).convert("RGB")
        photo = ImageOps.fit(photo, (W, PHOTO_H), Image.LANCZOS, centering=(0.5, 0.35))
        img.paste(photo, (0, 0))
        # fade photo into the lavender background
        fade = Image.new("L", (1, 240))
        for yy in range(240):
            fade.putpixel((0, yy), int(255 * (yy / 239) ** 1.6))
        fade = fade.resize((W, 240))
        img.paste(Image.new("RGB", (W, 240), bg), (0, PHOTO_H - 240), fade)
        # photo credit on the image, no "@" so nobody is mis-tagged
        names = ", ".join(dict.fromkeys(c.strip().lstrip("@") for c in post.get("credits", []) if c.strip()))
        credit = f"photo: {names}"
        cf = _font(24, bold=False)
        tw = draw.textlength(credit, font=cf)
        draw.rounded_rectangle((W - PAD - tw - 24, 28, W - PAD + 4, 72), radius=20, fill=(17, 17, 17))
        draw.text((W - PAD - tw - 10, 36), credit, font=cf, fill=(255, 255, 255))
        y = PHOTO_H - 30
    else:
        # text only card: yellow sun, soft lavender circle and the heart logo
        draw.ellipse((-260, 420, 420, 1100), fill=_mix((255, 255, 255), bg, 0.25))
        draw.ellipse((W - 330, 70, W - 110, 290), fill=hl)
        if heart:
            h = heart.copy()
            h.thumbnail((480, 480))
            h = h.rotate(-8, expand=True, resample=Image.BICUBIC)
            img.paste(h, (W - h.width - 40, 110), h)
        y = 620

    card = post.get("card", {})

    # tag pill in the highlight yellow
    tag = (card.get("tag") or "ЖАҢАЛЫҚ").upper()
    tf = _font(30)
    tw = draw.textlength(tag, font=tf)
    draw.rounded_rectangle((PAD, y, PAD + tw + 48, y + 58), radius=29, fill=hl, outline=fg, width=3)
    draw.text((PAD + 24, y + 11), tag, font=tf, fill=fg)
    y += 96

    fy = H - 120  # footer line position
    bottom = fy - 36
    sub = card.get("subline", "")

    # shrink headline and subline together until both fit above the footer
    for scale in (1.0, 0.92, 0.85, 0.78, 0.72, 0.66):
        hf, hlines = fit_text(draw, card.get("headline", ""), W - 2 * PAD, 3, int(76 * scale), int(48 * scale))
        sf, slines = (fit_text(draw, sub, W - 2 * PAD, 3, int(38 * scale), int(28 * scale), bold=False)
                      if sub else (None, []))
        need = len(hlines) * int(hf.size * 1.18) + 18 + (len(slines) * int(sf.size * 1.35) if sf else 0)
        if y + need <= bottom:
            break

    for line in hlines:
        draw.text((PAD, y), line, font=hf, fill=fg)
        y += int(hf.size * 1.18)
    y += 18
    for line in slines:
        draw.text((PAD, y), line, font=sf, fill=muted)
        y += int(sf.size * 1.35)

    # footer: heart + wordmark on the left, date on the right
    draw.line((PAD, fy, W - PAD, fy), fill=fg, width=3)
    bx = PAD
    if heart and has_photo:
        h = heart.copy()
        h.thumbnail((66, 66))
        img.paste(h, (bx, fy + 24), h)
        bx += h.width + 18
    if wordmark:
        wm = wordmark.copy()
        wm.thumbnail((360, 72))
        img.paste(wm, (bx, fy + 22 + (72 - wm.height) // 2), wm)
    else:
        draw.text((bx, fy + 36), d.get("brand_name", "withBTS.kz"), font=_font(36), fill=fg)
    date = now_almaty().strftime("%d.%m.%Y")
    df = _font(30, bold=False)
    draw.text((W - PAD - draw.textlength(date, font=df), fy + 42), date, font=df, fill=muted)

    img.save(out_path, "PNG", optimize=True)
    return out_path


def main(paths):
    files = [Path(p) for p in paths] or sorted(QUEUE.glob("*.json"))
    for f in files:
        post = load_json(f, None)
        if not post:
            continue
        out = MEDIA / f"card_{post['id']}.png"
        MEDIA.mkdir(exist_ok=True)
        render(post, out)
        post["card_file"] = f"media/{out.name}"
        save_json(f, post)
        log(f"Card rendered: {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
