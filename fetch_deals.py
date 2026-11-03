"""Deal Radar: daily Bangalore/Karnataka M&A, FDD and investment lead digest.
Run: python fetch_deals.py   (outputs docs/index.html + docs/digest.json)
Env (optional): ANTHROPIC_API_KEY, CLAUDE_MODEL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import os, re, json, html, difflib, datetime as dt
from urllib.parse import quote_plus
import feedparser, requests

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
os.makedirs(DOCS, exist_ok=True)
SEEN_PATH = os.path.join(DOCS, "seen.json")
DIGEST_PATH = os.path.join(DOCS, "digest.json")
MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
LOOKBACK_HOURS = 48
KEEP_DAYS = 14
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# ---------- SOURCES (edit freely) ----------
GOOGLE_QUERIES = [
    "Bengaluru acquisition", "Bangalore acquires", "Karnataka merger",
    "Bengaluru startup funding round", "Bengaluru open offer SEBI",
    "Bengaluru NCLT scheme of arrangement", "Bengaluru CCI approves combination",
    "Karnataka company stake sale", "Bengaluru slump sale", "Bengaluru DRHP IPO filing",
    "Bengaluru private equity investment", "Bengaluru due diligence acquisition",
    "Bengaluru joint venture foreign investor", "Karnataka demerger",
]
# (name, url, location_already_filtered)
DIRECT_FEEDS = [
    ("Entrackr", "https://entrackr.com/feed", False),
    ("Inc42", "https://inc42.com/feed/", False),
    ("YourStory", "https://yourstory.com/feed", False),
    ("Deccan Herald Business", "https://www.deccanherald.com/rss/business.rss", False),
    ("Reddit r/bangalore", "https://www.reddit.com/r/bangalore/search.rss?q=acquisition+OR+funding+OR+merger&restrict_sr=1&sort=new", False),
    ("Reddit r/IndianStockMarket", "https://www.reddit.com/r/IndianStockMarket/search.rss?q=bengaluru+OR+bangalore+OR+karnataka&restrict_sr=1&sort=new", True),
    ("Reddit r/startups_india", "https://www.reddit.com/r/startups_india/search.rss?q=bangalore+OR+bengaluru+acquisition+OR+funding&restrict_sr=1&sort=new", True),
]
for q in GOOGLE_QUERIES:
    DIRECT_FEEDS.append((
        "Google News",
        f"https://news.google.com/rss/search?q={quote_plus(q + ' when:2d')}&hl=en-IN&gl=IN&ceid=IN:en",
        True))

LOC_RE = re.compile(r"bengaluru|bangalore|karnataka|mysuru|mysore|hubballi|hubli|mangaluru|mangalore|belagavi|whitefield|electronic city|koramangala|hsr layout|indiranagar", re.I)
DEAL_RE = re.compile(r"acqui|merger|merge|amalgamat|takeover|open offer|stake|buyout|funding|raises|raised|series [a-f]\b|invest|slump sale|demerger|scheme of arrangement|nclt|\bcci\b|\bipo\b|drhp|rhp|due diligence|divest|joint venture|\bjv\b|buys|sells|valuation|pre-ipo|secondary sale|insolvency|cirp", re.I)

SERVICES = """FDD (financial due diligence); slump sale / business transfer structuring; share vs asset deal tax structuring;
stamp duty computation; FEMA / FC-GPR / FC-TRS compliance and valuation; transfer pricing (TNMM, ODI, FLA);
SPA review; scheme of arrangement / demerger tax; pre-IPO restructuring and tax diligence."""

# ---------- COLLECT ----------
def clean(t):
    return html.unescape(re.sub(r"<[^>]+>", " ", t or "")).strip()

def norm_title(t):
    t = re.sub(r"\s+[-|–]\s+[^-|–]{2,40}$", "", t)  # strip " - Publisher"
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()

def collect():
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=LOOKBACK_HOURS)
    items = []
    for name, url, loc_ok in DIRECT_FEEDS:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 DealRadar/1.0"})
            feed = feedparser.parse(r.content)
        except Exception as e:
            print(f"[warn] {name} failed: {e}")
            continue
        for e in feed.entries:
            ts = e.get("published_parsed") or e.get("updated_parsed")
            if ts:
                pub = dt.datetime(*ts[:6], tzinfo=dt.timezone.utc)
                if pub < cutoff:
                    continue
            title, summ = clean(e.get("title")), clean(e.get("summary"))[:500]
            text = f"{title} {summ}"
            if not DEAL_RE.search(text):
                continue
            if not (loc_ok or LOC_RE.search(text)):
                continue
            src = name
            if name == "Google News" and e.get("source"):
                src = e["source"].get("title", name)
            items.append({"title": title, "summary": summ, "link": e.get("link", ""), "source": src})
    print(f"[info] {len(items)} candidate items before dedupe")
    return items

def dedupe(items, seen):
    kept, norms = [], []
    for it in items:
        if it["link"] in seen:
            continue
        n = norm_title(it["title"])
        if any(difflib.SequenceMatcher(None, n, m).ratio() > 0.8 for m in norms):
            continue
        norms.append(n)
        kept.append(it)
    return kept

# ---------- CLASSIFY ----------
def heuristic(it):
    t = f"{it['title']} {it['summary']}".lower()
    if re.search(r"nclt|insolvency|cirp", t): typ, pitch = "NCLT / Distressed", "Early-stage lead: valuation, scheme tax impact, resolution-applicant diligence"
    elif re.search(r"open offer|takeover", t): typ, pitch = "Open offer / Takeover", "SEBI takeover compliance, valuation, tax structuring"
    elif re.search(r"acqui|buys|merger|amalgam|stake", t): typ, pitch = "M&A", "FDD, SPA review, stamp duty, slump sale vs share deal"
    elif re.search(r"ipo|drhp|rhp", t): typ, pitch = "IPO", "Pre-IPO restructuring and tax diligence"
    elif re.search(r"fund|raise|series|invest", t): typ, pitch = "Funding", "FEMA / FC-GPR compliance, valuation, investor diligence"
    else: typ, pitch = "Other", "Review manually"
    return {"relevant": True, "company": "", "deal_type": typ, "parties": "", "size": "", "stage": "", "score": 3, "pitch": pitch}

def classify(items):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print("[info] no ANTHROPIC_API_KEY, using keyword heuristics")
        return [heuristic(i) for i in items]
    results = [None] * len(items)
    for start in range(0, len(items), 20):
        batch = items[start:start + 20]
        listing = "\n".join(f"{i}. {b['title']} | {b['summary'][:300]} | {b['source']}" for i, b in enumerate(batch))
        prompt = f"""You screen news for a Bangalore transaction tax & M&A advisory firm. Services offered: {SERVICES}
For each numbered item return a JSON object. Respond with ONLY a JSON array, no prose or fences.
Fields: i (int), relevant (bool: true only if a real deal/transaction/investment/restructuring involving a Bangalore or Karnataka based party; false for opinion, generic news, stock-price chatter),
company (main Karnataka company), deal_type (M&A | Funding | Open offer | NCLT/Distressed | IPO | JV | Restructuring | Other),
parties (acquirer / target / investors), size (as stated, else ""), stage (rumour | announced | regulatory filing | completed),
score (1-5 lead value for the firm), pitch (one short line: which service to pitch and why). Do not invent facts not in the text.
Items:
{listing}"""
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 4000, "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            txt = "".join(c.get("text", "") for c in r.json()["content"])
            txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
            for o in json.loads(txt):
                results[start + int(o["i"])] = o
        except Exception as e:
            print(f"[warn] LLM batch failed ({e}); falling back to heuristics for this batch")
    return [r or heuristic(items[i]) for i, r in enumerate(results)]

# ---------- STATE / RENDER ----------
def load(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default

def render(entries, updated):
    esc = html.escape
    cards = []
    for e in entries:
        meta = " · ".join(x for x in [e.get("deal_type"), e.get("stage"), e.get("size")] if x)
        cards.append(f"""<article class="card" data-type="{esc(e.get('deal_type',''))}" data-score="{e.get('score',3)}">
<div class="top"><span class="score s{e.get('score',3)}">{e.get('score',3)}</span><span class="date">{esc(e['date'])}{' · NEW' if e.get('new') else ''}</span></div>
<h3><a href="{esc(e['link'])}" target="_blank" rel="noopener">{esc(e['title'])}</a></h3>
<p class="meta">{esc(meta)}</p>
{f'<p><b>Parties:</b> {esc(e["parties"])}</p>' if e.get('parties') else ''}
<p class="pitch">Pitch: {esc(e.get('pitch',''))}</p>
<p class="src">{esc(e['source'])}</p></article>""")
    body = "\n".join(cards) or "<p>No new leads today.</p>"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Deal Radar</title>
<style>
:root{{--bg:#f6f7f9;--fg:#15181d;--card:#fff;--mut:#5f6773;--bd:#e2e5ea;--ac:#1f5eff}}
@media(prefers-color-scheme:dark){{:root{{--bg:#101215;--fg:#e8eaed;--card:#1a1d22;--mut:#9aa3af;--bd:#2a2f37;--ac:#6b9bff}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}
header{{padding:16px;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--bd)}}
h1{{margin:0;font-size:18px}} .sub{{color:var(--mut);font-size:12px}}
.bar{{display:flex;gap:8px;margin-top:8px}} input,select{{flex:1;padding:8px;border:1px solid var(--bd);border-radius:8px;background:var(--card);color:var(--fg)}}
main{{max-width:760px;margin:0 auto;padding:12px}}
.card{{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px 14px;margin-bottom:10px}}
.card h3{{margin:6px 0;font-size:15px}} .card a{{color:var(--ac);text-decoration:none}}
.top{{display:flex;justify-content:space-between;align-items:center}} .date,.src,.meta{{color:var(--mut);font-size:12px;margin:2px 0}}
.pitch{{margin:6px 0;font-size:13px}} p{{margin:4px 0}}
.score{{font-weight:700;border-radius:6px;padding:1px 8px;font-size:12px;background:var(--bd)}} .s4,.s5{{background:var(--ac);color:#fff}}
</style></head><body>
<header><h1>Deal Radar · Bangalore / Karnataka</h1><div class="sub">Updated {esc(updated)} IST · {len(entries)} leads</div>
<div class="bar"><input id="q" placeholder="Search…" oninput="f()"><select id="m" onchange="f()">
<option value="0">All scores</option><option value="4">Score 4+</option><option value="5">Score 5</option></select></div></header>
<main id="list">{body}</main>
<script>function f(){{var q=document.getElementById('q').value.toLowerCase(),m=+document.getElementById('m').value;
document.querySelectorAll('.card').forEach(function(c){{c.style.display=(c.innerText.toLowerCase().includes(q)&&+c.dataset.score>=m)?'':'none'}})}}</script>
</body></html>"""
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

def telegram(new_entries):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat) or not new_entries: return
    top = sorted(new_entries, key=lambda e: -e.get("score", 3))[:10]
    msg = "Deal Radar: top leads today\n\n" + "\n\n".join(
        f"[{e.get('score')}] {e['title']}\n{e.get('pitch','')}\n{e['link']}" for e in top)
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": msg[:4000], "disable_web_page_preview": True}, timeout=20)
    except Exception as e:
        print(f"[warn] telegram failed: {e}")

def main():
    now = dt.datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    seen = load(SEEN_PATH, {})
    history = load(DIGEST_PATH, {"items": []})["items"]

    items = dedupe(collect(), seen)
    print(f"[info] {len(items)} new items after dedupe")
    meta = classify(items)

    new_entries = []
    for it, m in zip(items, meta):
        seen[it["link"]] = today
        if not m.get("relevant", True): continue
        new_entries.append({**it, **{k: m.get(k, "") for k in ("company","deal_type","parties","size","stage","score","pitch")},
                            "date": today, "new": True})

    cutoff = (now - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for h in history: h["new"] = False
    all_entries = [e for e in new_entries + history if e["date"] >= cutoff]
    all_entries.sort(key=lambda e: (e["date"], e.get("score", 3)), reverse=True)
    seen = {k: v for k, v in seen.items() if v >= (now - dt.timedelta(days=30)).strftime("%Y-%m-%d")}

    with open(DIGEST_PATH, "w") as f: json.dump({"updated": now.isoformat(), "items": all_entries}, f, indent=1)
    with open(SEEN_PATH, "w") as f: json.dump(seen, f)
    render(all_entries, now.strftime("%d %b %Y, %H:%M"))
    telegram(new_entries)
    print(f"[done] {len(new_entries)} new leads, {len(all_entries)} in digest")

if __name__ == "__main__":
    main()
