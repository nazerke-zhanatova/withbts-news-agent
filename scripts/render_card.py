"""Render the cover card (1080x1350 JPG) and carousel slides for every post in queue/.

Instagram only accepts JPG, and carousel images must be between 4:5 and 1.91:1,
so every slide is fitted onto a 1080x1350 canvas without cropping the photo.

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


def no_emoji(text):
    """Card fonts have no emoji: keep letters, digits and punctuation only."""
    import unicodedata
    out = "".join(ch for ch in (text or "") if unicodedata.category(ch)[0] in "LNPZ" or ch in "«»+%&")
    return " ".join(out.split())


def find_faces(photo):
    """Return face boxes (x, y, w, h) in photo pixels, or [] if none or OpenCV is missing."""
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    gray = cv2.cvtColor(np.array(photo), cv2.COLOR_RGB2GRAY)
    faces = []
    for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        try:
            det = cv2.CascadeClassifier(cv2.data.haarcascades + name)
            if det.empty():
                continue
            min_side = max(40, min(photo.size) // 12)
            found = det.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
            faces += [tuple(int(v) for v in f) for f in found]
        except Exception:  # noqa: BLE001
            continue
    return faces


def face_safe_fit(photo, size):
    """Fit the photo into `size` without ever cutting a face.

    Face detection misses faces with masks, sunglasses or side views, so we never trust it
    for cropping. Only when the photo already has almost the same shape (within 6%) is a tiny
    trim allowed. Otherwise the whole photo is shown on a blurred copy of itself.
    """
    from PIL import ImageFilter

    tw, th = size
    if abs((photo.width / photo.height) / (tw / th) - 1) <= 0.06:
        return ImageOps.fit(photo, size, Image.LANCZOS, centering=(0.5, 0.5))
    bg = ImageOps.fit(photo, size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(30))
    bg = Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), 0.25)
    fg = ImageOps.contain(photo, size, Image.LANCZOS)
    bg.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
    return bg


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
        photo = face_safe_fit(photo, (W, PHOTO_H))
        img.paste(photo, (0, 0))
        # fade photo into the lavender background
        fade = Image.new("L", (1, 240))
        for yy in range(240):
            fade.putpixel((0, yy), int(255 * (yy / 239) ** 1.6))
        fade = fade.resize((W, 240))
        img.paste(Image.new("RGB", (W, 240), bg), (0, PHOTO_H - 240), fade)
        # photo credit on the image, no "@" so nobody is mis-tagged
        owner = (post.get("image_credit") or (post.get("credits") or [""])[0]).strip().lstrip("@")
        credit = f"photo: {owner}"
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

    card = {k: no_emoji(v) if isinstance(v, str) else v for k, v in post.get("card", {}).items()}

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

    img.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


def render_slide(src, out_path):
    """Fit a photo on a 1080x1350 canvas (whole photo visible) over a blurred copy of itself."""
    from PIL import ImageFilter

    photo = Image.open(src).convert("RGB")
    bg = ImageOps.fit(photo, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)
    fg = ImageOps.contain(photo, (W, H), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    bg.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


def _allowed(url, domains):
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return url.startswith("https://") and any(host == d or host.endswith("." + d) for d in domains)


def download_photo(url, out_path, domains):
    """Download a photo the writer found on the internet (CLAUDE.md rule 5d). Returns the path or None."""
    import io
    import requests
    if not url or not _allowed(url, domains):
        log(f"  photo url not allowed or empty: {url}")
        return None
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 withBTS.kz news agent"})
        r.raise_for_status()
        if len(r.content) > 15_000_000:
            raise ValueError("file too large")
        img = Image.open(io.BytesIO(r.content))
        img = ImageOps.exif_transpose(img).convert("RGB")
        if min(img.size) < 400:
            raise ValueError(f"photo too small {img.size}")
        img.save(out_path, "JPEG", quality=95)
        log(f"  photo downloaded: {url} -> {out_path.name}")
        return out_path
    except Exception as e:  # noqa: BLE001
        log(f"  photo download failed ({e}): {url}")
        return None


def download_video(url, out_path, domains, max_mb=48):
    """Download a video clip (CLAUDE.md rule 5c). Telegram bots can send up to 50 MB."""
    import requests
    if not url or not _allowed(url, domains):
        log(f"  video url not allowed or empty: {url}")
        return None
    try:
        with requests.get(url, timeout=120, stream=True, headers={"User-Agent": "Mozilla/5.0 withBTS.kz news agent"}) as r:
            r.raise_for_status()
            size = 0
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    size += len(chunk)
                    if size > max_mb * 1024 * 1024:
                        raise ValueError(f"video larger than {max_mb} MB")
                    f.write(chunk)
        log(f"  video downloaded: {out_path.name} ({size // 1024} KB)")
        return out_path
    except Exception as e:  # noqa: BLE001
        log(f"  video download failed ({e}): {url}")
        if out_path.exists():
            out_path.unlink()
        return None


def telegram_media(url):
    """Photos, video still frames and video links of one public Telegram post (t.me/<channel>/<id>)."""
    import requests
    try:
        r = requests.get(url.split("?")[0] + "?embed=1&mode=tme", timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 withBTS.kz news agent"})
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log(f"  telegram post not readable ({e}): {url}")
        return [], []
    html = r.text
    import re as _re
    images = _re.findall(r"(?:tgme_widget_message_photo_wrap|tgme_widget_message_video_thumb)[^>]*background-image:url\('([^']+)'\)", html)
    videos = _re.findall(r"<video[^>]+src=\"(https://[^\"]+)\"", html)
    return images, videos


def video_frame(video_path, out_path, at=0.35):
    """Save one sharp frame of a video as a JPG (Telegram's own still frames are tiny)."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if n > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * at))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return None
        cv2.imwrite(str(out_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        log(f"  frame taken from video: {out_path.name}")
        return out_path
    except Exception as e:  # noqa: BLE001
        log(f"  could not take a frame from the video ({e})")
        return None


def make_collage(paths, out_path):
    """One cover from several photos (for example one photo per member), nothing cropped away badly:
    each photo is fitted into its own tile."""
    imgs = [Image.open(ROOT / p).convert("RGB") for p in paths]
    n = len(imgs)
    cols = 4 if n > 6 else 3 if n > 4 else n
    rows = (n + cols - 1) // cols
    tw, th = 1080 // cols, 1350 // rows
    canvas = Image.new("RGB", (tw * cols, th * rows), (20, 12, 30))
    for i, im in enumerate(imgs):
        r, c = divmod(i, cols)
        offset = (cols - (n - r * cols)) * tw // 2 if r == rows - 1 and n % cols else 0
        tile = ImageOps.fit(im, (tw - 6, th - 6), Image.LANCZOS, centering=(0.5, 0.3))
        canvas.paste(tile, (c * tw + 3 + offset, r * th + 3))
    canvas.save(out_path, "JPEG", quality=92)
    return out_path


def fetch_remote_photos(post):
    """Turn image_url / carousel_urls into files in media/. Falls back to the official group photo."""
    s = settings()
    domains = [d.lower() for d in s.get("photo_domains", [])]
    pid = post["id"]
    # Telegram posts given by link: take their video and still frames (CLAUDE.md rule 5c)
    if post.get("tg_video_from") and not post.get("video_url"):
        imgs, vids = telegram_media(post["tg_video_from"])
        if vids:
            post["video_url"] = vids[0]
        if imgs and not post.get("image_url") and not post.get("image"):
            post["image_url"] = imgs[0]
    if post.get("tg_photos_from") and not post.get("carousel"):
        s0 = settings()
        doms = [d.lower() for d in s0.get("photo_domains", [])]
        frames, urls = [], []
        for n, link in enumerate(post["tg_photos_from"][:9]):
            imgs, vids = telegram_media(link)
            if vids:  # a clip: take a full-size frame from the video itself
                tmp = download_video(vids[0], MEDIA / f"tmp_{pid}_{n}.mp4", doms)
                if tmp:
                    fr = video_frame(tmp, MEDIA / f"web_{pid}_f{n}.jpg")
                    tmp.unlink()
                    if fr:
                        frames.append(f"media/{fr.name}")
                        continue
            urls += imgs[:1]
        post["carousel"] = frames
        if urls:
            post["carousel_urls"] = urls
    has_local = post.get("image") and (ROOT / post["image"]).exists()
    if not has_local and post.get("image_url"):
        got = download_photo(post["image_url"], MEDIA / f"web_{pid}_cover.jpg", domains)
        if got:
            post["image"] = f"media/{got.name}"
    # No fallback photo: the same photo must never be used twice (Nazerke's rule).
    if post.get("video_url") and not (post.get("video_file") and (ROOT / post["video_file"]).exists()):
        got = download_video(post["video_url"], MEDIA / f"video_{pid}.mp4", domains)
        if got:
            post["video_file"] = f"media/{got.name}"
    if post.get("video_file") and (ROOT / post["video_file"]).exists() and not (post.get("image") and (ROOT / post["image"]).exists()):
        fr = video_frame(ROOT / post["video_file"], MEDIA / f"web_{pid}_frame.jpg")
        if fr:
            post["image"] = f"media/{fr.name}"
    carousel = list(post.get("carousel") or [])
    for n, url in enumerate((post.get("carousel_urls") or [])[:9]):
        if f"media/web_{pid}_{n}.jpg" in carousel and (MEDIA / f"web_{pid}_{n}.jpg").exists():
            continue  # already downloaded (card is being rendered again)
        got = download_photo(url, MEDIA / f"web_{pid}_{n}.jpg", domains)
        if got:
            carousel.append(f"media/{got.name}")
    post["carousel"] = carousel[:9]
    if post.get("cover_collage") and len(post["carousel"]) >= 2 and not (post.get("image") and (ROOT / post["image"]).exists()):
        out = make_collage(post["carousel"], MEDIA / f"web_{pid}_collage.jpg")
        post["image"] = f"media/{out.name}"
        log(f"  cover collage made from {len(post['carousel'])} photos")


def main(paths):
    files = [Path(p) for p in paths] or sorted(QUEUE.glob("*.json"))
    MEDIA.mkdir(exist_ok=True)
    for f in files:
        post = load_json(f, None)
        if not post:
            continue
        fetch_remote_photos(post)
        out = MEDIA / f"card_{post['id']}.jpg"
        render(post, out)
        post["card_file"] = f"media/{out.name}"
        slides = []
        for n, src in enumerate((post.get("carousel") or [])[:9]):
            src_path = (ROOT / src).resolve()
            if not str(src_path).startswith(str(MEDIA.resolve())) or not src_path.exists():
                log(f"  carousel photo skipped (not in media/): {src}")
                continue
            slide = MEDIA / f"slide_{post['id']}_{n}.jpg"
            try:
                render_slide(src_path, slide)
                slides.append(f"media/{slide.name}")
            except Exception as e:  # noqa: BLE001
                log(f"  carousel photo skipped ({e}): {src}")
        post["carousel_files"] = slides
        save_json(f, post)
        log(f"Card rendered: {out.name}" + (f" + {len(slides)} carousel photos" if slides else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
