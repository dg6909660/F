import os, re, io, html, zlib, random
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageOps
import studio as S
import fx

GROUP = []
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
TAGS = {"fear": "WARNING", "greed": "OPPORTUNITY?", "shock": "BREAKING", "calm": "EXPLAINED"}


# ---------------- find real photos in the RSS entries ----------------
def entry_image(e):
    for k in ("media_content", "media_thumbnail"):
        for m in e.get(k, []) or []:
            if m.get("url"):
                return m["url"]
    for enc in e.get("enclosures", []) or []:
        if "image" in (enc.get("type") or ""):
            return enc.get("href") or enc.get("url")
    for l in e.get("links", []) or []:
        if l.get("rel") == "enclosure" and "image" in (l.get("type") or ""):
            return l.get("href")
    blob = " ".join([e.get("summary", "") or ""] + [c.get("value", "") for c in e.get("content", []) or []])
    m = re.search(r'<img[^>]+src=["\']([^"\']+)', blob)
    return m.group(1) if m else None


def norm(t):
    return re.sub(r"\W+", "", html.unescape(t or "").strip().lower())


def find_photos(group, maxn=2):
    import brain, feedparser
    order = [norm(a["title"]) for a in group]
    found = {}
    for u in brain.FEEDS:
        try:
            f = feedparser.parse(u)
        except Exception:
            continue
        for e in f.entries:
            k = norm(e.get("title", ""))
            if k in order and k not in found:
                url = entry_image(e)
                if url:
                    found[k] = url
    imgs = []
    for k in order:
        if k not in found:
            continue
        try:
            r = requests.get(found[k], headers=UA, timeout=15)
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            if im.width >= 300 and im.height >= 160:
                imgs.append(im)
        except Exception as ex:
            print("photo download failed:", ex)
        if len(imgs) >= maxn:
            break
    print("news photos found:", len(imgs))
    return imgs


# ---------------- helpers ----------------
def grade(p, acc, W, H, tint=0.0):
    im = ImageOps.fit(p, (W, H), Image.LANCZOS, centering=(0.5, 0.4))
    im = ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.25)).enhance(1.15)
    return Image.blend(im, Image.new("RGB", im.size, acc), tint) if tint else im


def shade(W, H, left=205, bottom=150):
    yy, xx = np.mgrid[0:H, 0:W]
    a = np.maximum(np.clip(1 - xx / (0.62 * W), 0, 1) ** 1.2 * left, np.clip((yy / H - 0.55) / 0.45, 0, 1) * bottom)
    return Image.fromarray(np.dstack([np.zeros((H, W, 3), np.uint8), a.astype(np.uint8)]), "RGBA")


def glow(W, H, cx, cy, rad, acc, al=140):
    yy, xx = np.mgrid[0:H, 0:W]
    a = (np.clip(1 - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad, 0, 1) ** 2 * al).astype(np.uint8)
    return Image.fromarray(np.dstack([np.full((H, W), c, np.uint8) for c in acc] + [a]), "RGBA")


def tag(d, W, H, text, x, y):
    f = S.font(H * 0.05)
    w, h = int(f.getlength(text)) + 40, int(H * 0.085)
    d.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=(220, 30, 40), outline=(255, 255, 255), width=3)
    d.text((x + 20, y + h * 0.16), text, font=f, fill=(255, 255, 255))


def finish(im):
    im = ImageEnhance.Color(ImageEnhance.Contrast(im).enhance(1.08)).enhance(1.1)
    return im.filter(ImageFilter.UnsharpMask(1.2, 60, 3))


# ---------------- layouts ----------------
def lay_a(im, d, p, sp, acc, r, W, H):
    im.paste(grade(p, acc, W, H, 0.10))
    for ov in (shade(W, H), glow(W, H, int(W * .78), int(H * .55), int(H * .75), acc)):
        im.paste(ov, (0, 0), ov)
    b = "CRASH" if sp["mood"] in ("fear", "shock") else "BULL"
    h = int(H * .86)
    fx.put(im, fx.face("GOLDIE", int(h * .88), "calm"), int(W * .64), H, int(h * .88))
    fx.put(im, fx.face(b, h, fx.emo_for(sp, b)), int(W * .88), H, h)
    tag(d, W, H, TAGS[sp["mood"]], int(W * .04), int(H * .05))
    fx.fit_text(d, sp["headline"], (int(W * .04), int(H * .24), int(W * .58), int(H * .92)), sp["highlight"], acc, maxl=4)


def lay_b(im, d, p, sp, acc, r, W, H):
    im.paste(fx.tbg(W, H, acc, r))
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).polygon([(0, 0), (int(W * .62), 0), (int(W * .50), H), (0, H)], fill=255)
    im.paste(grade(p, acc, W, H), (0, 0), m.filter(ImageFilter.GaussianBlur(1)))
    d.line([(int(W * .62), 0), (int(W * .50), H)], fill=acc, width=14)
    cx, cy, rx, ry = int(W * r.uniform(.18, .34)), int(H * r.uniform(.3, .6)), int(W * .11), int(H * .17)
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=acc, width=10)
    b = "CRASH" if sp["mood"] in ("fear", "shock") else "BULL"
    fx.put(im, fx.face("GOLDIE", int(H * .48), "calm"), int(W * .72), H, int(H * .48))
    fx.put(im, fx.face(b, int(H * .56), fx.emo_for(sp, b)), int(W * .90), H, int(H * .56))
    fx.fit_text(d, sp["headline"], (int(W * .60), int(H * .04), int(W * .98), int(H * .46)), sp["highlight"], acc, maxl=4, top=110)
    tag(d, W, H, TAGS[sp["mood"]], int(W * .03), int(H * .04))


def lay_c(im, d, p, sp, acc, r, W, H):
    bg = ImageEnhance.Brightness(grade(p, acc, W, H, 0.35).filter(ImageFilter.GaussianBlur(10))).enhance(0.55)
    im.paste(bg)
    h = int(H * .84)
    fx.put(im, fx.face("BULL", h, fx.emo_for(sp, "BULL")), int(W * .17), H, h)
    fx.put(im, fx.face("CRASH", h, fx.emo_for(sp, "CRASH")), int(W * .83), H, h)
    R_ = int(H * .20)
    cx, cy = W // 2, int(H * .56)
    ph = ImageOps.fit(p, (2 * R_, 2 * R_), Image.LANCZOS)
    m = Image.new("L", (2 * R_, 2 * R_), 0)
    ImageDraw.Draw(m).ellipse((0, 0, 2 * R_ - 1, 2 * R_ - 1), fill=255)
    d.ellipse((cx - R_ - 10, cy - R_ - 10, cx + R_ + 10, cy + R_ + 10), fill=acc)
    im.paste(ph, (cx - R_, cy - R_), m)
    fx.badge(d, cx + R_ - 10, cy - R_ + 10, int(H * .08), sp["badge"], acc)
    fx.fit_text(d, sp["headline"], (int(W * .04), int(H * .02), int(W * .96), int(H * .30)), sp["highlight"], acc, maxl=2)


def lay_d(im, d, p, sp, acc, r, W, H):
    im.paste(fx.tbg(W, H, acc, r))
    pw, ph_ = int(W * .46), int(H * .50)
    frame = Image.new("RGBA", (pw + 28, ph_ + 28), (245, 242, 235, 255))
    frame.paste(ImageOps.fit(p, (pw, ph_), Image.LANCZOS), (14, 14))
    cv = Image.new("RGBA", (frame.width + 60, frame.height + 60), (0, 0, 0, 0))
    sh = Image.new("RGBA", cv.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 170), (38, 44, 38 + frame.width, 44 + frame.height))
    cv.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)))
    cv.alpha_composite(frame, (30, 30))
    tape = Image.new("RGBA", (int(pw * .3), int(H * .05)), (255, 225, 90, 175))
    cv.alpha_composite(tape, (cv.width // 2 - tape.width // 2, 14))
    cv = cv.rotate(-5, expand=True, resample=Image.BICUBIC)
    im.paste(cv, (int(W * .02), int(H * .24)), cv)
    b = "CRASH" if sp["mood"] in ("fear", "shock") else "BULL"
    fx.put(im, fx.face("GOLDIE", int(H * .62), "calm"), int(W * .70), H, int(H * .62))
    fx.put(im, fx.face(b, int(H * .70), fx.emo_for(sp, b)), int(W * .90), H, int(H * .70))
    fx.fit_text(d, sp["headline"], (int(W * .04), int(H * .02), int(W * .96), int(H * .24)), sp["highlight"], acc, maxl=2, top=110)


PL = {"a": lay_a, "b": lay_b, "c": lay_c, "d": lay_d}


def thumbnail(title, out, W=1280, H=720, layout=None, photos=None):
    if photos is None:
        photos = [] if os.getenv("USE_NEWS_PHOTOS", "1") == "0" else find_photos(GROUP)
    if not photos:
        return fx.thumbnail(title, out, W, H, layout if layout in fx.LAY else None)
    seed = zlib.crc32(title.encode())
    r = random.Random(seed)
    sp = fx.thumb_spec(title, r, seed)
    acc = fx.hexrgb(str(sp.get("accent", ""))) or r.choice(fx.PAL)
    if max(acc) < 120:
        acc = r.choice(fx.PAL)
    lay = layout if layout in PL else "abcd"[seed % 4]
    im = Image.new("RGB", (W, H))
    PL[lay](im, ImageDraw.Draw(im), photos[0], sp, acc, r, W, H)
    finish(im).save(out, quality=92)
    return out


def install():
    import brain
    orig = brain.pick_topic

    def pick(*a, **k):
        res = orig(*a, **k)
        GROUP[:] = res[2]
        return res
    brain.pick_topic = pick
    S.thumbnail = thumbnail
