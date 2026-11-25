#!/usr/bin/env python3
"""GTA 6 PC release checker.

No AI. Plain rules. Python standard library only.

What it does, in order:
  1. Reads news feeds (RSS) and the Steam store, and keeps only items about GTA 6.
  2. Splits them into sentences and looks for sentences that are about the PC version
     (PC / Steam / Windows / Epic Games Store).
  3. Pulls dates out of those sentences. Only dates from today up to 2 years ahead count.
     A date only counts if it sits next to a PC word (closer to "PC" than to "PS5/Xbox").
  4. Scores sources: official (Rockstar, Take-Two, Steam, Epic) > trusted gaming sites > others.
  5. Writes result.json, which the web page (index.html) displays.

Usage:
  python check.py                          # normal run (needs internet)
  python check.py --sample samples.json    # test the rules on fake articles
  python check.py --today 2026-11-25       # pretend today is another day
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

# ----------------------------------------------------------------------------
# Settings you can change
# ----------------------------------------------------------------------------

RESULT_FILE = "result.json"
HORIZON_DAYS = 365 * 2 + 1      # ignore dates further away than ~2 years
MAX_AGE_DAYS = 120              # ignore articles older than this
RELEASED_MAX_AGE_DAYS = 30      # "out now" news must be fresh
USER_AGENT = "Mozilla/5.0 (compatible; gta6-pc-release-checker/1.0)"

# Google News searches (they also find Rockstar's own site and the big gaming sites)
def gnews(query):
    return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
            + "&hl=en-US&gl=US&ceid=US:en")

FEEDS = [
    gnews('"GTA 6" PC release date'),
    gnews('"GTA VI" PC version'),
    gnews('"Grand Theft Auto VI" Steam OR "Epic Games Store" OR PC'),
    gnews('GTA 6 PC site:rockstargames.com'),
    gnews('Grand Theft Auto VI PC site:take2games.com'),
    gnews('GTA 6 PC site:techpowerup.com'),
    gnews('GTA 6 PC site:pcgamer.com'),
    gnews('GTA 6 PC site:ign.com'),
    gnews('GTA 6 PC site:gamespot.com'),
    # Direct feeds (a few may change address over time; a dead one is just skipped)
    "https://www.techpowerup.com/rss/news",
    "https://www.pcgamer.com/rss/",
    "https://www.eurogamer.net/feed",
    "https://www.videogameschronicle.com/feed/",
    "https://www.rockpapershotgun.com/feed",
    "https://www.gamespot.com/feeds/news/",
]

OFFICIAL_DOMAINS = ("rockstargames.com", "take2games.com", "take2interactive.com",
                    "steampowered.com", "epicgames.com")
TRUSTED_DOMAINS = ("ign.com", "gamespot.com", "pcgamer.com", "techpowerup.com",
                   "eurogamer.net", "videogameschronicle.com", "gamesradar.com",
                   "polygon.com", "kotaku.com", "rockpapershotgun.com", "reuters.com",
                   "bloomberg.com", "theverge.com", "pcgamesn.com", "gamesindustry.biz",
                   "gematsu.com", "destructoid.com", "windowscentral.com",
                   "tomshardware.com", "dexerto.com", "ign.com")

W_OFFICIAL, W_TRUSTED, W_OTHER = 5, 2, 1
OFFICIAL_SCORE = 4   # "official" = one official source, or two trusted sources agreeing
MIN_SCORE = 2        # below this (e.g. one random blog) the finding is ignored

# ----------------------------------------------------------------------------
# Word lists (the "lots of if statements" part)
# ----------------------------------------------------------------------------

GTA_RE = re.compile(r"\b(?:GTA\s*(?:6|VI)|Grand\s+Theft\s+Auto\s*(?:6|VI)|GTA6|GTAVI)\b", re.I)

PC_RE = re.compile(r"\b(?:PC|PCs|Steam|Windows)\b|(?i:\bEpic Games Store\b)")
CONSOLE_RE = re.compile(r"(?i)\b(?:PS5(?: Pro)?|PS4|PlayStation(?: ?[45])?|Xbox(?: Series [XS]|"
                        r" One)?|consoles?|Switch 2)\b")

RELEASE_WORD_RE = re.compile(r"(?i)(releas|launch|arriv|coming|come|debut|land|available|"
                             r"\bout\b|port|drop|ship|date|slated|scheduled|planned|"
                             r"set for|target|expected|due|delay|postpon|pushed|pushes|moved|"
                             r"moves)")

# Confirmation phrases (the sentence must also mention PC and GTA 6)
CONFIRM_RE = re.compile(r"(?i)(coming to|come to|comes to|heading to|headed to|arrives? on|"
                        r"will (?:launch|release|arrive|come|be (?:available|released|launching))|"
                        r"launch(?:es|ing)? on|releas(?:e|es|ing) on|confirmed|announced|"
                        r"officially|store page|listed on|release date)")

# "No PC" / "not yet" phrases. Sentences with these never count as confirmation or dates.
NEG_RE = re.compile(r"(?i)(no plans|not coming|isn't coming|is not coming|won't|will not|"
                    r"not (?:yet )?(?:been )?(?:announced|confirmed|revealed|available)|"
                    r"hasn't|has not|haven't|have not|yet to|unavailable|exclusive|"
                    r"only on (?:PS5|PlayStation|consoles?)|\bno\b[^.?!]{0,30}\bPC\b|"
                    r"without (?:a )?PC|"
                    r"skip(?:s|ping)? PC|never|no word|no news|no info|no sign|"
                    r"no mention|nothing about|nothing on|no official|doesn't|does not)")

# Questions, wishes, "if" talk: not facts
HYPO_RE = re.compile(r"(?i)(\?|^(?:will|when|is|are|does|do|can|could|should|why|how)\b|"
                     r"\b(?:hope|hoping|wish|petition|wishlist|speculat\w*|imagine|if|"
                     r"whether|what if|ever|demand\w*|begging|asking|wants?|wonder\w*|"
                     r"waiting|awaiting|curious|when will|when is|when does|when can)\b)")

# Rumor words: the sentence may still count, but only as "rumored"
RUMOR_RE = re.compile(r"(?i)(rumou?r\w*|leak\w*|insider|reportedly|allegedly|supposedly|"
                      r"unconfirmed|tipped|claims?\b|predict\w*|likely|could|(?-i:\bmay\b)|might|"
                      r"expected|expects?|appears?|hints?\b|suggests?|sources?\b|believed|"
                      r"speculat\w*|according to a|probably|possibly|perhaps)")

# "Out now" phrases (the sentence must also mention PC and GTA 6)
RELEASED_RE = re.compile(r"(?i)\b(?:now available|out now|available now|now live|"
                         r"launch(?:es)? today|launched today|released today|arrives? today|"
                         r"(?:has|have) (?:now )?(?:launched|released)|is now (?:out|available))\b")

# Words right before a date that mean it's NOT the release date ("after 2027")
BAD_PREFIX_RE = re.compile(r"(?i)(?:after|beyond|until|before|by|no earlier than|not before|"
                           r"since|from|as of|on or after)\s+$")

# Sentences about money/earnings are skipped for dates
FINANCE_RE = re.compile(r"(?i)\b(?:fiscal|earnings|stock|shares|quarterly results|"
                        r"financial results)\b")

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
          "sep": 9, "oct": 10, "nov": 11, "dec": 12}
MONTH = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
         r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?![A-Za-z])")

P_MDY = re.compile(r"\b" + MONTH + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d),?\s+(20\d\d)\b", re.I)
P_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + MONTH + r"\.?,?\s+(20\d\d)\b", re.I)
P_MY = re.compile(r"\b" + MONTH + r"\.?,?\s+(20\d\d)\b", re.I)
P_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
P_Q = re.compile(r"\bQ([1-4])\s*(?:of\s+)?(20\d\d)\b", re.I)
P_QW = re.compile(r"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?(20\d\d)\b", re.I)
P_SEASON = re.compile(r"\b(early|mid|late|spring|summer|fall|autumn|winter|H1|H2|first half|"
                      r"second half)(?:[-\s]+(?:of\s+)?)(20\d\d)\b", re.I)
P_YEAR = re.compile(r"\b(20\d\d)\b")

SEASON_RANGES = {   # (start month, end month)
    "early": (1, 4), "mid": (5, 8), "late": (9, 12), "spring": (3, 5), "summer": (6, 8),
    "fall": (9, 11), "autumn": (9, 11), "winter": (1, 2), "h1": (1, 6), "h2": (7, 12),
    "first half": (1, 6), "second half": (7, 12),
}
QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def log(*a):
    print(*a, file=sys.stderr)


def last_day(y, m):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return nxt - timedelta(days=1)


def domain_of(url):
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def weight_of(domain):
    for d in OFFICIAL_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return W_OFFICIAL
    for d in TRUSTED_DOMAINS:
        if domain == d or domain.endswith("." + d):
            return W_TRUSTED
    return W_OTHER


def strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", unescape(s)).strip()


def clean_text(s):
    s = strip_html(s)
    s = re.sub(r"PC\s?Gamer|PCGamer", "PCG", s)     # a site name, not a platform
    s = re.sub(r"\b(Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.", r"\1", s)
    return s


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c(])", text)
    return [p.strip() for p in parts if p.strip()]


def fetch(url, timeout=25, tries=2):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - any network problem just means "skip this one"
            last = e
    raise last


def parse_when(s):
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).date()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Feed reading
# ----------------------------------------------------------------------------


def parse_feed(xml_bytes, feed_url):
    root = ET.fromstring(xml_bytes)
    items = []
    tag = lambda e: e.tag.split("}")[-1]  # noqa: E731
    for el in root.iter():
        if tag(el) not in ("item", "entry"):
            continue
        title, text, link, when, src_domain, src_name = "", [], "", None, "", ""
        for c in el:
            t = tag(c)
            if t == "title":
                title = c.text or ""
            elif t in ("description", "summary", "encoded", "content"):
                text.append(c.text or "")
            elif t == "link":
                link = (c.get("href") or c.text or link or "").strip()
            elif t in ("pubDate", "published", "updated", "date") and when is None:
                when = parse_when(c.text)
            elif t == "source":
                src_domain = domain_of(c.get("url") or "")
                src_name = (c.text or "").strip()
        if src_name and title.endswith(" - " + src_name):     # Google News adds " - Site"
            title = title[: -len(src_name) - 3]
        domain = src_domain or domain_of(link) or domain_of(feed_url)
        items.append({"title": clean_text(title), "text": clean_text(" ".join(text)),
                      "link": link, "domain": domain, "published": when})
    return items


# ----------------------------------------------------------------------------
# Date finding
# ----------------------------------------------------------------------------


def fmt_exact(d):
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def find_dates(sentence, today):
    """Return all dates in a sentence that fall between today and today + ~2 years."""
    horizon = today + timedelta(days=HORIZON_DAYS)
    raw = []   # (start_idx, end_idx, kind, start_date, end_date, text)

    def month_num(name):
        return MONTHS[name[:3].lower()]

    def add(m, kind, start, end, text):
        raw.append((m.start(), m.end(), kind, start, end, text))

    def may_ok(m, name):   # "may" the verb vs "May" the month
        return name.lower() != "may" or name[0] == "M"

    for m in P_MDY.finditer(sentence):
        if not may_ok(m, m.group(1)):
            continue
        try:
            d = date(int(m.group(3)), month_num(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        add(m, "exact", d, d, fmt_exact(d))
    for m in P_DMY.finditer(sentence):
        if not may_ok(m, m.group(2)):
            continue
        try:
            d = date(int(m.group(3)), month_num(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        add(m, "exact", d, d, fmt_exact(d))
    for m in P_ISO.finditer(sentence):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        add(m, "exact", d, d, fmt_exact(d))
    for m in P_MY.finditer(sentence):
        if not may_ok(m, m.group(1)):
            continue
        y, mo = int(m.group(2)), month_num(m.group(1))
        add(m, "month", date(y, mo, 1), last_day(y, mo), f"{date(y, mo, 1).strftime('%B')} {y}")
    for m in P_Q.finditer(sentence):
        q, y = int(m.group(1)), int(m.group(2))
        add(m, "quarter", date(y, 3 * q - 2, 1), last_day(y, 3 * q), f"Q{q} {y}")
    for m in P_QW.finditer(sentence):
        q, y = QUARTER_WORDS[m.group(1).lower()], int(m.group(2))
        add(m, "quarter", date(y, 3 * q - 2, 1), last_day(y, 3 * q), f"Q{q} {y}")
    for m in P_SEASON.finditer(sentence):
        word, y = m.group(1).lower(), int(m.group(2))
        a, b = SEASON_RANGES[word]
        label = m.group(1).upper() if word in ("h1", "h2") else m.group(1).title()
        add(m, "season", date(y, a, 1), last_day(y, b), f"{label} {y}")
    for m in P_YEAR.finditer(sentence):
        y = int(m.group(1))
        add(m, "year", date(y, 1, 1), date(y, 12, 31), str(y))

    # Longest match wins when matches overlap ("May 26, 2027" beats plain "2027")
    raw.sort(key=lambda r: -(r[1] - r[0]))
    kept = []
    for r in raw:
        if all(r[1] <= k[0] or r[0] >= k[1] for k in kept):
            kept.append(r)

    out = []
    for s_idx, e_idx, kind, start, end, text in kept:
        if BAD_PREFIX_RE.search(sentence[max(0, s_idx - 20):s_idx]):
            continue
        if kind == "exact":
            ok = today <= start <= horizon
        else:
            ok = end >= today and start <= horizon
        if not ok:
            continue
        key = start.isoformat() if kind == "exact" else f"{kind}:{start}:{end}"
        out.append({"kind": kind, "start": start, "end": end, "text": text, "key": key,
                    "pos": s_idx})
    out.sort(key=lambda r: r["pos"])
    return out


# ----------------------------------------------------------------------------
# Reading one article: produce "findings"
# ----------------------------------------------------------------------------


def nearest(regex, sentence, pos):
    ds = [abs(m.start() - pos) for m in regex.finditer(sentence)]
    return min(ds) if ds else None


def analyze_item(item, today):
    findings = []
    title = item["title"]
    sents = [s for s in [title] + split_sentences(item["text"]) if s]
    title_gta = bool(GTA_RE.search(title))
    domain = item["domain"]
    weight = weight_of(domain)
    pub = item["published"]
    fresh = pub is not None and (today - pub).days <= RELEASED_MAX_AGE_DAYS

    def about_gta(i):
        return bool(GTA_RE.search(sents[i])) or title_gta or (i > 0 and bool(GTA_RE.search(sents[i - 1])))

    def base(kind, rumor, **extra):
        f = {"kind": kind, "domain": domain, "weight": weight, "rumor": rumor, "item": item,
             "published": pub or date.min}
        f.update(extra)
        return f

    for i, s in enumerate(sents):
        has_pc = bool(PC_RE.search(s))
        neg = bool(NEG_RE.search(s))
        hypo = bool(HYPO_RE.search(s))
        rumor = bool(RUMOR_RE.search(s))
        gta_ok = about_gta(i)

        # --- "PC version confirmed" and "out now on PC"
        if has_pc and gta_ok and not neg and not hypo:
            if RELEASED_RE.search(s) and not rumor and fresh:
                findings.append(base("released", False))
            elif CONFIRM_RE.search(s):
                findings.append(base("confirm", rumor))

        # --- dates
        if neg or hypo or not gta_ok or FINANCE_RE.search(s):
            continue
        for d in find_dates(s, today):
            attached = False
            pc_dist = nearest(PC_RE, s, d["pos"])
            con_dist = nearest(CONSOLE_RE, s, d["pos"])
            if pc_dist is not None:
                attached = con_dist is None or pc_dist <= con_dist
            elif con_dist is None:
                # PC word is in the sentence right before or after
                for j in (i - 1, i + 1):
                    if 0 <= j < len(sents) and PC_RE.search(sents[j]) \
                            and not NEG_RE.search(sents[j]) and not HYPO_RE.search(sents[j]):
                        attached = True
                        rumor = rumor or bool(RUMOR_RE.search(sents[j]))
            if not attached:
                continue
            neighbours = " ".join(sents[max(0, i - 1): i + 2])
            if not RELEASE_WORD_RE.search(s) and not RELEASE_WORD_RE.search(neighbours):
                continue
            if d["kind"] == "year" and not RELEASE_WORD_RE.search(s):
                continue
            findings.append(base("date", rumor, date=d))
    return findings


# ----------------------------------------------------------------------------
# Steam
# ----------------------------------------------------------------------------

STEAM_NAME_RE = re.compile(r"^grand theft auto\s*(?:vi|6)$", re.I)


def steam_from_json(search, details_fn):
    """Look for the real Rockstar page for GTA 6 on Steam. Returns a dict or None."""
    for it in (search or {}).get("items", []):
        name = re.sub(r"[\u2122\u00ae]", "", it.get("name", "")).strip()
        if it.get("type") != "app" or not STEAM_NAME_RE.match(name):
            continue
        det = details_fn(it["id"]) or {}
        data = (det.get(str(it["id"])) or {}).get("data") or {}
        pubs = " ".join(data.get("publishers", []) + data.get("developers", []))
        if not re.search(r"(?i)rockstar|take-two", pubs):   # ignore fake/fan pages
            continue
        rel = data.get("release_date") or {}
        return {"appid": it["id"], "coming_soon": bool(rel.get("coming_soon", True)),
                "date_text": rel.get("date", "")}
    return None


def steam_check():
    base = "https://store.steampowered.com/api/"
    try:
        search = json.loads(fetch(base + "storesearch/?term=Grand+Theft+Auto+VI&l=english&cc=US"))
    except Exception as e:  # noqa: BLE001
        log("steam search failed:", e)
        return None, False

    def details(appid):
        try:
            return json.loads(fetch(base + f"appdetails?appids={appid}&cc=us&l=english"))
        except Exception as e:  # noqa: BLE001
            log("steam details failed:", e)
            return None

    return steam_from_json(search, details), True


def steam_findings(info, today):
    item = {"title": "Steam store page for Grand Theft Auto VI",
            "link": f"https://store.steampowered.com/app/{info['appid']}",
            "domain": "store.steampowered.com", "published": today, "text": ""}
    base = {"domain": item["domain"], "weight": W_OFFICIAL, "rumor": False, "item": item,
            "published": today}
    out = []
    if not info["coming_soon"]:
        out.append(dict(base, kind="released"))
        return out
    out.append(dict(base, kind="confirm"))
    cleaned = clean_text(info.get("date_text", ""))
    for d in find_dates(cleaned, today):
        out.append(dict(base, kind="date", date=d))
        break
    return out


# ----------------------------------------------------------------------------
# Deciding
# ----------------------------------------------------------------------------


def aggregate(findings):
    """Score a group of findings: each website counts once."""
    by = {}
    for f in findings:
        d = by.setdefault(f["domain"], {"w": f["weight"], "clean": False})
        if not f["rumor"]:
            d["clean"] = True
    clean = sum(v["w"] for v in by.values() if v["clean"])
    total = sum(v["w"] for v in by.values())
    return clean, total


def evidence(findings, limit=5):
    seen, out = set(), []
    for f in sorted(findings, key=lambda f: (-f["weight"], f["rumor"])):
        it = f["item"]
        key = it["link"] or it["title"]
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": it["title"], "url": it["link"], "source": it["domain"],
                    "published": it["published"].isoformat() if it["published"] else None,
                    "rumor": f["rumor"]})
        if len(out) >= limit:
            break
    return out


def decide(findings):
    released = [f for f in findings if f["kind"] == "released"]
    confirms = [f for f in findings if f["kind"] == "confirm"]
    dates = [f for f in findings if f["kind"] == "date"]

    res = {"status": "none", "headline": "No PC announcement yet",
           "detail": "Nothing credible found about a PC version of GTA 6.",
           "date": None, "date_text": None, "date_kind": None, "confidence": None,
           "sources": []}

    r_clean, _ = aggregate(released)
    if released and r_clean >= OFFICIAL_SCORE:
        res.update(status="released", headline="Out now on PC", confidence="official",
                   detail="Several reliable sources say GTA 6 is available on PC.",
                   sources=evidence(released))
        return res

    c_clean, c_total = aggregate(confirms)
    # "Confirmed" needs Rockstar / Take-Two / Steam themselves. News sites alone = rumor.
    pc_confirmed = any(f["weight"] >= W_OFFICIAL and not f["rumor"] for f in confirms)
    pc_rumored = c_total >= MIN_SCORE

    groups = {}
    for f in dates:
        groups.setdefault(f["date"]["key"], []).append(f)
    ranked = []
    for key, fs in groups.items():
        clean, total = aggregate(fs)
        if total < MIN_SCORE:
            continue
        official = clean >= OFFICIAL_SCORE
        latest = max(f["published"] for f in fs)
        precision = 1 if fs[0]["date"]["kind"] == "exact" else 0
        ranked.append(((official, latest, precision, total), key, fs, official))
    ranked.sort(key=lambda r: r[0], reverse=True)

    if ranked:
        _, _, fs, official = ranked[0]
        d = fs[0]["date"]
        res.update(date=d["start"].isoformat() if d["kind"] == "exact" else None,
                   date_text=d["text"], date_kind=d["kind"],
                   sources=evidence(fs + (confirms if pc_confirmed else [])))
        if official:
            res.update(status="date_official", confidence="official",
                       headline=f"PC release: {d['text']}",
                       detail="Official: confirmed by Rockstar/Take-Two or several trusted sites.")
        elif pc_confirmed:
            res.update(status="pc_confirmed", confidence="rumored",
                       headline="PC confirmed, date not official",
                       detail=f"PC is confirmed, but the date ({d['text']}) is only rumored.")
        else:
            res.update(status="date_rumored", confidence="rumored",
                       headline=f"PC release: {d['text']} (rumored)",
                       detail="Rumored only. Not confirmed by Rockstar or several trusted sites.")
        return res

    if pc_confirmed:
        res.update(status="pc_confirmed", confidence="official",
                   headline="PC confirmed, no date yet",
                   detail="Reliable sources say a PC version is coming, but no date was found.",
                   sources=evidence(confirms))
    elif pc_rumored:
        res.update(status="pc_rumored", confidence="rumored",
                   headline="PC version rumored, nothing official",
                   detail="Some sites talk about a PC version, but nothing is confirmed.",
                   sources=evidence(confirms))
    return res


# ----------------------------------------------------------------------------
# Notifications (optional)
# ----------------------------------------------------------------------------


def notify(headline, detail):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    try:
        req = urllib.request.Request(
            "https://ntfy.sh/" + urllib.parse.quote(topic),
            data=f"{headline}\n{detail}".encode("utf-8"),
            headers={"Title": "GTA 6 PC checker", "Priority": "high"}, method="POST")
        urllib.request.urlopen(req, timeout=20).read()
        log("notification sent")
    except Exception as e:  # noqa: BLE001
        log("notification failed:", e)


def notify_category(res):
    """Which kind of news is this? Devices choose which kinds they want."""
    if res["status"] == "released":
        return "released"
    if res["confidence"] == "official" and res["status"] in ("date_official", "pc_confirmed"):
        return "official"
    return "rumor"      # rumors, and "back to nothing"


def write_notify_file(res, out_path):
    """Leaves notify.json next to result.json. The workflow's push step sends it."""
    path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "notify.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"title": "GTA 6 PC release update",
                   "body": f"{res['headline']}. {res['detail']}",
                   "category": notify_category(res)}, fh, ensure_ascii=False)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--sample", help="JSON file with fake articles (skips the internet)")
    ap.add_argument("--today", help="pretend today is YYYY-MM-DD")
    ap.add_argument("--out", default=RESULT_FILE)
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else datetime.now(timezone.utc).date()
    stale = os.path.join(os.path.dirname(os.path.abspath(args.out)), "notify.json")
    if os.path.exists(stale):
        os.remove(stale)        # never re-send an old notification
    items, feeds_ok, steam_note = [], 0, "not checked"
    steam_info = None

    if args.sample:
        with open(args.sample, encoding="utf-8") as fh:
            for it in json.load(fh):
                items.append({"title": clean_text(it.get("title", "")),
                              "text": clean_text(it.get("text", "")),
                              "link": it.get("link", ""),
                              "domain": it.get("domain", ""),
                              "published": parse_when(it.get("published")) or today})
        feeds_ok = feeds_total = 1
    else:
        feeds_total = len(FEEDS)
        for url in FEEDS:
            try:
                got = parse_feed(fetch(url), url)
                items.extend(got)
                feeds_ok += 1
            except Exception as e:  # noqa: BLE001
                log(f"feed failed: {url[:70]} ({e})")
        steam_info, steam_ok = steam_check()
        steam_note = ("page found" if steam_info else "no page") if steam_ok else "check failed"

    if feeds_ok == 0:
        log("No feed could be reached. Keeping the old result.")
        return 1

    # Keep only recent items that are about GTA 6, no duplicates
    seen, relevant = set(), []
    for it in items:
        if it["published"] and (today - it["published"]).days > MAX_AGE_DAYS:
            continue
        if not GTA_RE.search(it["title"] + " " + it["text"]):
            continue
        key = re.sub(r"\W+", "", it["title"].lower())
        if key in seen:
            continue
        seen.add(key)
        relevant.append(it)

    findings = []
    for it in relevant:
        findings.extend(analyze_item(it, today))
    if steam_info:
        findings.extend(steam_findings(steam_info, today))

    res = decide(findings)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    res.update(checked_at=now, changed_at=now, steam=steam_note,
               articles_scanned=len(items), articles_about_gta6=len(relevant),
               feeds_ok=feeds_ok, feeds_total=feeds_total)

    # Keep "changed_at" if nothing changed; notify if something did
    old = None
    if os.path.exists(args.out):
        try:
            with open(args.out, encoding="utf-8") as fh:
                old = json.load(fh)
        except Exception:
            old = None
    changed = (old is None) or (old.get("status"), old.get("date_text")) != (res["status"], res["date_text"])
    if not changed:
        res["changed_at"] = old.get("changed_at", now)
    elif old is not None and old.get("status") != "unchecked" and not args.sample:
        notify(res["headline"], res["detail"])
        write_notify_file(res, args.out)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"{res['status']}: {res['headline']}  ({len(relevant)} GTA 6 articles, steam: {steam_note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
