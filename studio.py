import os, re, sys, glob, math, wave, random, shutil, subprocess, urllib.request
import numpy as np
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from scipy import ndimage
from multiprocessing import Pool
from concurrent.futures import ThreadPoolExecutor

ORDER = ["BULL", "GOLDIE", "CASH", "CRASH"]
COL = {"BULL": (46, 204, 113), "CRASH": (231, 60, 60), "GOLDIE": (255, 200, 40), "CASH": (235, 235, 240)}
BODY = {"BULL": (40, 160, 70), "CRASH": (190, 40, 45), "GOLDIE": (230, 180, 30), "CASH": (235, 235, 235)}
# one fixed voice per character -> same voice in every video
VOICES = {"BULL": "en/en_US/ryan/medium/en_US-ryan-medium",
          "CRASH": "en/en_GB/alan/medium/en_GB-alan-medium",
          "GOLDIE": "en/en_US/amy/medium/en_US-amy-medium",
          "CASH": "en/en_US/joe/medium/en_US-joe-medium"}
MOUTH = (0.33, 0.235, 0.20, 0.055)   # x, y, width, height (fraction of sprite) -> tune if mouth looks off
UPPER = 0.56
RATE = 22050
PAUSE = 0.28
UP = ("rise", "rose", "surge", "rally", "gain", "jump", "soar", "growth", "record high", "bull", "climb", "opportunit", "upside")
DN = ("fall", "fell", "drop", "plunge", "crash", "slump", "decline", "loss", "sell-off", "selloff", "bear", "tumble", "risk", "danger")
STAT = re.compile(r"(\$\s?\d[\d,.]*(?:\s?(?:trillion|billion|million|thousand))?|\d[\d,.]*\s?(?:%|percent))", re.I)


@lru_cache(maxsize=None)
def font(sz, bold=True):
    p = "/usr/share/fonts/truetype/dejavu/DejaVuSans%s.ttf" % ("-Bold" if bold else "")
    try:
        return ImageFont.truetype(p, max(8, int(sz)))
    except Exception:
        return ImageFont.load_default()


def wrap(txt, f, maxw):
    out, cur = [], ""
    for w in txt.split():
        t = (cur + " " + w).strip()
        if f.getlength(t) <= maxw:
            cur = t
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def direction(t):
    l = t.lower()
    u, d = sum(k in l for k in UP), sum(k in l for k in DN)
    return 1 if u > d else -1 if d > u else 0


# ---------------- characters ----------------
def fallback(nm):
    im = Image.new("RGBA", (430, 1325), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((20, 330, 410, 1325), radius=40, fill=(30, 45, 90, 255))
    d.ellipse((0, 0, 300, 300), fill=BODY[nm] + (255,), outline=(0, 0, 0, 255), width=5)
    for ex in (60, 170):
        d.ellipse((ex, 70, ex + 60, 130), fill=(255, 255, 255, 255))
        d.ellipse((ex + 18, 88, ex + 42, 114), fill=(0, 0, 0, 255))
    d.rounded_rectangle((70, 160, 215, 190), radius=14, fill=(150, 80, 50, 255))
    return im


@lru_cache(maxsize=None)
def cutout(nm):
    fs = glob.glob(f"assets/{nm.lower()}.*")
    if not fs:
        print("missing image for", nm, "-> fallback drawing")
        return fallback(nm)
    im = Image.open(fs[0]).convert("RGB")
    a = np.array(im)
    lab, _ = ndimage.label(a.min(axis=2) > 232)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(border))
    alpha = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    bb = alpha.point(lambda v: 255 if v > 20 else 0).getbbox()
    return out.crop(bb) if bb else out


def make_sprite(nm, ch):
    c = cutout(nm)
    c = c.crop((0, 0, c.width, int(c.height * UPPER)))
    return c.resize((max(1, int(c.width * ch / c.height)), ch), Image.LANCZOS)


def mouth(sp, e):
    if e < 0.06:
        return sp
    im = sp.copy()
    d = ImageDraw.Draw(im)
    w, h = im.size
    cx, cy = MOUTH[0] * w, MOUTH[1] * h
    mw, mh = MOUTH[2] * w * (0.8 + 0.4 * e), MOUTH[3] * h * e + 2
    d.ellipse((cx - mw / 2, cy - mh / 2, cx + mw / 2, cy + mh / 2), fill=(45, 10, 14, 255))
    return im


# ---------------- layout / background ----------------
def layout(W, H):
    if W >= H:
        ch, by = int(H * 0.48), int(H * 0.66)
        return {n: (int(W * x), by, ch) for n, x in zip(ORDER, (0.14, 0.38, 0.62, 0.86))}
    ch = int(H * 0.20)
    return {"BULL": (int(W * .27), int(H * .43), ch), "CRASH": (int(W * .73), int(H * .43), ch),
            "GOLDIE": (int(W * .27), int(H * .67), ch), "CASH": (int(W * .73), int(H * .67), ch)}


def zones(W, H):
    if W >= H:
        return {"stat": (int(.03 * W), int(.065 * H), int(.20 * W), int(.13 * H)),
                "chart": (int(.28 * W), int(.065 * H), int(.22 * W), int(.13 * H)),
                "src": (int(.56 * W), int(.065 * H), int(.41 * W), int(.13 * H))}
    return {"stat": (int(.04 * W), int(.05 * H), int(.44 * W), int(.075 * H)),
            "chart": (int(.52 * W), int(.05 * H), int(.44 * W), int(.075 * H)),
            "src": (int(.04 * W), int(.135 * H), int(.92 * W), int(.06 * H))}


def init(cfg):
    global C, SEAT, SPR, BG, TABLE, PLATE, TICK, TICK_W, BADGE, PH, TH
    C = cfg
    W, H = cfg["W"], cfg["H"]
    SEAT = layout(W, H)
    SPR = {n: make_sprite(n, SEAT[n][2]) for n in ORDER}
    PH = {n: i * 1.7 for i, n in enumerate(ORDER)}
    TH = int(min(W, H) * 0.075)
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(14 + 16 * t), int(18 + 24 * t), int(40 + 36 * t)))
    if W >= H:
        f = font(H * 0.06)
        tx = "BULL  vs  CRASH"
        d.text(((W - f.getlength(tx)) / 2, H * 0.085), tx, font=f, fill=(38, 54, 90))
    for n, (cx, by, ch) in SEAT.items():
        cw = ch * 0.40
        d.rounded_rectangle((cx - cw, by - ch * 0.78, cx + cw, by + TH), radius=30, fill=(26, 30, 48))
    BG = im
    TABLE, PLATE = [], {}
    tx0 = int(W * 0.02)
    for by in sorted(set(s[1] for s in SEAT.values())):
        w = int(W * 0.96)
        strip = Image.new("RGBA", (w, TH), (0, 0, 0, 0))
        sd = ImageDraw.Draw(strip)
        sd.rounded_rectangle((0, 0, w - 1, TH - 1), radius=int(TH * .35), fill=(96, 62, 40, 255))
        sd.rectangle((10, 4, w - 11, 8), fill=(130, 90, 62, 255))
        ty = by - int(TH * 0.2)
        for n, (cx, b, _) in SEAT.items():
            if b != by:
                continue
            nf = font(TH * 0.42)
            tw = nf.getlength(n)
            cl = cx - tx0
            x0, y0, x1, y1 = cl - tw / 2 - 14, TH * .30, cl + tw / 2 + 14, TH * .88
            sd.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=(20, 24, 38, 255), outline=COL[n], width=2)
            sd.text((cl - tw / 2, TH * .34), n, font=nf, fill=COL[n])
            PLATE[n] = (x0 + tx0, y0 + ty, x1 + tx0, y1 + ty)
        TABLE.append((strip, tx0, ty))
    hb = int(H * 0.045) if W >= H else int(H * 0.03)
    f = font(hb * 0.55)
    unit = "   •   " + cfg["topic"].upper()[:90] + "   •   LIVE MARKET DEBATE   •   BULL vs CRASH   "
    uw = int(f.getlength(unit)) + 1
    reps = int(W * 2 / uw) + 3
    TICK = Image.new("RGB", (uw * reps, hb), (14, 18, 32))
    ImageDraw.Draw(TICK).text((0, hb * 0.18), unit * reps, font=f, fill=(240, 240, 240))
    TICK_W = uw
    BADGE = Image.new("RGBA", (int(hb * 2.4), int(hb * 0.8)), (0, 0, 0, 0))
    bd = ImageDraw.Draw(BADGE)
    bd.rounded_rectangle((0, 0, BADGE.width - 1, BADGE.height - 1), radius=6, fill=(220, 30, 40, 255))
    bd.text((int(hb * .35), int(hb * .08)), "LIVE", font=font(hb * 0.55), fill=(255, 255, 255, 255))


# ---------------- graphics cards ----------------
def caption_card(sc):
    W, H = C["W"], C["H"]
    fs = int(H * 0.039) if W >= H else int(W * 0.042)
    f, fb = font(fs, False), font(fs * 0.8)
    w = int(W * 0.92)
    lines = wrap(sc["text"], f, w - 40)[:4]
    lh = int(fs * 1.3)
    h = len(lines) * lh + int(fs * 1.1) + 24
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, int(fs * 0.9), w - 1, h - 1), radius=18, fill=(8, 10, 20, 215))
    nm = sc["who"]
    tw = fb.getlength(nm)
    d.rounded_rectangle((16, 0, 16 + tw + 28, int(fs * 1.1)), radius=10, fill=COL[nm])
    d.text((30, 2), nm, font=fb, fill=(10, 10, 10))
    y = int(fs * 1.1) + 8
    for l in lines:
        d.text((20, y), l, font=f, fill=(255, 255, 255))
        y += lh
    return card, (W - w) // 2, H - h - int(H * 0.02)


def base_card(w, h, col):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, h - 1), radius=int(h * .12), fill=(10, 14, 28, 232),
                                         outline=col + (255,), width=2)
    return im


def src_card(info, w, h):
    im = base_card(w, h, (90, 140, 255))
    d = ImageDraw.Draw(im)
    x = int(h * .16)
    d.rectangle((0, 0, int(h * .07), h - 1), fill=(90, 140, 255, 255))
    d.text((x, h * .06), "SOURCE: " + info["name"].upper(), font=font(h * .22), fill=(150, 190, 255))
    d.text((x, h * .34), "Published: " + info["date"], font=font(h * .2, False), fill=(235, 235, 235))
    t, f = info["title"], font(h * .17, False)
    if f.getlength(t) > w - x - 12:
        while f.getlength(t + "...") > w - x - 12 and len(t) > 8:
            t = t[:-2]
        t += "..."
    d.text((x, h * .64), t, font=f, fill=(200, 205, 215))
    return im


def stat_card(txt, w, h, col):
    im = base_card(w, h, col)
    d = ImageDraw.Draw(im)
    sz = h * .55
    f = font(sz)
    while f.getlength(txt) > w * .9 and sz > 10:
        sz *= .9
        f = font(sz)
    d.text(((w - f.getlength(txt)) / 2, h * .08), txt, font=f, fill=col + (255,))
    d.text((w * .06, h * .78), "KEY NUMBER", font=font(h * .13), fill=(180, 190, 210, 255))
    return im


def chart_card(w, h, dirn, seed):
    r = random.Random(seed)
    v = [0.0]
    for _ in range(30):
        v.append(v[-1] + 0.12 * dirn + r.uniform(-.35, .35))
    lo, hi = min(v), max(v)
    pts = [(int(w * .06 + w * .88 * i / 30), int(h * .86 - h * .62 * (a - lo) / (hi - lo + 1e-6))) for i, a in enumerate(v)]
    col = (46, 204, 113) if dirn > 0 else (231, 60, 60)
    im = base_card(w, h, col)
    d = ImageDraw.Draw(im)
    d.line(pts, fill=col + (255,), width=max(3, h // 28), joint="curve")
    d.text((int(w * .06), int(h * .04)), "▲ BULLISH" if dirn > 0 else "▼ BEARISH", font=font(h * .18), fill=col + (255,))
    return im


def fade(card, f):
    c = card.copy()
    c.putalpha(c.getchannel("A").point(lambda v: int(v * f)))
    return c


def build_extras(sc):
    W, H = C["W"], C["H"]
    Z = zones(W, H)
    r = random.Random(sc["i"] * 7919)
    x = {"amp": r.uniform(.7, 1.3), "tilt": r.uniform(1.5, 4.0), "lean": r.choice([-2, -1, 0, 1, 2]),
         "pulse": 1 if (re.search(r"[!?]", sc["text"]) or r.random() < .3) else 0}
    wide = sc["i"] % 5 == 0
    x["focus"] = not wide
    x["z0"], x["z1"] = (1.0, 1.06) if wide else ((1.14, 1.26) if sc["i"] % 2 else (1.26, 1.14))
    x["fixed"] = [caption_card(sc)]
    d = direction(sc["text"])
    x["stat"] = x["chart"] = x["src"] = None
    m = STAT.search(sc["text"])
    if m:
        zx, zy, zw, zh = Z["stat"]
        col = (46, 204, 113) if d > 0 else (231, 60, 60) if d < 0 else (255, 200, 40)
        x["stat"] = (stat_card(m.group(1).strip(" .,"), zw, zh, col), zx, zy)
    if d and sc["i"] % 3 == 0:
        zx, zy, zw, zh = Z["chart"]
        x["chart"] = (chart_card(zw, zh, d, sc["i"]), zx, zy)
    if sc.get("src"):
        zx, zy, zw, zh = Z["src"]
        x["src"] = (src_card(sc["src"], zw, zh), zx, zy)
    return x


# ---------------- frame / scene rendering ----------------
def draw_frame(sc, k, x):
    W, H, fps = C["W"], C["H"], C["fps"]
    t = k / fps
    T = sc["t0"] + t
    spk = sc["who"]
    e = sc["env"][min(k, len(sc["env"]) - 1)]
    img = BG.copy()
    for nm in ORDER:
        cx, by, ch = SEAT[nm]
        sp, ph = SPR[nm], PH[nm]
        if nm == spk:
            dy = -(3 + 9 * e) * x["amp"] - 2 * math.sin(T * 8.2 + ph)
            ang = x["tilt"] * math.sin(T * 4.4 + ph) * (0.4 + e) + x["lean"]
            s = 1 + 0.03 * e + 0.02 * x["pulse"] * max(0, math.sin(T * 2.8))
            im = mouth(sp, e)
        else:
            dy = -2.5 * math.sin(T * 3.1 + ph)
            ang = 1.2 * math.sin(T * 1.9 + ph)
            s, im = 1.0, sp
        if s != 1.0:
            im = im.resize((int(im.width * s), int(im.height * s)), Image.BILINEAR)
        im = im.rotate(ang, resample=Image.BICUBIC, expand=True)
        img.paste(im, (int(cx - im.width / 2), int(by + TH * .35 - im.height + dy)), im)
    for strip, tx, ty in TABLE:
        img.paste(strip, (tx, ty), strip)
    ImageDraw.Draw(img).rounded_rectangle(PLATE[spk], radius=10, outline=COL[spk], width=4)
    z = x["z0"] + (x["z1"] - x["z0"]) * k / max(1, sc["n"] - 1)
    if z > 1.001:
        if x["focus"]:
            fx, fby, fch = SEAT[spk]
            fy = fby - fch * 0.45
        else:
            fx, fy = W / 2, H / 2
        cw, chh = W / z, H / z
        x0 = min(max(fx - cw / 2, 0), W - cw)
        y0 = min(max(fy - chh / 2, 0), H - chh)
        img = img.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + chh))
    off = int(T * 90) % TICK_W
    img.paste(TICK.crop((off, 0, off + W, TICK.height)), (0, 0))
    img.paste(BADGE, (8, 3), BADGE)
    if x["stat"]:
        c, px, py = x["stat"]
        f = min(1, t / 0.35)
        cc = c if f >= 1 else fade(c, f)
        img.paste(cc, (px, py + int((1 - f) * 20)), cc)
    if x["chart"]:
        c, px, py = x["chart"]
        part = c.crop((0, 0, max(2, int(c.width * min(1, t / 1.3))), c.height))
        img.paste(part, (px, py), part)
    if x["src"]:
        c, px, py = x["src"]
        p = min(1, t / 0.45)
        ee = 1 - (1 - p) ** 3
        img.paste(c, (px + int((1 - ee) * (W - px)), py), c)
    for c, px, py in x["fixed"]:
        img.paste(c, (px, py), c)
    return img


def render_scene(sc):
    W, H, fps = C["W"], C["H"], C["fps"]
    x = build_extras(sc)
    out = f"{C['tmp']}/v{sc['i']:05d}.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "animation",
           "-crf", "26", "-pix_fmt", "yuv420p", "-an", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for k in range(sc["n"]):
        p.stdin.write(draw_frame(sc, k, x).convert("RGB").tobytes())
    p.stdin.close()
    p.wait()
    return out


# ---------------- audio ----------------
def voice_path(who):
    sub = VOICES[who]
    name = sub.split("/")[-1]
    os.makedirs("voices", exist_ok=True)
    for ext in (".onnx", ".onnx.json"):
        p = f"voices/{name}{ext}"
        if not os.path.exists(p):
            urllib.request.urlretrieve(f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{sub}{ext}", p)
    return f"voices/{name}.onnx"


def tts(who, text, out):
    r = subprocess.run([sys.executable, "-m", "piper", "--model", voice_path(who), "--output_file", out],
                       input=text.encode(), capture_output=True)
    if r.returncode or not os.path.exists(out):
        raise RuntimeError("piper failed: " + r.stderr.decode()[-300:])


def readwav(p):
    w = wave.open(p)
    r = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    w.close()
    if r != RATE and len(x):
        x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * RATE / r)), np.arange(len(x)), x)
    return x.astype(np.float32)


def to_scenes(lines, sources, maxw=24):
    sc = []
    for ln in lines:
        tags = re.findall(r"\[S(\d+)\]", ln["text"])
        src = None
        if tags and 0 <= int(tags[0]) - 1 < len(sources):
            src = sources[int(tags[0]) - 1]
        clean = re.sub(r"\s*\[S\d+\]", "", ln["text"]).strip()
        chunks, cur = [], ""
        for s in re.split(r"(?<=[.!?])\s+", clean):
            if cur and len((cur + " " + s).split()) > maxw:
                chunks.append(cur)
                cur = s
            else:
                cur = (cur + " " + s).strip()
        if cur:
            chunks.append(cur)
        for c in chunks:
            sc.append({"who": ln["who"], "text": c, "src": src})
    for i, s in enumerate(sc):
        s["i"] = i
    return sc


def produce(lines, sources, topic, W, H, fps, out, max_sec=None, tmp="work"):
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    scenes = to_scenes(lines, sources)
    for w in VOICES:
        voice_path(w)

    def job(s):
        s["wav"] = f"{tmp}/a{s['i']:05d}.wav"
        tts(s["who"], s["text"], s["wav"])

    print("voices:", len(scenes), "scenes", flush=True)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(job, scenes))
    data = [readwav(s["wav"]) for s in scenes]
    if max_sec:
        tot, keep = 0, 0
        for x in data:
            tot += len(x) / RATE + PAUSE
            if tot > max_sec and keep >= 4:
                break
            keep += 1
        scenes, data = scenes[:keep], data[:keep]
    t0, chunks = 0.0, []
    for s, x in zip(scenes, data):
        n = int(math.ceil((len(x) / RATE + PAUSE) * fps))
        tgt = int(round(n / fps * RATE))
        a = np.zeros(tgt, dtype=np.float32)
        a[:min(len(x), tgt)] = x[:tgt]
        chunks.append(a.astype(np.int16))
        step = RATE / fps
        e = np.array([float(np.sqrt(np.mean(x[int(k * step):int((k + 1) * step)] ** 2))) if len(x[int(k * step):int((k + 1) * step)]) else 0.0
                      for k in range(n)])
        e = np.clip(e / (np.percentile(e, 90) + 1e-6), 0, 1)
        e = np.convolve(e, [.25, .5, .25], mode="same")
        s.update(n=n, t0=t0, env=e.tolist())
        t0 += n / fps
    master = f"{tmp}/master.wav"
    wv = wave.open(master, "wb")
    wv.setnchannels(1)
    wv.setsampwidth(2)
    wv.setframerate(RATE)
    wv.writeframes(np.concatenate(chunks).tobytes())
    wv.close()
    del data, chunks
    print(f"total length {t0/60:.1f} min -> rendering", flush=True)
    init({"W": W, "H": H, "fps": fps, "topic": topic, "tmp": tmp})
    clips = []
    with Pool(os.cpu_count() or 2) as pool:
        for j, c in enumerate(pool.imap(render_scene, scenes, chunksize=1)):
            clips.append(c)
            if j % 25 == 0:
                print("rendered", j, "/", len(scenes), flush=True)
    lst = f"{tmp}/list.txt"
    open(lst, "w").write("".join(f"file '{os.path.abspath(c)}'\n" for c in clips))
    vid = f"{tmp}/video.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", vid], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", vid, "-i", master, "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "160k", "-shortest", "-movflags", "+faststart", out], check=True)
    for c in clips:
        os.remove(c)
    return out


# ---------------- thumbnail / image ----------------
def thumbnail(title, out, W=1280, H=720):
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    for xx in range(W):
        t = xx / W
        d.line([(xx, 0), (xx, H)], fill=(int(10 + 80 * t), int(70 - 55 * t), int(45 - 20 * t)))
    th = int(H * 0.66)
    for nm, fx in zip(ORDER, (0.13, 0.38, 0.63, 0.88)):
        c = cutout(nm)
        c = c.crop((0, 0, c.width, int(c.height * 0.5)))
        c = c.resize((int(c.width * th / c.height), th), Image.LANCZOS)
        im.paste(c, (int(W * fx - c.width / 2), H - th), c)
    words = re.sub(r"#\w+", "", title).upper().split()[:8]
    sz = 120
    while True:
        f = font(sz)
        ls = wrap(" ".join(words), f, W - 80)
        if len(ls) <= 2 or sz < 56:
            break
        sz -= 6
    y = 18
    for i, l in enumerate(ls[:2]):
        d.text((40, y), l, font=f, fill=(255, 235, 0) if i == 0 else (255, 255, 255), stroke_width=7, stroke_fill=(0, 0, 0))
        y += int(sz * 1.12)
    im.save(out, quality=90)
    return out
