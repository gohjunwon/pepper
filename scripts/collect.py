"""Pepper 자동 수집기 — Claude 없이 GitHub Actions에서 돌아갑니다.

사용법: python scripts/collect.py today|full|all
  today : 오늘의 호재(장전·시간외 급등 + 관련 기사)와 자동 선별 갱신
  full  : 1달 앞 일정(실적·FDA·시장), 시장 일정, 실적 미리보기, 캘린더(.ics) 갱신
  all   : 둘 다
결과: data/today.json, upcoming.json, macro.json, earnings.json, picks.json,
      preview/*.json, calendar.ics, (MY_PASS 비밀값이 있으면) my/data.enc
"""
import base64
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as XML
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA = os.path.join(ROOT, "data")
ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
NOW = datetime.now(timezone.utc)
WD = "월화수목금토일"

# ---------------------------------------------------------------- 휴장·연준 (공식 발표 기준)
HOLIDAYS = {  # NYSE 휴장일
    "2026-11-26": "추수감사절", "2026-12-25": "크리스마스",
    "2027-01-01": "새해 첫날", "2027-01-18": "마틴 루서 킹 데이", "2027-02-15": "대통령의 날",
    "2027-03-26": "성금요일", "2027-05-31": "메모리얼 데이", "2027-06-18": "준틴스 데이(대체 휴장)",
    "2027-07-05": "독립기념일(대체 휴장)", "2027-09-06": "노동절", "2027-11-25": "추수감사절",
    "2027-12-24": "크리스마스(대체 휴장)",
}
EARLY_CLOSE = {"2026-11-27": "추수감사절 다음 날", "2026-12-24": "크리스마스이브", "2027-11-26": "추수감사절 다음 날"}
FOMC = ["2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15", "2027-11-17"]
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

EXTRA = ["CPNG", "PLTR", "SOFI", "IONQ", "RKLB", "MSTR", "COIN", "HOOD", "ARM", "SMCI", "RIVN", "LCID", "NIO",
         "XPEV", "LI", "BABA", "PDD", "TSM", "ASML", "SHOP", "SNOW", "MDB", "CRWD", "NET", "DDOG", "ZS", "OKTA",
         "U", "RBLX", "AFRM", "UPST", "MRNA", "BNTX", "NVO", "LLY", "SOUN", "BBAI", "RGTI", "QBTS", "OKLO", "SMR",
         "HIMS", "CELH", "DKNG", "ROKU", "SPOT", "SE", "MELI", "NU", "GRAB", "TEM", "APP", "CVNA", "AI", "PATH"]


# ---------------------------------------------------------------- 공통
def get(url, tries=3, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    h.update(headers or {})
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj):
    p = os.path.join(DATA, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def iso(d=None):
    return (d or NOW).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def md(d):
    return f"{d.month}/{d.day}({WD[d.weekday()]})"


def is_trading(d):
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def next_trading(d):
    while not is_trading(d):
        d += timedelta(days=1)
    return d


def session_day():
    """곧 열릴(또는 열려 있는) 미국 정규장 날짜(미국 날짜)"""
    n = NOW.astimezone(ET)
    d = n.date()
    if is_trading(d) and (n.hour, n.minute) < (16, 0):
        return d
    return next_trading(d + timedelta(days=1))


def session_label(d):
    o = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET).astimezone(KST)
    c = datetime(d.year, d.month, d.day, 13 if d.isoformat() in EARLY_CLOSE else 16, 0, tzinfo=ET).astimezone(KST)
    hm = lambda x: f"{x.hour - 12 if x.hour > 12 else x.hour}:{x.minute:02d}"
    s = f"미국 {md(d)} 장 = 한국 {o.month}/{o.day} 밤 {hm(o)} ~ {c.month}/{c.day} 아침 {hm(c)}"
    if d.isoformat() in EARLY_CLOSE:
        s += f" ({EARLY_CLOSE[d.isoformat()]} 조기 폐장)"
    return s


def kst_of_et(d, h, m):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET).astimezone(KST)


# ---------------------------------------------------------------- 번역 (무료 구글 번역, 결과 저장)
TCACHE_P = os.path.join(ROOT, "tools", "tcache.json")
try:
    TCACHE = json.load(open(TCACHE_P, encoding="utf-8"))
except Exception:
    TCACHE = {}


def _gtx(text):
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ko&dt=t&q="
           + urllib.parse.quote(text))
    data = json.loads(get(url, tries=1))
    return "".join(seg[0] for seg in data[0] if seg and seg[0]).split("\n")


def _dict(text):
    url = ("https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=en&tl=ko&"
           + "&".join("q=" + urllib.parse.quote(t) for t in text.split("\n")))
    data = json.loads(get(url, tries=1))
    return [(d[0] if isinstance(d, list) else d) for d in data]


def _mobile(text):
    out = []
    for t in text.split("\n"):
        h = get("https://translate.google.com/m?sl=en&tl=ko&hl=ko&q=" + urllib.parse.quote(t), tries=1).decode("utf-8", "ignore")
        m = re.search(r'class="result-container">(.*?)</div>', h, re.S)
        out.append(__import__("html").unescape(m.group(1)) if m else "")
        time.sleep(0.3)
    return out


TR_FAIL = {"n": 0}


def ko_many(texts):
    texts = [re.sub(r"\s+", " ", t or "").strip() for t in texts]
    need = [t for t in dict.fromkeys(texts) if t and t not in TCACHE]
    chunks, cur = [], []
    for t in need:
        if cur and sum(len(x) for x in cur) + len(t) > 1500:
            chunks.append(cur)
            cur = []
        cur.append(t)
    if cur:
        chunks.append(cur)
    for chunk in chunks:
        if TR_FAIL["n"] >= 6:
            break  # 번역 창구가 모두 막혔으면 이번엔 영어로 두고 다음 실행 때 다시
        joined = "\n".join(chunk)
        done = False
        for fn in (_gtx, _dict, _mobile):
            try:
                out = fn(joined)
                if len(out) == len(chunk) and all(out):
                    for a, b in zip(chunk, out):
                        TCACHE[a] = b.strip()
                    done = True
                    break
            except Exception as e:
                print("번역 창구 실패", fn.__name__, str(e)[:60])
                time.sleep(3)
        if not done:
            TR_FAIL["n"] += 1
        time.sleep(1.2)
    return [TCACHE.get(t, t) if t else "" for t in texts]


def ko(t):
    return ko_many([t])[0]


def save_tcache():
    items = list(TCACHE.items())[-4000:]
    json.dump(dict(items), open(TCACHE_P, "w", encoding="utf-8"), ensure_ascii=False)


# ---------------------------------------------------------------- 종목 이름(한글)
NAMES_P = os.path.join(ROOT, "tools", "names_ko.json")
try:
    NAMES = json.load(open(NAMES_P, encoding="utf-8"))
except Exception:
    NAMES = {}


def learn_names():
    """예전 데이터에 들어 있던 한글 이름을 모아 둡니다"""
    for n in ["today.json", "upcoming.json"]:
        for i in load(n, {}).get("items", []):
            t, nm = i.get("ticker"), i.get("name")
            if t and nm and re.search("[가-힣]", nm) and "," not in str(t):
                NAMES.setdefault(str(t).upper(), nm)
    for i in load("preview/index.json", {}).get("items", []):
        if i.get("ticker") and i.get("name"):
            NAMES.setdefault(i["ticker"].upper(), i["name"])


def short_en(name):
    n = re.sub(r"\(.*?\)", " ", name or "")
    n = re.sub(r"[,.]|\b(Inc|Corp|Corporation|Company|Co|Ltd|plc|PLC|Holdings|Group|Incorporated|The|Class [A-C]|N\.?V|S\.?A|Limited)\b", " ", n)
    w = n.split()
    k = 4 if len(w) > 3 and (w[1].lower() in ("of", "and", "&") or w[2].lower() in ("of", "and", "&")) else 3
    return " ".join(w[:k]).strip()


def name_of(t, en=""):
    return NAMES.get(t.upper()) or short_en(en) or t


# ---------------------------------------------------------------- 관심종목 (my 사이트 암호 데이터에서)
def my_key(pw, salt, it):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=it).derive(pw)


def my_old():
    pw = os.environ.get("MY_PASS", "")
    if not pw:
        return None
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        e = json.load(open(os.path.join(ROOT, "my", "data.enc")))
        k = my_key(pw.encode(), base64.b64decode(e["salt"]), e["iter"])
        return json.loads(AESGCM(k).decrypt(base64.b64decode(e["iv"]), base64.b64decode(e["ct"]), None))
    except Exception as ex:
        print("my/data.enc 풀기 실패(비밀번호 확인):", ex)
        return None


def watch_list(old):
    env = os.environ.get("WATCHLIST", "")
    if env.strip():
        return [x.strip().upper() for x in re.split(r"[,\s]+", env) if x.strip()]
    if old and isinstance(old.get("watch"), dict):
        return [str(x).upper() for x in old["watch"].get("tickers", [])]
    return []


# ---------------------------------------------------------------- 뉴스
def gnews(q, lang="en", days=2):
    loc = "hl=ko&gl=KR&ceid=KR:ko" if lang == "ko" else "hl=en-US&gl=US&ceid=US:en"
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{days}d") + "&" + loc
    try:
        root = XML.fromstring(get(url))
    except Exception as e:
        print("구글뉴스 실패", q, e)
        return []
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            when = parsedate_to_datetime(it.findtext("pubDate"))
        except Exception:
            when = None
        out.append({"t": title, "u": it.findtext("link") or "", "s": src, "when": when})
    return out


def yahoo_news(t):
    import yfinance as yf
    out = []
    try:
        for it in (yf.Ticker(t).news or [])[:15]:
            c = it.get("content", it)
            title = c.get("title") or ""
            summ = c.get("summary") or c.get("description") or ""
            url = (c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link") or ""
            prov = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance"
            when = None
            if c.get("pubDate"):
                when = datetime.fromisoformat(c["pubDate"].replace("Z", "+00:00"))
            elif it.get("providerPublishTime"):
                when = datetime.fromtimestamp(it["providerPublishTime"], timezone.utc)
            if title:
                out.append({"t": title, "sum": re.sub(r"<[^>]+>", "", summ), "u": url, "s": prov, "when": when})
    except Exception as e:
        print("야후 뉴스 실패", t, e)
    return out


KIND_RULES = [
    ("FDA", r"\bFDA\b|approv(al|es|ed) .*(drug|therapy|treatment)|PDUFA|clearance|510\(k\)|breakthrough therapy"),
    ("DATA", r"topline|phase [123i]|trial (data|results)|met (its )?primary endpoint|clinical data"),
    ("MA", r"acquir|merger|to buy |buyout|takeover|definitive agreement|deal to|go private|tender offer"),
    ("ER", r"beats|tops (estimates|expectations)|raises (guidance|outlook|forecast)|lifts (guidance|outlook|forecast)|record (revenue|quarter|sales)|earnings (beat|surge|jump)|strong (results|quarter|earnings)|blowout"),
    ("UP", r"upgrade|price target|raises target|initiat(es|ed) .*(buy|overweight|outperform)|outperform|overweight"),
    ("EV", r"buyback|repurchase|dividend|contract|partnership|collaborat|award|launch|unveil|order|invest|stake|index|S&P 500"),
]
JUNK = r"\?|^why |stock is a|should you|buy now|own |takeaways|wall street zen|famous investors|top analyst calls|motley|zacks rank|best stocks|stocks to|here's|reasons|vs\.? "
BAD = r"downgrade|plunge|tumble|falls|drops|slump|sinks|lawsuit|probe|investigation|cuts guidance|misses|halt|recall|offering|dilut|bankrupt|delist|fraud|warning"


def kind_of(text):
    for k, rx in KIND_RULES:
        if re.search(rx, text, re.I):
            return k
    return ""


def relevant(title, t, en):
    tl = " " + re.sub(r"[^a-z0-9& ]", " ", title.lower()) + " "
    sn = short_en(en).lower()
    first = sn.split()[0] if sn else ""
    return f" {t.lower()} " in tl or (sn and sn in title.lower()) or (len(first) >= 4 and f" {first}" in tl)


# ---------------------------------------------------------------- 주가 (장전·장중·시간외 등락률)
def universe():
    try:
        rows = list(csv.DictReader(io.StringIO(get(
            "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv").decode())))
        sp = {r["Symbol"].replace(".", "-"): r.get("Security", "") for r in rows}
    except Exception as e:
        print("S&P500 목록 실패", e)
        sp = {}
    return sp


def moves(tickers):
    """{티커: (등락률%, 기준 설명)} — 5분봉(장전·시간외 포함)으로 계산"""
    import pandas as pd
    import yfinance as yf
    out = {}
    n = NOW.astimezone(ET)
    regular_now = is_trading(n.date()) and (9, 30) <= (n.hour, n.minute) < (16, 0)
    tickers = list(dict.fromkeys(tickers))
    for i in range(0, len(tickers), 120):
        chunk = tickers[i:i + 120]
        try:
            df = yf.download(chunk, period="5d", interval="5m", prepost=True, progress=False,
                             group_by="ticker", auto_adjust=False, threads=True)
        except Exception as e:
            print("주가 실패", e)
            continue
        for t in chunk:
            try:
                s = (df[t]["Close"] if len(chunk) > 1 else df["Close"]).dropna()
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[:, 0]
                if s.empty:
                    continue
                s.index = s.index.tz_convert(ET)
                hm = s.index.hour * 60 + s.index.minute
                reg = s[(hm >= 570) & (hm < 960)]
                if reg.empty:
                    continue
                last_t = s.index[-1]
                if regular_now:
                    prev = reg[reg.index.date < n.date()]
                    if prev.empty:
                        continue
                    ref, label = float(prev.iloc[-1]), "장중"
                else:
                    ref = float(reg.iloc[-1])
                    lt = last_t.hour * 60 + last_t.minute
                    if last_t.date() == reg.index[-1].date() and lt >= 960:
                        label = "시간외"
                    elif lt < 570:
                        label = "장전"
                    else:
                        continue  # 장 마감 직후 거래가 아직 없음
                    if last_t <= reg.index[-1]:
                        continue
                out[t] = (round((float(s.iloc[-1]) / ref - 1) * 100, 1), label)
            except Exception:
                continue
    return out


def screen_gainers():
    try:
        import yfinance as yf
        r = yf.screen("day_gainers", count=50)
        return {q["symbol"]: q.get("shortName") or q.get("longName") or "" for q in r.get("quotes", [])
                if (q.get("marketCap") or 0) >= 1e9}
    except Exception as e:
        print("상승 종목 검색 실패", e)
        return {}


PR_QUERIES = ["FDA approves NASDAQ", "FDA approval NYSE", "announces definitive agreement to acquire",
              "topline results NASDAQ positive", "raises full-year guidance", "stocks moving premarket",
              "after-hours movers stock", "upgraded to buy stock"]
TICK_RX = re.compile(r"\((?:NASDAQ|Nasdaq|NYSE|NYSE American|NYSE Arca|OTC)[:\s]+([A-Z]{1,5})\)")


def news_candidates():
    found = {}
    for q in PR_QUERIES:
        for it in gnews(q, days=1):
            for t in TICK_RX.findall(it["t"]):
                found.setdefault(t, it)
    return found


# ---------------------------------------------------------------- 1) 오늘의 호재
def run_today(watch):
    learn_names()
    sd = session_day()
    label = session_label(sd)
    old = load("today.json", {})
    keep = [i for i in old.get("items", []) if i.get("v") == 2] if old.get("session") == label else []
    sp = universe()
    names_en = dict(sp)
    names_en.update(screen_gainers())
    pr = news_candidates()
    cand = list(dict.fromkeys(list(names_en) + EXTRA + watch + list(pr) + [i["ticker"] for i in keep]))
    mv = moves(cand)
    print("시세", len(mv), "종목")
    n = NOW.astimezone(ET)
    since = NOW - timedelta(hours=30)
    pick = [t for t, (p, _) in mv.items() if p >= 5] + [t for t in watch if mv.get(t, (0,))[0] >= 2] + list(pr)
    print("급등 후보", sum(1 for p, _ in mv.values() if p >= 5), "보도자료 후보", len(pr))
    pick = sorted(dict.fromkeys(pick), key=lambda t: (t not in watch, -mv.get(t, (0,))[0]))[:45]
    items = {i["ticker"]: i for i in keep}
    raw = []
    for t in pick:
        en = names_en.get(t, "")
        mover = mv.get(t, (0,))[0] >= 2
        if mover:
            news = [x for x in yahoo_news(t) if x["when"] and x["when"] >= since and not re.search(BAD, x["t"], re.I)
                    and not re.search(JUNK, x["t"], re.I) and relevant(x["t"] + " " + x["sum"][:200], t, en)]
        else:  # 주가 움직임이 확인되지 않은 종목은 보도자료성 큰 소식만
            x = pr.get(t)
            news = [{**x, "sum": ""}] if x and not re.search(BAD + "|" + JUNK, x["t"], re.I) and kind_of(x["t"]) in ("FDA", "MA", "DATA") else []
        if not news:
            continue  # 기사로 확인되지 않은 급등은 넣지 않습니다
        best = sorted(news, key=lambda x: (kind_of(x["t"] + " " + x["sum"]) == "", -(x["when"] or NOW).timestamp()))[0]
        k = kind_of(best["t"] + " " + best["sum"])
        if not k:
            continue  # 오른 이유로 보기 어려운 기사
        if not en:
            m = re.search(r"([A-Z][\w.&'-]*(?: [A-Z][\w.&'-]*){0,3}) \((?:NASDAQ|Nasdaq|NYSE)[^)]*" + t + r"\)", best["t"])
            en = m.group(1) if m else ""
        raw.append((t, en, best, k))
    titles = ko_many([b["t"] for _, _, b, _ in raw])
    sums = ko_many([(b["sum"] or "").split(". ")[0][:220] for _, _, b, _ in raw])
    for (t, en, b, k), tk, sk in zip(raw, titles, sums):
        p = mv.get(t)
        when = b["when"].astimezone(KST) if b["when"] else None
        reason = tk.rstrip(".") + "."
        if sk and sk not in tk:
            reason += " " + sk.rstrip(".") + "."
        if when:
            reason += f" (기사 {when.month}/{when.day} {when.hour:02d}:{when.minute:02d} 한국 시각)"
        items[t] = {"v": 2, "ticker": t, "name": name_of(t, en), "move": (f"{p[0]:+.1f}% ({p[1]})" if p else ""),
                    "kind": k, "reason": reason, "source": b["s"] + " · 자동 번역", "url": b["u"]}
    for t, it in items.items():  # 이미 있던 종목은 등락률만 새로
        if t in mv and it.get("move") is not None:
            it["move"] = f"{mv[t][0]:+.1f}% ({mv[t][1]})"
    pr_k = {"FDA": 0, "MA": 1, "DATA": 2, "ER": 3, "UP": 4, "EV": 5}
    def mvv(i):
        m = re.match(r"([+-]?\d+(\.\d+)?)", i.get("move") or "")
        return float(m.group(1)) if m else 0
    final = sorted(items.values(), key=lambda i: (i["ticker"] not in watch, pr_k.get(i["kind"], 9), -mvv(i)))[:20]
    import yfinance as yf
    for i in final:
        if i["name"] in ("", i["ticker"]):
            try:
                info = yf.Ticker(i["ticker"]).get_info()
                i["name"] = short_en(info.get("shortName") or info.get("longName") or "") or i["ticker"]
            except Exception:
                pass
    base = "장 시작 전" if n.hour < 9 or (n.hour == 9 and n.minute < 30) or not is_trading(n.date()) else "장중"
    note = (f"{base} 기준 자동 수집 · 등락률 옆 괄호는 장전·장중·시간외 구분 · 기사 제목·요약은 자동 번역이라 어색할 수 있습니다 · "
            "급등했어도 관련 기사가 확인되지 않은 종목은 넣지 않습니다")
    save("today.json", {"session": label, "updatedAt": iso(), "note": note, "items": final})
    print("오늘의 호재", len(final))
    return final


# ---------------------------------------------------------------- 2) 1달 앞 일정
def nasdaq_json(path):
    try:
        return json.loads(get("https://api.nasdaq.com/api/" + path, headers={
            "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/"}, timeout=20))
    except Exception as e:
        print("나스닥 실패", path, e)
        return None


def money(s):
    try:
        return float(re.sub(r"[^0-9.\-]", "", s or "") or 0)
    except Exception:
        return 0.0


def earnings_rows(days=31):
    rows = []
    start = NOW.astimezone(ET).date()
    for i in range(days + 1):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        j = nasdaq_json(f"calendar/earnings?date={d.isoformat()}")
        for r in ((j or {}).get("data") or {}).get("rows") or []:
            tm = r.get("time", "")
            rows.append({"symbol": r.get("symbol", ""), "name": r.get("name", ""), "reportDate": d.isoformat(),
                         "estimate": re.sub(r"[^0-9.\-]", "", r.get("epsForecast") or ""),
                         "timeOfTheDay": "pre-market" if "pre" in tm else "post-market" if "after" in tm else "",
                         "cap": money(r.get("marketCap"))})
        time.sleep(0.4)
    if not rows:  # 나스닥이 막히면 야후 실적 달력
        try:
            import yfinance as yf
            cal = yf.Calendars(start=start, end=start + timedelta(days=days))
            df = cal.get_earnings_calendar(limit=500, filter_most_active=False)
            for sym, r in df.iterrows():
                dd = r.get("Event Start Date")
                tm = str(r.get("Timing") or "")
                rows.append({"symbol": sym, "name": r.get("Company", ""), "reportDate": str(dd)[:10],
                             "estimate": "" if r.get("EPS Estimate") != r.get("EPS Estimate") else str(r.get("EPS Estimate") or ""),
                             "timeOfTheDay": "pre-market" if "BMO" in tm else "post-market" if "AMC" in tm else "",
                             "cap": float(r.get("Marketcap") or 0)})
        except Exception as e:
            print("야후 실적 달력 실패", e)
    return rows


def third_friday(y, m):
    d = date(y, m, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def market_items(d0, d1):
    out = []
    for ds, nm in {**HOLIDAYS, **EARLY_CLOSE}.items():
        d = date.fromisoformat(ds)
        if d0 <= d <= d1:
            early = ds in EARLY_CLOSE
            out.append({"date": ds, "kind": "MK", "name": "뉴욕증시", "event": f"{nm} {'조기 폐장(미국 오후 1시)' if early else '휴장'}",
                        "time": "한국 다음 날 새벽 3시 마감" if early else "하루 종일", "us": ds,
                        "source": "NYSE", "url": "https://www.nyse.com/markets/hours-calendars", "auto": "mk"})
    y, m = d0.year, d0.month
    for _ in range(3):
        f = third_friday(y, m)
        if not is_trading(f):
            f -= timedelta(days=1)
        if d0 <= f <= d1:
            q = m in (3, 6, 9, 12)
            out.append({"date": f.isoformat(), "kind": "IDX" if q else "MK", "name": "옵션 만기",
                        "event": "쿼드러플 위칭(주식·지수 선물·옵션 동시 만기)과 S&P 500 정기 변경 적용일" if q else "월간 옵션 만기일",
                        "time": f"한국 {f.month}/{f.day} 밤 ~ 다음 날 새벽", "us": f.isoformat(), "source": "Cboe",
                        "url": "https://www.cboe.com/about/hours/", "auto": "mk"})
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


PDUFA_RX = re.compile(r"PDUFA[^.]{0,60}?((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2},? 20\d\d)", re.I)


def fda_items(d0, d1):
    out = {}
    for q in ["PDUFA date", "PDUFA target action date", "FDA decision expected"]:
        for it in gnews(q, days=30):
            m = PDUFA_RX.search(it["t"])
            tk = TICK_RX.findall(it["t"])
            if not m or not tk:
                continue
            try:
                d = datetime.strptime(re.sub(r"[.,]", "", m.group(1)).replace("Sept", "Sep")[:3] + " " +
                                      " ".join(re.sub(r"[.,]", "", m.group(1)).split()[1:]), "%b %d %Y").date()
            except Exception:
                continue
            if d0 <= d <= d1:
                out[(tk[0], d)] = {"date": d.isoformat(), "kind": "FDA", "ticker": tk[0], "name": name_of(tk[0]),
                                   "event": ko(it["t"]), "time": f"{d.month}/{d.day} 밤 ~ {(d + timedelta(days=1)).month}/{(d + timedelta(days=1)).day} 오후 사이 발표(시각 미정)",
                                   "us": f"{d.month}/{d.day} 중", "source": it["s"] + " · 자동 번역", "url": it["u"], "auto": "fda"}
    return list(out.values())


def run_upcoming(watch):
    learn_names()
    d0 = NOW.astimezone(KST).date()
    d1 = d0 + timedelta(days=30)
    old = load("upcoming.json", {}).get("items", [])
    keep = []
    for i in old:
        if i.get("auto"):
            continue  # 자동 항목은 매번 새로 만듭니다
        ds = i.get("date", "")
        if re.match(r"^\d{4}-\d\d-\d\d$", ds):
            if ds < d0.isoformat() or i.get("done"):
                continue
            if i.get("kind") == "ER":
                continue  # 실적은 나스닥 달력으로 다시 만듭니다
        else:
            i.setdefault("addedAt", iso())
            if (NOW - datetime.fromisoformat(i["addedAt"].replace("Z", "+00:00"))).days > 40:
                continue
        keep.append(i)
    rows = earnings_rows()
    # 실적 달력(전체)
    save("earnings.json", {"updatedAt": iso(), "rows": [{k: v for k, v in r.items() if k != "cap"} for r in rows]})
    # 주요 실적 묶음 (시가총액 200억 달러 이상 + 관심종목)
    groups = {}
    for r in rows:
        if not r["symbol"] or (r["cap"] < 2e10 and r["symbol"] not in watch):
            continue
        ud = date.fromisoformat(r["reportDate"])
        post = r["timeOfTheDay"] == "post-market"
        kd = ud + timedelta(days=1) if post else ud
        groups.setdefault((kd, r["timeOfTheDay"]), []).append(r)
    er = []
    for (kd, tm), lst in sorted(groups.items()):
        if not (d0 <= kd <= d1):
            continue
        lst.sort(key=lambda r: -r["cap"])
        ud = kd - timedelta(days=1) if tm == "post-market" else kd
        names = ", ".join(f"{name_of(r['symbol'], r['name'])}({r['symbol']})" for r in lst[:14])
        more = f" 외 {len(lst) - 14}곳" if len(lst) > 14 else ""
        when = {"pre-market": "밤(미국 장 시작 전 발표)", "post-market": "아침 6시 이후(미국 장 마감 후 발표)"}.get(tm, "발표 시각 미정")
        er.append({"date": kd.isoformat(), "kind": "ER", "name": "주요 실적", "event": f"주요 실적: {names}{more}",
                   "time": f"{kd.month}/{kd.day} {when}", "us": f"{ud.month}/{ud.day} " + {"pre-market": "장 시작 전", "post-market": "장 마감 후"}.get(tm, ""),
                   "note": "시가총액 200억 달러 이상과 관심종목", "source": "Nasdaq 실적 달력",
                   "url": "https://www.nasdaq.com/market-activity/earnings", "auto": "er"})
    fda = fda_items(d0, d1)
    have = {(i.get("ticker"), i.get("date")) for i in keep}
    fda = [f for f in fda if (f["ticker"], f["date"]) not in have]
    items = keep + er + fda + market_items(d0, d1)
    items.sort(key=lambda i: (i.get("date") if re.match(r"^\d{4}", i.get("date", "")) else "9" + i.get("date", "")))
    save("upcoming.json", {"updatedAt": iso(), "items": items})
    print("1달 앞 일정", len(items), "(실적 묶음", len(er), "FDA", len(fda), ")")
    return items, rows


# ---------------------------------------------------------------- 3) 시장 일정
ECO = [  # (영문 이름 규칙, 한국어 제목, 설명)
    (r"Nonfarm Payrolls|Non-Farm Payrolls|Nonfarm Employment Change", "고용보고서(비농업 일자리)", "미국 고용 상황을 보여 주는 가장 중요한 지표입니다."),
    (r"^Unemployment Rate", "실업률", ""),
    (r"^(Core )?CPI\b|Consumer Price Index", "소비자물가(CPI)", "물가 흐름을 보여 주며 금리 전망에 큰 영향을 줍니다."),
    (r"Core PCE|Core Personal Consumption", "근원 PCE 물가", "연준이 가장 중요하게 보는 물가 지표입니다."),
    (r"^(Core )?PPI\b|Producer Price Index", "생산자물가(PPI)", ""),
    (r"^GDP \((QoQ|Annualized)|^GDP$|Gross Domestic Product", "국내총생산(GDP)", ""),
    (r"^(Core )?Retail Sales", "소매판매", "소비 흐름을 보여 줍니다."),
    (r"ISM Manufacturing", "ISM 제조업 지수", ""),
    (r"ISM Non-Manufacturing|ISM Services", "ISM 서비스업 지수", ""),
    (r"JOLT", "구인 건수(JOLTS)", ""),
    (r"Initial Jobless Claims", "신규 실업수당 청구", ""),
    (r"CB Consumer Confidence|Conference Board Consumer Confidence", "소비자신뢰지수(콘퍼런스보드)", ""),
    (r"Michigan Consumer Sentiment|Michigan Sentiment", "미시간대 소비자심리", ""),
    (r"FOMC (Meeting )?Minutes", "FOMC 의사록 공개", ""),
    (r"ADP (Nonfarm|Employment)", "ADP 민간 고용", ""),
    (r"Durable Goods Orders", "내구재 주문", ""),
    (r"Housing Starts", "주택 착공", ""),
    (r"Existing Home Sales", "기존 주택 판매", ""),
    (r"Fed Chair .*Speaks|Powell Speaks|Warsh Speaks", "연준 의장 발언", ""),
]


ALLNAMES = set()


def run_macro():
    d0 = NOW.astimezone(KST).date()
    d1 = d0 + timedelta(days=30)
    items = []
    seen = set()
    start = NOW.astimezone(ET).date()
    for i in range(31):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        j = nasdaq_json(f"calendar/economicevents?date={d.isoformat()}")
        for r in ((j or {}).get("data") or {}).get("rows") or []:
            if "United States" not in (r.get("country") or ""):
                continue
            ALLNAMES.add(r.get("eventName") or "")
            nm = r.get("eventName") or ""
            hit = next((e for e in ECO if re.search(e[0], nm, re.I)), None)
            if not hit or (d, hit[1]) in seen:
                continue
            seen.add((d, hit[1]))
            tm = (r.get("gmt") or "").strip()
            when, us = "", f"{d.month}/{d.day}"
            mm = re.match(r"(\d{1,2}):(\d\d)", tm)
            if mm:
                k = kst_of_et(d, int(mm.group(1)), int(mm.group(2)))
                when = f"한국 {k.month}/{k.day} {'밤' if k.hour >= 18 else '새벽' if k.hour < 6 else '오전' if k.hour < 12 else '오후'} {k.hour if k.hour <= 12 else k.hour - 12}:{k.minute:02d}"
                us += f" 오전 {mm.group(1)}:{mm.group(2)}(동부)" if int(mm.group(1)) < 12 else f" {tm}(동부)"
                kd = k.date()
            else:
                kd = d
            det = hit[2]
            cl = lambda v: re.sub(r"&\w+;|\s", "", str(v or ""))
            if cl(r.get("consensus")) or cl(r.get("previous")):
                det = (det + " " if det else "") + f"예상치 {cl(r.get('consensus')) or '-'}, 이전 {cl(r.get('previous')) or '-'}."
            items.append({"date": kd.isoformat(), "kind": "ECO", "title": hit[1], "time": when, "us": us,
                          "detail": det, "source": "Nasdaq 경제 일정", "url": "https://www.nasdaq.com/market-activity/economic-calendar"})
        time.sleep(0.4)
    for ds in FOMC:
        d = date.fromisoformat(ds)
        k = kst_of_et(d, 14, 0)
        if d0 <= k.date() <= d1:
            items.append({"date": k.date().isoformat(), "kind": "FED", "title": "FOMC 금리 결정",
                          "time": f"한국 {k.month}/{k.day} 새벽 {k.hour}:00 발표, 30분 뒤 의장 기자회견", "us": f"{d.month}/{d.day} 오후 2시(동부)",
                          "detail": "미국 기준금리 결정입니다. 결과와 앞으로의 금리 전망에 따라 시장이 크게 움직일 수 있습니다.",
                          "source": "연방준비제도", "url": FOMC_URL})
    for ds, nm in {**HOLIDAYS, **EARLY_CLOSE}.items():
        d = date.fromisoformat(ds)
        if d0 <= d <= d1:
            items.append({"date": ds, "kind": "MK", "title": f"뉴욕증시 {nm} {'조기 폐장' if ds in EARLY_CLOSE else '휴장'}", "time": "",
                          "us": ds, "detail": "미국 오후 1시(한국 새벽 3시)에 장을 닫습니다." if ds in EARLY_CLOSE else "미국 증시가 열리지 않습니다.",
                          "source": "NYSE", "url": "https://www.nyse.com/markets/hours-calendars"})
    # 진행 중인 큰 흐름: 오늘 시장 기사 제목 3개(자동 번역)
    heads = [h for h in gnews("stock market today Wall Street", days=1) if h["t"]][:3]
    trs = ko_many([h["t"] for h in heads])
    for h, tr in zip(heads, trs):
        items.append({"date": "", "kind": "RISK", "title": "오늘 시장 흐름", "time": "", "us": "", "detail": tr,
                      "source": h["s"] + " · 자동 번역", "url": h["u"]})
    items.sort(key=lambda i: i["date"] or "0")
    if not any(i["kind"] == "ECO" for i in items):
        for i in load("macro.json", {}).get("items", []):  # 경제 일정을 못 받았으면 예전 것 중 남은 날짜 유지
            if i.get("kind") == "ECO" and i.get("date", "") >= d0.isoformat():
                items.append(i)
    save("macro.json", {"updatedAt": iso(), "items": items})
    open(os.path.join(ROOT, "tools", "eco_names.txt"), "w").write("\n".join(sorted(ALLNAMES)))
    print("시장 일정", len(items))


# ---------------------------------------------------------------- 4) 실적 미리보기 (숫자 단서)
def preview_one(t, row):
    import yfinance as yf
    y = yf.Ticker(t)
    sig = []
    url = f"https://finance.yahoo.com/quote/{t}/analysis"
    est = row.get("estimate") or ""
    try:
        tr = y.eps_trend
        if tr is not None and "0q" in tr.index:
            c, a = float(tr.loc["0q", "current"]), float(tr.loc["0q", "90daysAgo"])
            if a == a and c == c and a:
                ch = (c / a - 1) * 100
                sig.append({"category": "애널리스트 전망 변화", "direction": "긍정" if ch > 1 else "부정" if ch < -1 else "중립",
                            "date": "최근 90일", "finding": f"이번 분기 주당순이익(EPS) 예상치가 90일 전 {a:.2f}달러에서 지금 {c:.2f}달러로 {ch:+.1f}% 바뀌었습니다.",
                            "source": "Yahoo Finance 애널리스트 전망", "url": url})
            if c == c:
                est = est or f"{c:.2f}"
    except Exception:
        pass
    try:
        rv = y.eps_revisions
        if rv is not None and "0q" in rv.index:
            u, d = int(rv.loc["0q"].get("upLast30days") or 0), int(rv.loc["0q"].get("downLast30days") or rv.loc["0q"].get("downLast30Days") or 0)
            if u or d:
                sig.append({"category": "전망 수정 건수", "direction": "긍정" if u > d else "부정" if d > u else "중립", "date": "최근 30일",
                            "finding": f"최근 30일 동안 EPS 예상치를 올린 애널리스트 {u}명, 내린 애널리스트 {d}명입니다.",
                            "source": "Yahoo Finance 애널리스트 전망", "url": url})
    except Exception:
        pass
    try:
        rvn = y.revenue_estimate
        if rvn is not None and "0q" in rvn.index:
            g = rvn.loc["0q"].get("growth")
            avg = rvn.loc["0q"].get("avg")
            if g == g and g is not None and avg:
                sig.append({"category": "매출 전망", "direction": "긍정" if g > 0.05 else "부정" if g < 0 else "중립", "date": "이번 분기",
                            "finding": f"이번 분기 매출 예상 평균은 {avg / 1e8:,.0f}억 달러로, 작년 같은 분기보다 {g * 100:+.1f}%입니다.",
                            "source": "Yahoo Finance 애널리스트 전망", "url": url})
    except Exception:
        pass
    try:
        eh = y.earnings_history
        if eh is not None and len(eh):
            sp = [float(x) for x in eh["surprisePercent"].dropna().tolist()[-4:]]
            if sp:
                beat = sum(1 for x in sp if x > 0)
                sig.append({"category": "과거 실적 기록", "direction": "긍정" if beat >= 3 else "부정" if beat <= 1 else "중립", "date": "최근 4분기",
                            "finding": f"최근 {len(sp)}분기 중 {beat}번 예상보다 좋은 EPS를 냈습니다(평균 차이 {sum(sp) / len(sp) * 100:+.1f}%).",
                            "source": "Yahoo Finance 실적 기록", "url": url})
    except Exception:
        pass
    try:
        ud = y.upgrades_downgrades
        if ud is not None and len(ud):
            ud = ud[ud.index >= (NOW - timedelta(days=30)).replace(tzinfo=None)] if ud.index.tz is None else ud[ud.index >= NOW - timedelta(days=30)]
            up = int((ud["Action"] == "up").sum()) if "Action" in ud else 0
            dn = int((ud["Action"] == "down").sum()) if "Action" in ud else 0
            if len(ud):
                last = ud.iloc[0]
                sig.append({"category": "증권사 의견", "direction": "긍정" if up > dn else "부정" if dn > up else "중립", "date": "최근 30일",
                            "finding": f"최근 30일 의견 변경 {len(ud)}건(상향 {up}, 하향 {dn}). 가장 최근: {last.get('Firm', '')} '{last.get('ToGrade', '')}'.",
                            "source": "Yahoo Finance 증권사 의견", "url": f"https://finance.yahoo.com/quote/{t}/analysis"})
    except Exception:
        pass
    seen_t = set()
    news = []
    for x in yahoo_news(t):
        if x["when"] and x["when"] >= NOW - timedelta(days=21) and x["t"] not in seen_t and relevant(x["t"], t, row.get("name", "")) \
                and not re.search(JUNK, x["t"], re.I):
            seen_t.add(x["t"])
            news.append(x)
    news = news[:3]
    for x, tr in zip(news, ko_many([x["t"] for x in news])):
        bad = re.search(BAD, x["t"], re.I)
        good = re.search(r"beat|raise|upgrade|record|strong|surge|growth|jump", x["t"], re.I)
        sig.append({"category": "최근 뉴스", "direction": "부정" if bad else "긍정" if good else "중립",
                    "date": f"{x['when'].astimezone(KST).month}/{x['when'].astimezone(KST).day}", "finding": tr,
                    "source": x["s"] + " · 자동 번역", "url": x["u"]})
    if not sig:
        return None
    p = sum(s["direction"] == "긍정" for s in sig)
    n = sum(s["direction"] == "부정" for s in sig)
    verdict = "긍정 단서 우세" if p >= n + 2 else "부정 단서 우세" if n >= p + 2 else "엇갈림"
    ud = date.fromisoformat(row["reportDate"])
    post = row.get("timeOfTheDay") == "post-market"
    kd = ud + timedelta(days=1) if post else ud
    return {"ticker": t, "name": name_of(t, row.get("name", "")), "updatedAt": iso(),
            "nextEarnings": {"kst": f"{md(kd)} " + ("아침 6시 이후" if post else "밤" if row.get("timeOfTheDay") == "pre-market" else "(시각 미정)"),
                             "us": f"{ud.month}/{ud.day} " + ("장 마감 후" if post else "장 시작 전" if row.get("timeOfTheDay") == "pre-market" else ""),
                             "estimate": "" if est in ("nan", "None") else est},
            "verdict": verdict,
            "summary": f"자동으로 모은 숫자 단서 {len(sig)}개 중 긍정 {p}개, 부정 {n}개입니다. 애널리스트 전망·과거 기록·최근 기사 제목만 본 것이라 매장 방문·카드 결제 같은 현장 자료는 들어 있지 않습니다.",
            "signals": sig,
            "caveats": "숫자와 기사 제목을 규칙에 따라 자동으로 모은 자료입니다. 주가는 실적 자체보다 '기대 대비'에 반응하고, 기사 번역은 어색할 수 있습니다. 투자 권유가 아닙니다."}


def run_preview(rows, watch):
    d0 = NOW.astimezone(ET).date()
    soon = [r for r in rows if r["symbol"] and d0 <= date.fromisoformat(r["reportDate"]) <= d0 + timedelta(days=10)
            and (r["cap"] >= 5e10 or r["symbol"] in watch)]
    soon.sort(key=lambda r: (r["symbol"] not in watch, r["reportDate"], -r["cap"]))
    soon = soon[:14]
    pdir = os.path.join(DATA, "preview")
    os.makedirs(pdir, exist_ok=True)
    keep = {r["symbol"] for r in soon}
    idx = []
    for r in soon:
        try:
            rep = preview_one(r["symbol"], r)
        except Exception as e:
            print("미리보기 실패", r["symbol"], e)
            rep = None
        if rep:
            save(f"preview/{r['symbol']}.json", rep)
            idx.append({"ticker": r["symbol"], "name": rep["name"], "updatedAt": rep["updatedAt"], "source": "upcoming", "verdict": rep["verdict"],
                        "date": r["reportDate"]})
    for f in os.listdir(pdir):
        t = f[:-5]
        if f.endswith(".json") and f != "index.json" and t not in keep:
            os.remove(os.path.join(pdir, f))
    save("preview/index.json", {"updatedAt": iso(), "items": idx})
    print("실적 미리보기", len(idx))


# ---------------------------------------------------------------- 5) 자동 선별 (my 사이트)
def run_picks(watch):
    today = load("today.json", {}).get("items", [])
    up = load("upcoming.json", {}).get("items", [])
    pv = load("preview/index.json", {}).get("items", [])
    risk = {"FDA": "승인 소식은 이미 주가에 반영됐을 수 있고, 출시·판매 속도가 기대보다 느릴 수 있습니다.",
            "DATA": "임상 결과는 해석이 갈리기 쉽고, 다음 단계 실패 가능성이 남아 있습니다.",
            "MA": "인수 가격이 정해지면 주가가 그 가격 근처에 묶이고, 규제 심사로 무산될 수 있습니다.",
            "ER": "실적 발표 직후 급등은 며칠 안에 되돌려지는 경우가 많습니다.",
            "UP": "증권사 의견 하나로 오른 주가는 쉽게 되돌아올 수 있습니다.",
            "EV": "단발성 소식이면 상승이 오래가지 않을 수 있습니다."}
    def mvv(i):
        m = re.match(r"([+-]?\d+(\.\d+)?)", i.get("move") or "")
        return float(m.group(1)) if m else 0
    tl = sorted([i for i in today if mvv(i) > 0], key=lambda i: (i["ticker"] not in watch, -mvv(i)))[:5]
    tp = [{"ticker": i["ticker"], "name": i["name"], "level": "고위험" if mvv(i) >= 15 or i["kind"] in ("FDA", "DATA") else "보통",
           "catalyst": f"오늘 장 ({i['move']})", "thesis": i["reason"], "risk": risk.get(i["kind"], risk["EV"]),
           "source": i.get("source"), "url": i.get("url")} for i in tl]
    d0 = NOW.astimezone(KST).date().isoformat()
    upl = []
    for p in pv:
        if p.get("verdict") == "긍정 단서 우세":
            upl.append({"ticker": p["ticker"], "name": p["name"], "level": "보통", "catalyst": f"실적 발표 {p.get('date', '')}",
                        "thesis": "애널리스트 전망·과거 실적 기록 등 숫자 단서가 긍정 쪽으로 기울어 있습니다(실적 미리보기 참고).",
                        "risk": "기대가 이미 높으면 좋은 실적에도 주가가 내릴 수 있습니다.", "source": "실적 미리보기", "url": ""})
    for i in up:
        if i.get("kind") in ("FDA", "DATA") and i.get("ticker") and d0 <= i.get("date", "") <= (NOW.astimezone(KST).date() + timedelta(days=14)).isoformat():
            upl.append({"ticker": i["ticker"], "name": i.get("name", ""), "level": "고위험", "catalyst": f"{i['date']} {i.get('event', '')}"[:80],
                        "thesis": i.get("event", ""), "risk": "결과에 따라 크게 오르거나 내릴 수 있습니다(승인·성공 여부 예측 아님).",
                        "source": i.get("source"), "url": i.get("url")})
    save("picks.json", {"updatedAt": iso(), "today": tp, "upcoming": upl[:5]})


# ---------------------------------------------------------------- 6) 캘린더 구독 파일(.ics)
def ics_text(s):
    return re.sub(r"([,;\\])", r"\\\1", str(s or "")).replace("\n", "\\n")


def run_ics():
    up = load("upcoming.json", {}).get("items", [])
    mac = load("macro.json", {}).get("items", [])
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//pepper//radar//KO", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Pepper 미국주식 일정", "X-WR-TIMEZONE:Asia/Seoul", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"]
    stamp = NOW.strftime("%Y%m%dT%H%M%SZ")
    n = 0
    for i in up + [m for m in mac if m.get("date")]:
        ds = i.get("date", "")
        if not re.match(r"^\d{4}-\d\d-\d\d$", ds):
            continue
        d = date.fromisoformat(ds)
        title = i.get("title") or (f"{i['ticker']} " if i.get("ticker") else "") + (i.get("event") or "")
        tag = {"ER": "실적", "FDA": "FDA", "DATA": "임상", "FED": "연준", "ECO": "지표", "MK": "시장", "IDX": "지수"}.get(i.get("kind"), "일정")
        uid = re.sub(r"[^A-Za-z0-9]", "", f"{ds}{i.get('kind')}{i.get('ticker', '')}{title[:20]}")[:60] + "@pepper"
        lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
                  f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
                  f"SUMMARY:{ics_text('[' + tag + '] ' + title[:120])}",
                  f"DESCRIPTION:{ics_text(' · '.join(x for x in [i.get('time'), i.get('detail') or i.get('note'), i.get('url')] if x))}",
                  "TRANSP:TRANSPARENT", "END:VEVENT"]
        n += 1
    lines.append("END:VCALENDAR")
    with open(os.path.join(DATA, "calendar.ics"), "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    print("캘린더 일정", n)


# ---------------------------------------------------------------- 7) my 사이트 암호화
def run_my(old, watch):
    pw = os.environ.get("MY_PASS", "")
    if not pw:
        print("MY_PASS 비밀값이 없어 my 사이트는 건너뜁니다")
        return
    if os.path.exists(os.path.join(ROOT, "my", "data.enc")) and old is None:
        print("비밀번호가 기존 데이터와 맞지 않아 my 사이트를 덮어쓰지 않습니다")
        return
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    pv = {}
    for i in load("preview/index.json", {}).get("items", []):
        r = load(f"preview/{i['ticker']}.json", None)
        if r:
            pv[i["ticker"]] = r
    blob = {"today": load("today.json", None), "upcoming": load("upcoming.json", None), "macro": load("macro.json", None),
            "picks": load("picks.json", None), "watch": {"tickers": watch, "updatedAt": (old or {}).get("watch", {}).get("updatedAt", iso())},
            "preview": pv}
    raw = json.dumps(blob, ensure_ascii=False).encode()
    salt, ivb, it = os.urandom(16), os.urandom(12), 1000000
    ct = AESGCM(my_key(pw.encode(), salt, it)).encrypt(ivb, raw, None)
    b = lambda x: base64.b64encode(x).decode()
    json.dump({"v": 1, "iter": it, "salt": b(salt), "iv": b(ivb), "ct": b(ct)}, open(os.path.join(ROOT, "my", "data.enc"), "w"))
    print("my 사이트 갱신", len(raw), "bytes")


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    old = my_old()
    watch = watch_list(old)
    print("모드", mode, "관심종목", len(watch))
    steps = []
    if mode in ("today", "all"):
        steps.append(("today", lambda: run_today(watch)))
    if mode in ("full", "all"):
        def full():
            _, rows = run_upcoming(watch)
            run_macro()
            run_preview(rows, watch)
            run_ics()
        steps.append(("full", full))
    for nm, fn in steps:
        try:
            fn()
        except Exception as e:  # 한 부분이 실패해도 나머지는 진행
            import traceback
            traceback.print_exc()
            print("실패:", nm, e)
    for nm, fn in [("picks", lambda: run_picks(watch)), ("my", lambda: run_my(old, watch))]:
        try:
            fn()
        except Exception as e:
            print("실패:", nm, e)
    save_tcache()
    json.dump(NAMES, open(NAMES_P, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


if __name__ == "__main__":
    main()
