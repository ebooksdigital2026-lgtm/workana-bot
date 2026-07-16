#!/usr/bin/env python3
"""
Workana Freelance Job Scraper + Telegram Notifier
Standalone script - runs as Railway cron job every hour.
"""

import json
import os
import re
import sys
import time
from urllib.parse import urlencode
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", "8807055193:AAGaLQ1htm4-O5S0gpAxCSY4eTafvt8qVro"
)
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8269637460")
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

WORKANA_BASE = "https://www.workana.com"
WORKANA_JOBS = f"{WORKANA_BASE}/jobs"

CATEGORY = os.environ.get("WORKANA_CATEGORY") or "it-programming"
LANGUAGE = os.environ.get("WORKANA_LANGUAGE") or "es"
PUBLICATION = os.environ.get("WORKANA_PUBLICATION") or "1d"
MAX_PAGES = int(os.environ.get("WORKANA_MAX_PAGES") or "10")
MAX_RESULTS = int(os.environ.get("WORKANA_MAX_RESULTS") or "15")
MIN_BIDS = int(os.environ.get("WORKANA_MIN_BIDS") or "1")
MAX_BIDS = int(os.environ.get("WORKANA_MAX_BIDS") or "20")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}
REQUEST_DELAY = 2.0


def build_url(params):
    return f"{WORKANA_JOBS}?{urlencode(params, doseq=True)}"


def parse_results(html):
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("search", attrs={":results-initials": True})
    if not tag:
        return [], {}
    try:
        data = json.loads(tag[":results-initials"])
    except json.JSONDecodeError:
        return [], {}
    return data.get("results", []), data.get("pagination", {})


def extract_text(html_str):
    if not html_str:
        return ""
    s = re.sub(r"<[^>]+>", "", html_str)
    s = s.replace("&amp;", "&").replace("&quot;", '"').replace("&#039;", "'").replace("&nbsp;", " ")
    return s.strip()


def parse_bids(s):
    m = re.search(r"(\d+)", s or "")
    return int(m.group(1)) if m else 0


def scrape():
    seen = set()
    jobs = []
    total = 0
    pages = 0
    params = {"language": LANGUAGE, "category": CATEGORY}
    if PUBLICATION and PUBLICATION != "any":
        params["publication"] = PUBLICATION

    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            params["page"] = page
        try:
            r = requests.get(build_url(params), headers=HEADERS, timeout=30)
            r.raise_for_status()
        except Exception:
            break
        raw, pag = parse_results(r.text)
        if not raw:
            break
        pages = page
        if page == 1:
            total = pag.get("total", 0)
        for job in raw:
            slug = job.get("slug", "")
            if slug in seen:
                continue
            seen.add(slug)
            bids = parse_bids(job.get("totalBids", ""))
            if bids < MIN_BIDS or bids > MAX_BIDS:
                continue
            skills = []
            for s in job.get("skills", []):
                skills.append(s.get("anchorText", "") if isinstance(s, dict) else s)
            rating = job.get("rating", {})
            rv = rating.get("value", "0.00") if isinstance(rating, dict) else "0.00"
            jobs.append({
                "title": extract_text(job.get("title", "")),
                "url": f"{WORKANA_BASE}/job/{slug}",
                "budget": extract_text(job.get("budget", "")),
                "bids": bids,
                "posted": extract_text(job.get("postedDate", "")),
                "skills": skills,
                "country": extract_text(job.get("country", "")),
                "verified": job.get("hasVerifiedPaymentMethod", False),
                "urgent": job.get("isUrgent", False),
            })
        if pag and page >= pag.get("pages", 1):
            break
        if page < MAX_PAGES:
            time.sleep(REQUEST_DELAY)
    return jobs, total, pages


def format_report(jobs, total, pages):
    pub_map = {"1d": "24h", "3d": "3d", "1w": "1 semana"}
    pub = pub_map.get(PUBLICATION, "")

    lines = []
    lines.append("\U0001f50d *Workana - Ofertas IT/Programaci\u00f3n*\n")
    lines.append(f"Total: {total:,} | Revisadas: {pages} p\u00e1ginas\n")
    hdr = f"Filtro: {MIN_BIDS}-{MAX_BIDS} propuestas"
    if pub:
        hdr += f" | {pub}"
    lines.append(hdr + "\n")
    lines.append(f"\u2705 *{len(jobs)} coincidencias*\n")
    lines.append("_\n")

    if not jobs:
        return "\n".join(lines + ["\u26a0\ufe0f Sin ofertas con estos filtros."])

    jobs = sorted(jobs, key=lambda j: (not j["urgent"], j["bids"]))
    shown = jobs[:MAX_RESULTS]

    for i, j in enumerate(shown, 1):
        u = " \U0001f6a8" if j["urgent"] else ""
        v = " \u2705" if j["verified"] else ""
        t = j["title"][:60]
        lines.append(f"_{i}._ `{j['bids']} prop.` {t}{u}{v}\n")
        lines.append(f"\U0001f4b0 *{j['budget']}* | \U0001f310 {j['country']} | {j['posted']}\n")
        lines.append(f"\U0001f517 {j['url']}\n")

    if len(jobs) > MAX_RESULTS:
        lines.append(f"\n_(mostrando top {MAX_RESULTS} de {len(jobs)})_\n")

    lines.append("_\u23f0 WorkanaBot_\n")
    return "\n".join(lines)


def send_telegram(text):
    try:
        r = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
        return r.json()
    except Exception as e:
        print(f"ERROR sending to Telegram: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Scraping Workana...")
    jobs, total, pages = scrape()
    print(f"Found {len(jobs)} jobs (total: {total}, pages: {pages})")

    text = format_report(jobs, total, pages)
    result = send_telegram(text)

    if result.get("ok"):
        print(f"Sent to Telegram (msg_id: {result['result']['message_id']})")
    else:
        print(f"Telegram error: {result}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())