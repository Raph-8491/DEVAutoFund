"""
DEVAutoFund Analyse v2: zaehlt die ECHT GEPUSHTEN Autos (Marker 'pu'), nicht die
ganze seen.json (die nur als Marktwert-Vergleichs-DB dient).
Plus Doppel-Check: meldet durchgerutschte Dubletten.
Manuell ueber GitHub Actions (workflow_dispatch).
"""
import os
import json
import re
import unicodedata
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import Counter

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_IDS = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
SEEN_FILE = Path("seen.json")

MARKET_TOLERANCE_YEARS = 2
MARKET_TOLERANCE_KM = 30000
MARKET_MIN_COUNT = 3

DEALER_BRANDS = {"VW", "Audi", "BMW", "Mercedes-Benz"}
LIMO_POS = ["limousine", "limo", "sedan", "stufenheck"]
LIMO_NEG = ["kombi", "avant", "touring", "variant", "sportback", "gran coupe",
            "gran turismo", "shooting brake", "suv", "coupe", "sportwagen",
            "cabrio", "roadster", "van", "kompakt", "schraegheck", "fliessheck",
            "t-modell", "tourer", "allroad", "cross country", "kasten", "bus",
            "hatchback", "fastback", "liftback", "active tourer", "gran tourer", "scenic"]


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _normalize_model(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower()) if s else ""


def is_limousine(body, title):
    t = _strip_accents(((body or "") + " " + (title or "")).lower())
    if any(k in t for k in LIMO_NEG):
        return False
    if any(k in t for k in LIMO_POS):
        return True
    return False


def body_bucket(body, title):
    t = _strip_accents(((body or "") + " " + (title or "")).lower())
    if "suv" in t or "gelandewagen" in t:
        return "SUV"
    if "kombi" in t or "avant" in t or "touring" in t or "variant" in t or "t-modell" in t:
        return "Kombi"
    if "cabrio" in t or "roadster" in t:
        return "Cabrio"
    if "coupe" in t or "sportwagen" in t:
        return "Coupe"
    if is_limousine(body, title):
        return "Limousine"
    return "unbekannt"


def car_fingerprint(info):
    ma = (info.get("ma") or "").strip().lower()
    mo = re.sub(r"[^a-z0-9]", "", (info.get("mo") or "").lower())
    y = info.get("y")
    km = info.get("km")
    p = info.get("p")
    if not ma or not mo or y is None or km is None or not p:
        return None
    return f"{ma}|{mo}|{y}|{km}|{p}"


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
        if im.lower() != make_lower or _normalize_model(imo) != model_norm:
            continue
        if abs(iy - year) > MARKET_TOLERANCE_YEARS or abs(ikm - km) > MARKET_TOLERANCE_KM:
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


def parse_ts(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


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

    db_complete = sum(1 for k, v in seen.items()
                      if k != "__health__" and v.get("ma") and v.get("mo")
                      and v.get("y") and v.get("km") and v.get("p"))

    # Nur ECHT gepushte Autos (Marker pu)
    pushed = [(k, v) for k, v in seen.items() if k != "__health__" and v.get("pu")]

    now = datetime.now(timezone.utc)
    n_total = len(pushed)
    n_24h = n_7d = 0
    for _, v in pushed:
        ts = parse_ts(v.get("pu"))
        if not ts:
            continue
        age = now - ts
        if age <= timedelta(hours=24):
            n_24h += 1
        if age <= timedelta(days=7):
            n_7d += 1

    # Markt-Position NUR der gepushten Autos
    cats = Counter()
    no_cmp = 0
    by_make = Counter()
    bodies = Counter()
    for ad_id, info in pushed:
        by_make[info.get("ma")] += 1
        bodies[body_bucket(info.get("b"), info.get("t"))] += 1
        median = calculate_market_median(seen, info.get("ma"), info.get("mo"), info.get("y"), info.get("km"))
        c = categorize(info.get("p"), median)
        if c is None:
            no_cmp += 1
        else:
            cats[c] += 1

    # Doppel-Check: gleiche Fingerprints unter verschiedenen IDs (durchgerutschte Dubletten)
    fp_map = {}
    for ad_id, info in pushed:
        fp = car_fingerprint(info)
        if fp:
            fp_map.setdefault(fp, []).append(ad_id)
    dupes = {fp: ids for fp, ids in fp_map.items() if len(ids) > 1}
    dupe_extra = sum(len(ids) - 1 for ids in dupes.values())

    def pct(n):
        return f"{100 * n / n_total:.1f}%" if n_total else "0%"

    if n_total == 0:
        msg = ("📊 *DEVAutoFund Analyse v2*\n\n"
               "Noch *keine* gepushten Autos mit Marker vorhanden.\n"
               "Der Push-Marker (pu) kommt mit v1.4 - ab jetzt wird jeder echte "
               "Push gezaehlt. In ein paar Stunden/Tagen hier nochmal triggern.\n\n"
               f"_Markt-DB (Vergleichsbasis): {db_complete} vollst. Eintraege._")
        print(msg)
        send_telegram(msg)
        return

    msg = (
        f"📊 *DEVAutoFund Analyse v2 (echte Pushes)*\n\n"
        f"*Vom Bot gepusht:*\n"
        f"Gesamt: *{n_total}*\n"
        f"Letzte 24h: *{n_24h}*\n"
        f"Letzte 7 Tage: *{n_7d}*\n\n"
        f"*Markt-Position dieser Autos:*\n"
        f"🟢🟢🟢 SUPER PREIS: {cats['super']} ({pct(cats['super'])})\n"
        f"🟢 Guter Preis: {cats['gut']} ({pct(cats['gut'])})\n"
        f"⚪ Fair: {cats['fair']} ({pct(cats['fair'])})\n"
        f"🔴 Ueber Markt: {cats['ueber']} ({pct(cats['ueber'])})\n"
        f"🔴🔴🔴 Ueberteuert: {cats['ueberteuert']} ({pct(cats['ueberteuert'])})\n"
        f"❓ Zu wenige Vergleiche: {no_cmp} ({pct(no_cmp)})\n\n"
        f"*Pro Marke (gepusht):*\n"
    )
    for mk in ["VW", "Audi", "BMW", "Mercedes-Benz"]:
        if by_make.get(mk):
            msg += f"• {mk}: {by_make.get(mk)}\n"
    msg += "\n*Karosserie (gepusht):*\n"
    for b, n in bodies.most_common():
        msg += f"• {b}: {n}\n"
    msg += (f"\n*Doppel-Check:* "
            + (f"⚠️ {dupe_extra} durchgerutschte Dublette(n)!" if dupe_extra
               else "✅ keine Dubletten (jedes Auto nur 1x)"))
    msg += f"\n\n_Markt-DB (Vergleichsbasis): {db_complete} vollst. Eintraege._"

    print(msg)
    if dupes:
        print("\nDubletten-Details:")
        for fp, ids in list(dupes.items())[:10]:
            print(f"  {fp}  ->  {ids}")
    send_telegram(msg)
    print("\n✅ Analyse abgeschlossen")


if __name__ == "__main__":
    main()
