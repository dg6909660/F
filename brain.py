import os, re, json, time, html, hashlib
from urllib.parse import urlparse
import feedparser, requests

feedparser.USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
OLLAMA = "http://127.0.0.1:11434/api/generate"
MODEL = os.getenv("MODEL", "qwen2.5:3b")

FEEDS = [
 "https://www.cnbc.com/id/10000664/device/rss/rss.html",
 "https://www.cnbc.com/id/15839069/device/rss/rss.html",
 "https://www.cnbc.com/id/100003114/device/rss/rss.html",
 "https://feeds.bbci.co.uk/news/business/rss.xml",
 "https://feeds.marketwatch.com/marketwatch/topstories/",
 "https://feeds.marketwatch.com/marketwatch/marketpulse/",
 "https://finance.yahoo.com/news/rssindex",
 "https://www.investing.com/rss/news.rss",
 "https://www.investing.com/rss/stock_Stocks.rss",
 "https://seekingalpha.com/market_currents.xml",
 "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
 "https://www.fool.com/a/feeds/foolwatch/",
 "https://www.benzinga.com/feed",
 "https://cointelegraph.com/rss",
 "https://www.coindesk.com/arc/outboundfeeds/rss/",
 "https://www.theguardian.com/business/rss",
 "https://www.economist.com/finance-and-economics/rss.xml",
 "https://www.nasdaq.com/feed/rssoutbound?category=Markets",
 "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
 "https://www.business-standard.com/rss/markets-106.rss",
 "https://fortune.com/feed/",
 "https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline",
]
NAMES = {"cnbc.com": "CNBC", "bbci.co.uk": "BBC News", "marketwatch.com": "MarketWatch",
 "yahoo.com": "Yahoo Finance", "investing.com": "Investing.com", "seekingalpha.com": "Seeking Alpha",
 "dj.com": "The Wall Street Journal", "fool.com": "The Motley Fool", "benzinga.com": "Benzinga",
 "cointelegraph.com": "Cointelegraph", "coindesk.com": "CoinDesk", "theguardian.com": "The Guardian",
 "economist.com": "The Economist", "nasdaq.com": "Nasdaq", "indiatimes.com": "The Economic Times",
 "business-standard.com": "Business Standard", "fortune.com": "Fortune", "investopedia.com": "Investopedia"}
KEYS = ["stock", "market", "invest", "fed ", "federal reserve", "rate", "inflation", "bitcoin", "crypto", "etf",
 "earnings", "bond", "yield", "dollar", "gold", "oil", "economy", "recession", "bank", "ipo", "nasdaq", "dow",
 "s&p", "tesla", "nvidia", "trading", "fund", "tax", "debt", "dividend", "wall street", "shares", "crash",
 "rally", "tariff", "billion", "trillion"]
STOP = set("this that with from have will what when your about after their they been more than into over says said just could would amid".split())

SYS = ("You are a world-class finance scriptwriter and market analyst. You write vivid, deeply analytical spoken "
       "dialogue in English. Never invent precise statistics you are not sure about; prefer well-known facts, "
       "historical episodes, mechanisms, analogies and clear reasoning.")


def llm(prompt, system=SYS, tokens=2200, temp=0.8):
    for _ in range(3):
        try:
            r = requests.post(OLLAMA, json={"model": MODEL, "prompt": prompt, "system": system, "stream": False,
                "options": {"temperature": temp, "num_predict": tokens, "num_ctx": 8192}}, timeout=3600)
            t = r.json().get("response", "").strip()
            if t:
                return t
        except Exception as e:
            print("llm error:", e)
            time.sleep(5)
    return ""


def src_name(u, f):
    d = urlparse(u).netloc.replace("www.", "")
    for k, v in NAMES.items():
        if k in d:
            return v
    return (f.feed.get("title") or d).split(" - ")[0][:40]


def fetch_articles(days=3):
    out, cutoff = [], time.time() - days * 86400
    for u in FEEDS:
        try:
            f = feedparser.parse(u)
            for e in f.entries[:25]:
                title = html.unescape(e.get("title", "")).strip()
                summ = re.sub(r"<[^>]+>", " ", html.unescape(e.get("summary", "")))
                summ = re.sub(r"\s+", " ", summ).strip()[:500]
                ts = e.get("published_parsed") or e.get("updated_parsed")
                if ts and time.mktime(ts) < cutoff:
                    continue
                low = (title + " " + summ).lower()
                if not title or not any(k in low for k in KEYS):
                    continue
                date = time.strftime("%B %d, %Y", ts) if ts else time.strftime("%B %d, %Y")
                out.append({"title": title, "summary": summ, "source": src_name(u, f), "date": date})
        except Exception as ex:
            print("feed failed", u, ex)
    print(len(out), "finance articles")
    return out


def toks(s):
    return {w for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in STOP}


def pick_topic(arts, used, prefix):
    for a in arts:
        a["tok"] = toks(a["title"])
    scored = []
    for a in arts:
        rel = [b for b in arts if b is not a and len(a["tok"] & b["tok"]) >= 2]
        kw = sum(k in a["title"].lower() for k in KEYS)
        scored.append((len(rel) * 2 + kw, a, rel))
    scored.sort(key=lambda x: -x[0])
    for _, a, rel in scored:
        h = prefix + hashlib.md5(a["title"].lower().encode()).hexdigest()[:10]
        if h in used:
            continue
        return h, a, [a] + rel[:7]
    raise RuntimeError("no fresh topic found")


def clean_topic(t):
    return re.split(r"\s[-|–—]\s", t)[0].strip()


def facts_text(group):
    return "\n".join(f"[S{i+1}] {a['title']} — {a['summary']}" for i, a in enumerate(group))


LINE = re.compile(r"^\W*(BULL|CRASH|GOLDIE|CASH)\W*[:：]\s*(.+)$", re.I)


def parse(text, allowed):
    out = []
    for raw in text.splitlines():
        m = LINE.match(raw.strip())
        if not m:
            continue
        who = m.group(1).upper()
        if who not in allowed:
            continue
        body = re.sub(r"\((?!S\d)[^)]{0,50}\)", "", m.group(2))
        body = re.sub(r"[*_#`]+", "", body)
        body = re.sub(r"\s+", " ", body).strip()
        if len(body.split()) >= 3:
            out.append({"who": who, "text": body})
    return out


FALLBACK_TITLES = ["What exactly happened and why it matters", "The numbers behind the headline",
 "History: similar moments in the market", "The bull case in depth", "The bear case in depth",
 "Who wins and who loses", "Beginner mistakes to avoid", "Risk management and position sizing",
 "How professionals think about this", "Hidden links: rates, currencies and commodities",
 "Scenarios: best case, base case, worst case", "Myths and misinformation", "What to watch next",
 "Psychology: fear, greed and herd behavior", "Practical checklist for investors", "Long-term vs short-term view"]


def outline(topic, facts, n=16):
    t = llm(f"Topic: {topic}\nNews facts:\n{facts}\n\nGive {n} numbered section titles for a deep two-hour debate about this "
            f"topic. Do not include a conclusion section. Output only the numbered list.", tokens=500)
    titles = [re.sub(r"^\W*\d+[\).:-]?\s*", "", l).strip(" *#-") for l in t.splitlines() if re.match(r"^\W*\d+", l)]
    titles = [x for x in titles if 5 < len(x) < 110]
    i = 0
    while len(titles) < n:
        titles.append(FALLBACK_TITLES[i % len(FALLBACK_TITLES)])
        i += 1
    return titles[:n]


def section_prompt(topic, facts, title, k, covered, tail, words):
    return f"""Write one section of a long, deep finance debate show.
TOPIC: {topic}
SECTION TITLE: {title}
NEWS FACTS (cite as [S1], [S2] ... right after the sentence that uses a fact):
{facts}

CHARACTERS:
GOLDIE (neutral moderator): opens with a sharp question, challenges BOTH sides, never takes a side.
BULL (optimist): explains the benefits, upside and opportunities with evidence.
CRASH (pessimist): explains the harms, risks and warnings with evidence.

RULES:
- Output only dialogue lines. Each line starts with BULL:, CRASH: or GOLDIE: (uppercase, colon).
- Bull and Crash rebut each other directly and argue hard. Goldie keeps asking deeper follow-up questions.
- Use evidence: facts, historical episodes, mechanisms, analogies, examples, and your own expert knowledge.
- Natural spoken English, 1-4 sentences per line, about {words} words in total. No stage directions, no markdown.
Already covered, do not repeat: {'; '.join(covered[-8:]) or 'nothing yet'}
Last lines so far:
{tail}
Begin:"""


def intro_lines(topic, facts):
    t = llm(f"Write the opening monologue of CASH, the white host who ONLY introduces the show. 6 to 8 lines, each "
            f"starting with 'CASH:'. State today's topic, why it matters right now, 4 specific things viewers will learn, "
            f"what the debate will cover, and that this is education, not financial advice.\nTopic: {topic}\nFacts:\n{facts}",
            tokens=800)
    ls = parse(t, {"CASH"})
    if len(ls) < 3:
        ls = [{"who": "CASH", "text": f"Welcome to the table. I'm Cash, the white one, and I open the show. Today's topic: {topic}."},
              {"who": "CASH", "text": "In the next two hours you will learn what happened, why it matters, who benefits, who gets hurt, and how to think about it."},
              {"who": "CASH", "text": "This is education, not financial advice."}]
    if "cash" not in ls[0]["text"].lower():
        ls[0]["text"] = "Welcome to the table. I'm Cash, the white one, and my only job is to open the show. " + ls[0]["text"]
    ls += [
        {"who": "CASH", "text": "Now let me introduce the rest of the table."},
        {"who": "GOLDIE", "text": "I'm Goldie, the golden one. I'm neutral. I ask the hard questions to both sides and I keep this debate honest."},
        {"who": "BULL", "text": "I'm Bull, the green one. I bring the benefits, the opportunities and the upside of every story."},
        {"who": "CRASH", "text": "And I'm Crash, the red one. I bring the harms, the risks and the warnings nobody wants to hear."},
        {"who": "CASH", "text": "That's the table. Goldie, it's all yours."},
        {"who": "GOLDIE", "text": "Thank you, Cash. Bull, Crash, let's get started."},
    ]
    return ls


CLOSING = [
    {"who": "GOLDIE", "text": "That's the full picture. Bull showed you the opportunity, Crash showed you the danger, and the smart move is to do your own research."},
    {"who": "GOLDIE", "text": "This show is for education only, not financial advice. If it helped you, subscribe, and tell us in the comments which side you were on."},
]


def write_script(topic, group, target_min=112, max_sections=26):
    facts = facts_text(group)
    target = int(target_min * 155)
    titles = outline(topic, facts)
    lines = intro_lines(topic, facts)
    words = sum(len(l["text"].split()) for l in lines)
    covered, k, fails = [], 0, 0

    def section(title, w):
        tail = "\n".join(f"{l['who']}: {l['text']}" for l in lines[-3:])
        txt = llm(section_prompt(topic, facts, title, k, covered, tail, w), tokens=2400)
        return parse(txt, {"BULL", "CRASH", "GOLDIE"})

    while words < target and k < max_sections and fails < 4:
        title = titles[k] if k < len(titles) else f"Audience questions and sharper rebuttals, round {k - len(titles) + 1}"
        got = section(title, 1100)
        w = sum(len(l["text"].split()) for l in got)
        if w < 120:
            fails += 1
            continue
        fails = 0
        lines += got
        words += w
        covered.append(title)
        k += 1
        print(f"section {k}: +{w} words, total {words}/{target}", flush=True)
    got = section("Final verdict, key takeaways and what to watch next", 600)
    if sum(len(l["text"].split()) for l in got) >= 100:
        lines += got
    lines += CLOSING
    if len(lines) < 30:
        raise RuntimeError("script too short")
    return lines


def write_short(topic, group):
    facts = facts_text(group[:3])
    t = llm(f"""Write a 50-second finance debate for a YouTube Short. Topic: {topic}
Facts:
{facts}
Output exactly 8 lines, each starting with a name and colon, in this order: CASH (one sentence: today's topic), GOLDIE (sharp question),
BULL (benefit, 2 sentences), CRASH (risk, 2 sentences), BULL (rebuttal, 1 sentence), CRASH (rebuttal, 1 sentence),
GOLDIE (neutral verdict), GOLDIE (asks viewers which side they are on). Plain spoken English, under 22 words per line.""", tokens=600)
    ls = parse(t, {"CASH", "GOLDIE", "BULL", "CRASH"})
    if len(ls) < 5:
        raise RuntimeError("short script failed")
    return ls[:9]


def clean(t, names):
    t = re.sub(r"https?://\S+|www\.\S+", "", t)
    for n in names:
        t = re.sub(re.escape(n), "", t, flags=re.I)
    t = re.sub(r"[<>]", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def seo(topic, short=False):
    names = list(NAMES.values())
    title = llm(f"Write ONE click-worthy but honest YouTube title (max 80 characters, no quotes, no hashtags) for a finance "
                f"debate video: Bull vs Crash on this topic: {topic}. Output the title only.", tokens=40, temp=0.9)
    title = title.split("\n")[0].strip(' "\'*#')
    if not 15 < len(title) <= 90:
        title = f"{topic[:70]}: Bull vs Crash"
    desc = llm(f"Write an SEO-optimised YouTube description of 180-260 words for a finance debate video about: {topic}. "
               f"Two hook sentences with keywords first, then what viewers will learn as short sentences. Characters: "
               f"Bull (benefits), Crash (risks), Goldie (neutral), Cash (host). NO links, NO mention of where information "
               f"came from, NO news outlet names.", tokens=500)
    desc = clean(desc, names)
    desc += "\n\nEducational content only. Not financial advice.\n\n#finance #investing #stockmarket #bullvscrash"
    if short:
        title = title[:80] + " #Shorts"
        desc += " #Shorts"
    tg = llm(f"Give 15 YouTube tags (comma separated, no hashtags, finance and investing) for: {topic}", tokens=150)
    tags, tot = [], 0
    for x in re.split(r"[,\n]", tg):
        x = re.sub(r"^\W*\d+[\).]?\s*", "", x).strip(' "#')
        if 2 < len(x) <= 30 and tot + len(x) < 450:
            tags.append(x)
            tot += len(x)
    tags = tags or ["finance", "investing", "stock market", "bull vs bear"]
    return title, desc, tags


def friday_text(topic):
    t = llm(f"Write a short engaging community post (70-100 words) for a finance YouTube channel about: {topic}. "
            f"End with a question to the audience. Add 3 hashtags. No links, no source names.", tokens=250)
    return clean(t, list(NAMES.values()))
