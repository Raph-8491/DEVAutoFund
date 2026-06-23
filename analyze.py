"""
DEVAutoFund Statistik-Analyse: zaehlt Markt-Position UND Haendler-Filter-Treffer
ueber alle Eintraege in seen.json. Manuell ueber GitHub Actions (workflow_dispatch).
"""
import os
import json
import re
import requests
from pathlib import Path
from collections import Counter

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]

SEEN_FILE = Path("seen.json")

MARKET_TOLERANCE_YEARS = 2
MARKET_TOLERANCE_KM = 30000
MARKET_MIN_COUNT = 3
DAILY_VOLUME = 900

DEALER_BRANDS = {"VW", "Audi", "BMW", "Mercedes-Benz"}
LIMO_POS = ["limousine", "limo", "sedan", "stufenheck"]
LIMO_NEG = ["kombi", "avant", "touring", "variant", "sportback", "gran coupe",
            "gran turismo", "shooting brake", "suv", "coupe", "cabrio", "roadster",
            "van", "kompakt", "schraegheck", "fliessheck", "t-modell", "tourer",
            "allroad", "cross country", "kasten", "bus", "hatchback", "fastback",
            "liftback", "active tourer", "gran tourer", "scenic"]


def _normalize_model(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""


def is_limousine(body, title):
    t = ((body or "") + " " + (title or "")).lower()
    if any(k in t for k in LIMO_NEG):
        return False
    if any(k in t for k in LIMO_POS):
        return True
    return False


def car_passes_dealer_rules(make, body, title, year, km):
    if make not in DEALER_BRANDS:
        return False
    if year is None:
        return False
    limo = is_limousine(body, title)
    if make == "Mercedes-Benz":
        if not limo:
            return False
        if year >= 2014:
            return True
        return year >= 2010 and km is not None and km <= 240000
    if make == "BMW":
        if limo:
            return year >= 2006
        if year >= 2014:
            return True
        return year >= 2010 and km is not None and km <= 250000
    if year >= 2014:
        return True
    return year >= 2008 and km is not None and km <= 240000


def body_bucket(body, title):
    t = ((body or "") + " " + (title or "")).lower()
    if "suv" in t or "gelaend" in t or "geländ" in t:
        return "SUV"
    if "kombi" in t or "avant" in t or "touring" in t or "variant" in t or "t-modell" in t:
        return "Kombi"
    if "cabrio" in t or "roadster" in t:
        return "Cabrio"
    if "coupe" in t or "coupé" in t:
        return "Coupe"
    if is_limousine(body, title):
        return "Limousine"
    return "unbekannt"


def calculate_market_median(seen, make, model, year, km):
    if not make or not model or year is None or km is None:
        return None
    make_lower = make.lower()
    model_norm = _normalize_model(model)
    if not model_norm:
        return None
    prices = []
    seen_base = set()
    for ad_id, info in seen.items():
        if ad_id == "__health__":
            continue
        base = ad_id.split(":", 1)[1] if ":" in ad_id else ad_id
        if base in seen_base:
            continue
        seen_base.add(base)
        im = info.get("ma") or ""
        imo = info.get("mo") or ""
        iy = info.get("y")
        ikm = info.get("km")
        ip = info.get("p")
        if not im or not imo or iy is None or ikm is None or not ip:
            continue
        if im.lower() != make_lower:
            continue
        if _normalize_model(imo) != model_norm:
            continue
        if abs(iy - year) > MARKET_TOLERANCE_YEARS:
            continue
        if abs(ikm - km) > MARKET_TOLERANCE_KM:
            continue
        prices.append(ip)
    if len(prices) < MARKET_MIN_COUNT:
        return None
    prices.sort()
    n = len(prices)
    return prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) // 2


def categorize(price, median):
    if not price or not median:
        return None
    pct = round(100 * (price - median) / median) if median > 0 else 0
    if abs(pct) < 3:
        return "fair"
    if pct < 0:
        return "super" if pct <= -15 else "gut"
    return "ueberteuert" if pct >= 15 else "ueber"


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        print("⚠️ TELEGRAM_TOKEN oder CHAT_ID fehlt - kein Push")
        return
    for chat_id in TELEGRAM_CHAT_IDS:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code != 200:
                payload.pop("parse_mode", None)
                r = requests.post(url, data=payload, timeout=20)
            if r.status_code != 200:
                print(f"Telegram-Fehler {chat_id}: {r.status_code}")
        except Exception as e:
            print(f"Telegram-Exception {chat_id}: {e}")


def main():
    if not SEEN_FILE.exists():
        print("seen.json nicht gefunden!")
        return
    seen = json.loads(SEEN_FILE.read_text())

    total = sum(1 for k in seen if k != "__health__")
    complete = []
    for ad_id, info in seen.items():
        if ad_id == "__health__":
            continue
        if info.get("ma") and info.get("mo") and info.get("y") and info.get("km") and info.get("p"):
            complete.append((ad_id, info))
    incomplete = total - len(complete)

    print(f"Gesamt: {total} | vollstaendig: {len(complete)} | unvollstaendig: {incomplete}")
    print("Analysiere...")

    # Markt-Position
    cats = Counter()
    no_cmp = 0
    for i, (ad_id, info) in enumerate(complete):
        if i % 1000 == 0 and i > 0:
            print(f"  ... {i}/{len(complete)}")
        median = calculate_market_median(seen, info["ma"], info["mo"], info["y"], info["km"])
        c = categorize(info["p"], median)
        if c is None:
            no_cmp += 1
        else:
            cats[c] += 1
    total_cls = sum(cats.values()) + no_cmp or 1

    def pct(n):
        return f"{100 * n / total_cls:.1f}%"

    def per_day(n):
        return round(DAILY_VOLUME * n / total_cls)

    # Haendler-Filter ueber gesamte DB (Marke vorhanden reicht, BJ/KM/Limo werden geprueft)
    brand_total = Counter()
    brand_hit = Counter()
    body_dist = Counter()
    dealer_hits = 0
    for ad_id, info in seen.items():
        if ad_id == "__health__":
            continue
        make = info.get("ma")
        if make not in DEALER_BRANDS:
            continue
        brand_total[make] += 1
        body = info.get("b") or ""
        title = info.get("t") or ""
        body_dist[body_bucket(body, title)] += 1
        if car_passes_dealer_rules(make, body, title, info.get("y"), info.get("km")):
            brand_hit[make] += 1
            dealer_hits += 1
    zielmarken_total = sum(brand_total.values())

    msg = (
        f"📊 *DEVAutoFund Analyse*\n\n"
        f"Eintraege gesamt: *{total}*\n"
        f"Mit vollst. Daten: *{len(complete)}*\n\n"
        f"*Markt-Position (vollst. Eintraege):*\n"
        f"🟢🟢🟢 SUPER PREIS: {cats['super']} ({pct(cats['super'])})\n"
        f"🟢 Guter Preis: {cats['gut']} ({pct(cats['gut'])})\n"
        f"⚪ Fair: {cats['fair']} ({pct(cats['fair'])})\n"
        f"🔴 Ueber Markt: {cats['ueber']} ({pct(cats['ueber'])})\n"
        f"🔴🔴🔴 Ueberteuert: {cats['ueberteuert']} ({pct(cats['ueberteuert'])})\n"
        f"❓ Zu wenige Vergleiche: {no_cmp} ({pct(no_cmp)})\n\n"
        f"*HAENDLER-FILTER (VW/Audi/BMW/MB):*\n"
        f"Zielmarken in DB: *{zielmarken_total}*\n"
        f"Erfuellen Haendler-Regeln: *{dealer_hits}*\n\n"
        f"_Pro Marke (Treffer / gesamt):_\n"
    )
    for mk in ["VW", "Audi", "BMW", "Mercedes-Benz"]:
        msg += f"• {mk}: {brand_hit.get(mk,0)} / {brand_total.get(mk,0)}\n"
    msg += "\n_Karosserie (Zielmarken):_\n"
    for b, n in body_dist.most_common():
        msg += f"• {b}: {n}\n"
    msg += ("\n⚠️ Limousinen-Erkennung bei Alt-Eintraegen noch ueber Titel "
            "(Karosserie-Feld erst ab v1.1). Mercedes daher evtl. untertrieben.")

    print(msg)
    send_telegram(msg)
    print("\n✅ Analyse abgeschlossen")


if __name__ == "__main__":
    main()
