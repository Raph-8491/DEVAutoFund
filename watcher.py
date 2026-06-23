"""
Fahrzeug-Watcher v1.1 (Haendler-Profil VW/Audi/BMW/Mercedes): 3 Plattformen + Marken-/BJ-/KM-/Limousinen-Filter + Karosserie-Feld (b) + Cross-Push-Dedup + Block-Warnung + Template + Preis-Drop + Link-Check + Marktwert-DB + Header-Rotation.
"""
import os
import json
import re
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_IDS = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()]
CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "").strip()

WILLHABEN_URL = (
    "https://www.willhaben.at/iad/gebrauchtwagen/auto/gebrauchtwagenboerse"
    "?MOTOR_CONDITION=20"
    "&ENGINE/FUEL=100001&ENGINE/FUEL=100003"
    "&TRANSMISSION=180004&TRANSMISSION=180001"
    "&areaId=1&areaId=2&areaId=3&areaId=4&areaId=5"
    "&areaId=6&areaId=7&areaId=8&areaId=900"
    "&areaId=-137"
    "&DEALER=1"
    "&YEAR_MODEL_FROM=2008"
    "&PRICE_TO=15000"
    "&rows=50&sort=1"
)

AUTOSCOUT_URL = (
    "https://www.autoscout24.at/lst"
    "?atype=C"
    "&custtype=P&cy=A%2CD"
    "&damaged_listing=exclude&desc=1&fregfrom=2008"
    "&priceto=15000"
    "&fuel=B%2CD&gear=A%2CM"
    "&offer=U&powertype=kw&sort=age&ustate=N%2CU"
)

GEBRAUCHTWAGEN_URL = (
    "https://www.gebrauchtwagen.at/angebote"
    "?atype=C"
    "&custtype=P&cy=A"
    "&damaged_listing=exclude&desc=1&fregfrom=2008"
    "&priceto=15000"
    "&fuel=B%2CD&gear=A%2CM"
    "&offer=U&powertype=kw&sort=age&ustate=N%2CU"
)

AUTOSCOUT_PAGES = 2
GEBRAUCHTWAGEN_PAGES = 2
MAX_RETRIES = 3

LINK_CHECK_MIN_MIN = 3
LINK_CHECK_MAX_MIN = 15
LINK_CHECK_LIMIT = 10
LINK_CHECK_DOUBLE_DELAY = 5

BAYERN_RANGES = [(80000, 87999), (90000, 97999)]

MARKET_TOLERANCE_YEARS = 2
MARKET_TOLERANCE_KM = 30000
MARKET_MIN_COUNT = 3

KNOWN_MAKES_LOWER = {
    "audi": "Audi",
    "bmw": "BMW",
    "mercedes": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
    "opel": "Opel",
    "seat": "Seat",
    "skoda": "Skoda",
    "škoda": "Skoda",
    "toyota": "Toyota",
    "vw": "VW",
    "volkswagen": "VW",
}

MESSAGE_TEMPLATES = [
    "Hallo, ist das auto noch verfügbar? wäre eine besichtigung möglich? können auch gerne telefonieren unter {phone}",
    "Servus, ist das auto noch zu haben? wann könnt ich ihn mir anschauen? können auch gerne telefonieren unter {phone}",
    "Hallo, ist das auto noch verfügbar? wann wäre eine besichtigung möglich? gerne auch ein anruf unter {phone}",
]

STRONG_DEAD_KEYWORDS = [
    "diese anzeige ist derzeit nicht verfügbar",
    "diese anzeige wurde gelöscht",
    "anzeige wurde entfernt",
    "anzeige nicht mehr verfügbar",
    "anzeige nicht gefunden",
    "anzeige ist leider nicht mehr",
    "das angebot ist nicht mehr verfügbar",
    "das gesuchte angebot ist nicht mehr verfügbar",
    "this listing is no longer available",
    "the offer you are looking for is no longer available",
    "ne-3 404",
]

SEEN_FILE = Path("seen.json")
SEEN_CACHE = {}

BROWSER_PROFILES = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="130", "Chromium";v="130", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
        "sec-ch-ua": None,
        "sec-ch-ua-mobile": None,
        "sec-ch-ua-platform": None,
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
        "sec-ch-ua": None,
        "sec-ch-ua-mobile": None,
        "sec-ch-ua-platform": None,
    },
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
    },
    {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
        "sec-ch-ua": None,
        "sec-ch-ua-mobile": None,
        "sec-ch-ua-platform": None,
    },
]


def build_headers():
    """Wählt zufälligen Browser-Profil und baut komplette Headers."""
    profile = random.choice(BROWSER_PROFILES)
    headers = {
        "User-Agent": profile["User-Agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(["de-AT,de;q=0.9,en;q=0.8", "de-DE,de;q=0.9,en;q=0.8", "de,de-AT;q=0.9,en-US;q=0.7,en;q=0.6"]),
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    if profile["sec-ch-ua"]:
        headers["sec-ch-ua"] = profile["sec-ch-ua"]
        headers["sec-ch-ua-mobile"] = profile["sec-ch-ua-mobile"]
        headers["sec-ch-ua-platform"] = profile["sec-ch-ua-platform"]
    return headers


HEADERS = build_headers()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _parse_int(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = re.sub(r"[^\d]", "", str(value))
    return int(s) if s else None


def _first_truthy(*values):
    for v in values:
        if v not in (None, "", 0):
            return v
    return None


def _fmt_int(v):
    try:
        return f"{int(v):,}".replace(",", ".")
    except Exception:
        return str(v)


def _extract_year_int(year_raw):
    if year_raw is None:
        return None
    s = str(year_raw).strip()
    m = re.search(r"(19\d{2}|20\d{2})", s)
    if m:
        return int(m.group(1))
    return None


def _normalize_model(s):
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def escape_markdown(text):
    """Entfernt Telegram Markdown Sonderzeichen aus User-Input damit Formatierung nicht kaputt geht."""
    if not text:
        return text
    text = str(text)
    return (text
            .replace("\\", "")
            .replace("*", "")
            .replace("_", "")
            .replace("`", "")
            .replace("[", "(")
            .replace("]", ")"))


def parse_make_model_from_title(title):
    if not title:
        return None, None
    parts = title.strip().split()
    if not parts:
        return None, None
    first_lower = parts[0].lower()
    if first_lower in KNOWN_MAKES_LOWER:
        make = KNOWN_MAKES_LOWER[first_lower]
        model = parts[1] if len(parts) > 1 else None
        return make, model
    return None, None


def is_zombie_entry(info):
    if info.get("ch") == "skip":
        return False
    return (
        info.get("f") is None and
        info.get("u") is None and
        info.get("t") is None and
        info.get("ts") is None
    )


def load_seen():
    if not SEEN_FILE.exists():
        return {}
    try:
        data = json.loads(SEEN_FILE.read_text())
    except Exception:
        return {}
    if isinstance(data, list):
        print(f"  Konvertiere alte seen.json (Liste mit {len(data)} Einträgen) zu neuem Format...")
        return {ad_id: {"p": None, "f": None, "u": None, "t": None, "ts": None, "ch": None,
                        "ma": None, "mo": None, "y": None, "km": None} for ad_id in data}
    if not isinstance(data, dict):
        return {}
    migrated = 0
    for ad_id, info in data.items():
        if not info.get("ma") and info.get("t"):
            make, model = parse_make_model_from_title(info["t"])
            if make:
                info["ma"] = make
                migrated += 1
            if model:
                info["mo"] = model
    if migrated:
        print(f"  Marktwert-Migration: {migrated} alte Einträge mit Marke ergänzt")
    return data


def save_seen(seen):
    health = seen.pop("__health__", None)
    if len(seen) > 12000:
        items = sorted(seen.items(), key=lambda x: str(x[1].get("f") or ""), reverse=True)
        seen = dict(items[:12000])
    if health is not None:
        seen["__health__"] = health
    SEEN_FILE.write_text(json.dumps(seen, indent=2))
    return seen
    

def calculate_market_value(make, model, year, km):
    if not make or not model or year is None or km is None:
        return None
    make_lower = make.lower()
    model_norm = _normalize_model(model)
    if not model_norm:
        return None
    similar_prices = []
    seen_base_ids = set()
    for ad_id, info in SEEN_CACHE.items():
        if ad_id == "__health__":
            continue
        base_id = ad_id.split(":", 1)[1] if ":" in ad_id else ad_id
        if base_id in seen_base_ids:
            continue
        seen_base_ids.add(base_id)
        info_make = info.get("ma") or ""
        info_model = info.get("mo") or ""
        info_year = info.get("y")
        info_km = info.get("km")
        info_price = info.get("p")
        if not info_make or not info_model:
            continue
        if info_year is None or info_km is None or not info_price:
            continue
        if info_make.lower() != make_lower:
            continue
        if _normalize_model(info_model) != model_norm:
            continue
        if abs(info_year - year) > MARKET_TOLERANCE_YEARS:
            continue
        if abs(info_km - km) > MARKET_TOLERANCE_KM:
            continue
        similar_prices.append(info_price)
    if len(similar_prices) < MARKET_MIN_COUNT:
        return None
    similar_prices.sort()
    n = len(similar_prices)
    if n % 2 == 1:
        median = similar_prices[n // 2]
    else:
        median = (similar_prices[n // 2 - 1] + similar_prices[n // 2]) // 2
    return {"median": median, "count": n, "min": similar_prices[0], "max": similar_prices[-1]}
    

def format_market_value(market_data, current_price):
    if not market_data or not current_price:
        return []
    median = market_data["median"]
    count = market_data["count"]
    diff = current_price - median
    pct = round(100 * diff / median) if median > 0 else 0
    abs_diff = abs(diff)
    lines = [f"📊 Markt-Preis: € {_fmt_int(median)} ({count} Vergleiche)"]
    if abs(pct) < 3:
        lines.append(f"⚪ Fairer Preis: ±€ {_fmt_int(abs_diff)} (~Marktwert)")
    elif pct < 0:
        if pct <= -15:
            lines.append(f"🟢🟢🟢 *SUPER PREIS*: -€ {_fmt_int(abs_diff)} ({pct}% unter Markt) 💎")
        else:
            lines.append(f"🟢 Guter Preis: -€ {_fmt_int(abs_diff)} ({pct}% unter Markt)")
    else:
        if pct >= 15:
            lines.append(f"🔴🔴🔴 *ÜBERTEUERT*: +€ {_fmt_int(diff)} (+{pct}% über Markt)")
        else:
            lines.append(f"🔴 Über Markt: +€ {_fmt_int(diff)} (+{pct}%)")
    return lines


def make_session(base_url, name):
    s = requests.Session()
    headers = build_headers()
    s.headers.update(headers)
    ua_short = headers["User-Agent"].split(")")[0][-40:]
    print(f"→ {name}: Hole Homepage für Cookies (UA: ...{ua_short})")
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            h = s.get(base_url, timeout=30)
            print(f"  Homepage Status: {h.status_code}")
            return s
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"  Versuch {attempt}/{MAX_RETRIES} fehlgeschlagen ({type(e).__name__}), warte {wait}s...")
                time.sleep(wait)
    print(f"  Alle {MAX_RETRIES} Homepage-Versuche fehlgeschlagen, fahre trotzdem fort: {last_exc}")
    return s


def fetch_page(session, url, label):
    print(f"→ Hole {label}...")
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=30)
            print(f"  Status: {r.status_code}, Length: {len(r.text)}")
            r.raise_for_status()
            return r.text
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"  Versuch {attempt}/{MAX_RETRIES} fehlgeschlagen ({type(e).__name__}), warte {wait}s...")
                time.sleep(wait)
            else:
                print(f"  Alle {MAX_RETRIES} Versuche fehlgeschlagen")
    raise last_exc


def extract_willhaben(html):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    script = soup.find("script", id="__NEXT_DATA__")
    if script:
        try:
            data = json.loads(script.string)
        except Exception:
            return listings
        def walk(obj):
            if isinstance(obj, dict):
                k = obj.keys()
                if "id" in k and "description" in k and "attributes" in k:
                    listings.append(obj)
                elif "id" in k and "verticalId" in k and "attributes" in k:
                    listings.append(obj)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for it in obj:
                    walk(it)
        walk(data)
    return listings


def extract_as_engine(html):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        print("  ✗ kein __NEXT_DATA__")
        return listings
    try:
        data = json.loads(script.string)
    except Exception as e:
        print(f"  JSON-Fehler: {e}")
        return listings
    raw = data.get("props", {}).get("pageProps", {}).get("listings", [])
    if not raw:
        def walk(obj):
            if isinstance(obj, dict):
                if "id" in obj and "vehicle" in obj:
                    listings.append(obj)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for it in obj:
                    walk(it)
        walk(data)
        raw = listings
    return list(raw)


def get_wh_attr(ad, name):
    attrs = ad.get("attributes", {}).get("attribute", [])
    if isinstance(attrs, list):
        for a in attrs:
            if a.get("name") == name:
                values = a.get("values", [])
                if values:
                    return values[0]
    return None


def wh_url(ad):
    ad_id = str(ad.get("id", "?"))
    seo_url = get_wh_attr(ad, "SEO_URL")
    return f"https://www.willhaben.at/iad/{seo_url}" if seo_url else f"https://www.willhaben.at/iad/object?adId={ad_id}"


def wh_price(ad):
    return _parse_int(get_wh_attr(ad, "PRICE"))


def wh_title(ad):
    title = ad.get("description", "Auto") or "Auto"
    return re.sub(r"\s+", " ", title).strip()[:200]


def wh_meta(ad):
    title = wh_title(ad)
    make, model = parse_make_model_from_title(title)
    year = _parse_int(get_wh_attr(ad, "YEAR_MODEL"))
    km = _parse_int(get_wh_attr(ad, "MILEAGE"))
    return {"ma": make, "mo": model, "y": year, "km": km, "body": wh_body(ad)}


def as_engine_url(ad, base_url):
    ad_id = str(ad.get("id", "?"))
    rel = ad.get("relativeUrl") or ad.get("url") or ""
    if rel.startswith("http"):
        return rel
    elif rel.startswith("/"):
        return f"{base_url}{rel}"
    elif rel:
        return f"{base_url}/{rel}"
    return f"{base_url}/offers/{ad_id}"


def autoscout_url(ad):
    return as_engine_url(ad, "https://www.autoscout24.at")


def gebrauchtwagen_url(ad):
    return as_engine_url(ad, "https://www.gebrauchtwagen.at")


def as_engine_price(ad):
    prices = ad.get("prices", {}) or {}
    price_obj = ad.get("price", {}) or {}
    tracking = ad.get("tracking", {}) or {}
    amount = _first_truthy(
        prices.get("public", {}).get("priceRaw") if isinstance(prices.get("public"), dict) else None,
        prices.get("public", {}).get("price") if isinstance(prices.get("public"), dict) else None,
        price_obj.get("amount"),
        price_obj.get("priceRaw"),
        tracking.get("price"),
    )
    return _parse_int(amount)


def as_engine_title(ad):
    vehicle = ad.get("vehicle", {}) or {}
    tracking = ad.get("tracking", {}) or {}
    make = _first_truthy(
        vehicle.get("makeName"), vehicle.get("make"),
        tracking.get("make"), tracking.get("makeFormatted"),
        ad.get("make")
    ) or ""
    model = _first_truthy(
        vehicle.get("modelName"), vehicle.get("model"),
        tracking.get("model"), tracking.get("modelFormatted"),
        ad.get("model")
    ) or ""
    return f"{make} {model}".strip() or "Auto"


def as_engine_meta(ad):
    vehicle = ad.get("vehicle", {}) or {}
    tracking = ad.get("tracking", {}) or {}
    make = _first_truthy(
        vehicle.get("makeName"), vehicle.get("make"),
        tracking.get("make"), tracking.get("makeFormatted"),
        ad.get("make")
    ) or ""
    model = _first_truthy(
        vehicle.get("modelName"), vehicle.get("model"),
        tracking.get("model"), tracking.get("modelFormatted"),
        ad.get("model")
    ) or ""
    make_lower = make.lower()
    if make_lower in KNOWN_MAKES_LOWER:
        make = KNOWN_MAKES_LOWER[make_lower]
    year_raw = _first_truthy(
        vehicle.get("firstRegistration"),
        vehicle.get("firstRegistrationDate"),
        vehicle.get("firstRegistrationString"),
        tracking.get("firstRegistration"),
    )
    year = _extract_year_int(year_raw)
    km_raw = _first_truthy(
        vehicle.get("mileageInKm"),
        vehicle.get("mileageInKmRaw"),
        vehicle.get("mileage"),
        tracking.get("mileage"),
    )
    km = _parse_int(km_raw)
    return {"ma": make or None, "mo": model or None, "y": year, "km": km, "body": as_engine_body(ad)}


def get_template_message():
    if not CONTACT_PHONE:
        return ""
    template = random.choice(MESSAGE_TEMPLATES)
    return template.format(phone=CONTACT_PHONE)


def is_zip_in_bayern(zip_str):
    if not zip_str:
        return False
    zip_str = str(zip_str).strip()
    if not zip_str.isdigit() or len(zip_str) != 5:
        return False
    plz = int(zip_str)
    return any(low <= plz <= high for low, high in BAYERN_RANGES)


def is_target_willhaben(ad):
    state = (get_wh_attr(ad, "STATE") or "").lower()
    postcode = str(get_wh_attr(ad, "POSTCODE") or get_wh_attr(ad, "ZIP_CODE") or "")
    at_states = ["wien", "niederösterreich", "niederoesterreich", "oberösterreich",
                 "oberoesterreich", "burgenland", "kärnten", "kaernten", "salzburg",
                 "steiermark", "tirol", "vorarlberg"]
    if any(s in state for s in at_states):
        return True
    if "bayern" in state or "bavaria" in state:
        return True
    if postcode.isdigit():
        if len(postcode) == 4:
            return True
        if is_zip_in_bayern(postcode):
            return True
    other_de = ["baden", "berlin", "brandenburg", "bremen", "hamburg", "hessen",
                "mecklenburg", "niedersachsen", "nordrhein", "rheinland", "saarland",
                "sachsen", "schleswig", "thüringen", "thueringen"]
    if any(s in state for s in other_de):
        return False
    return True


def is_target_autoscout(ad):
    location = ad.get("location", {}) or {}
    country = (location.get("countryCode") or "").upper()
    zip_code = str(location.get("zip") or "")
    if country == "AT":
        return True
    if country == "DE":
        return is_zip_in_bayern(zip_code)
    return False


def is_target_gebrauchtwagen(ad):
    location = ad.get("location", {}) or {}
    country = (location.get("countryCode") or "").upper()
    return country == "AT" or not country


def is_ooe_willhaben(ad):
    state = (get_wh_attr(ad, "STATE") or "").lower()
    if "oberösterreich" in state or "oberoesterreich" in state:
        return True
    postcode = str(get_wh_attr(ad, "POSTCODE") or get_wh_attr(ad, "ZIP_CODE") or "")
    if postcode and len(postcode) == 4 and postcode.startswith("4"):
        return True
    location = (get_wh_attr(ad, "LOCATION") or "").lower()
    for city in ["linz", "wels", "steyr", "ried", "vöcklabruck", "gmunden", "braunau"]:
        if city in location:
            return True
    return False


def is_ooe_as_engine(ad):
    location = ad.get("location", {}) or {}
    country = (location.get("countryCode") or "").upper()
    zip_code = str(location.get("zip") or "")
    if country == "AT" and zip_code.startswith("4") and len(zip_code) == 4:
        return True
    if not country and zip_code.startswith("4") and len(zip_code) == 4:
        return True
    return False


def format_willhaben(ad):
    title = wh_title(ad)
    price = get_wh_attr(ad, "PRICE_FOR_DISPLAY") or get_wh_attr(ad, "PRICE")
    price_raw = get_wh_attr(ad, "PRICE")
    price_int = _parse_int(price_raw)
    mileage = get_wh_attr(ad, "MILEAGE")
    year = get_wh_attr(ad, "YEAR_MODEL")
    location = get_wh_attr(ad, "LOCATION") or get_wh_attr(ad, "STATE")
    link = wh_url(ad)
    template = get_template_message()

    meta = wh_meta(ad)
    market_data = calculate_market_value(meta["ma"], meta["mo"], meta["y"], meta["km"])
    market_lines = format_market_value(market_data, price_int)

    parts = []
    if is_ooe_willhaben(ad):
        parts.append("⭐ *OBERÖSTERREICH-TREFFER* ⭐")
    parts.append(f"🚗 *{escape_markdown(title)}*")
    parts.append("_(willhaben)_")
    if price: parts.append(f"💶 *{escape_markdown(price)}*")
    for ml in market_lines:
        parts.append(ml)
    info = []
    if year: info.append(f"BJ {escape_markdown(year)}")
    if mileage: info.append(f"{escape_markdown(mileage)} km")
    if info: parts.append(" | ".join(info))
    if location: parts.append(f"📍 {escape_markdown(location)}")
    parts.append("")
    parts.append(f"👉 [INSERAT ANSEHEN]({link}) 👈")
    if template:
        parts.append("")
        parts.append("📋 *Nachricht zum Kopieren:*")
        parts.append(f"`{template}`")
    return "\n".join(parts)


def format_as_engine(ad, platform_label, base_url):
    ad_id = str(ad.get("id", "?"))
    vehicle = ad.get("vehicle", {}) or {}
    prices = ad.get("prices", {}) or {}
    price_obj = ad.get("price", {}) or {}
    location = ad.get("location", {}) or {}
    tracking = ad.get("tracking", {}) or {}
    make = _first_truthy(
        vehicle.get("makeName"), vehicle.get("make"),
        tracking.get("make"), tracking.get("makeFormatted"),
        ad.get("make")
    ) or ""
    model = _first_truthy(
        vehicle.get("modelName"), vehicle.get("model"),
        tracking.get("model"), tracking.get("modelFormatted"),
        ad.get("model")
    ) or ""
    version = _first_truthy(
        vehicle.get("modelVersionInput"), vehicle.get("version"),
        vehicle.get("modelVariant"), tracking.get("version")
    ) or ""
    title = " ".join(filter(None, [str(make), str(model), str(version)])).strip() or "Auto"

    price_text = _first_truthy(
        prices.get("public", {}).get("priceFormatted") if isinstance(prices.get("public"), dict) else None,
        prices.get("priceFormatted"),
        price_obj.get("priceFormatted"),
        price_obj.get("formatted"),
        prices.get("formatted"),
    )
    price_raw = as_engine_price(ad)
    if not price_text and price_raw:
        price_text = f"€ {_fmt_int(price_raw)}"

    year = _first_truthy(
        vehicle.get("firstRegistration"),
        vehicle.get("firstRegistrationDate"),
        vehicle.get("firstRegistrationFormatted"),
        vehicle.get("firstRegistrationString"),
        tracking.get("firstRegistration"),
        tracking.get("firstRegistrationFormatted"),
        ad.get("firstRegistration"),
    )

    mileage_raw = _first_truthy(
        vehicle.get("mileageInKm"),
        vehicle.get("mileageInKmRaw"),
        vehicle.get("mileage"),
        vehicle.get("mileageRaw"),
        tracking.get("mileage"),
        tracking.get("mileageInKm"),
        ad.get("mileage"),
    )
    if mileage_raw is None:
        mileage_text = ""
    elif isinstance(mileage_raw, (int, float)):
        mileage_text = f"{_fmt_int(mileage_raw)} km"
    elif isinstance(mileage_raw, str):
        if "km" in mileage_raw.lower():
            mileage_text = mileage_raw
        else:
            try:
                mileage_text = f"{_fmt_int(mileage_raw)} km"
            except Exception:
                mileage_text = mileage_raw
    else:
        mileage_text = str(mileage_raw)

    template = get_template_message()
    meta = as_engine_meta(ad)
    market_data = calculate_market_value(meta["ma"], meta["mo"], meta["y"], meta["km"])
    market_lines = format_market_value(market_data, price_raw)

    city = location.get("city", "") or ""
    zip_code = str(location.get("zip") or "")
    country = location.get("countryCode", "") or ""
    location_text = " ".join(filter(None, [zip_code, city, country]))

    rel = ad.get("relativeUrl") or ad.get("url") or ""
    if rel.startswith("http"):
        link = rel
    elif rel.startswith("/"):
        link = f"{base_url}{rel}"
    elif rel:
        link = f"{base_url}/{rel}"
    else:
        link = f"{base_url}/offers/{ad_id}"

    parts = []
    if is_ooe_as_engine(ad):
        parts.append("⭐ *OBERÖSTERREICH-TREFFER* ⭐")
    parts.append(f"🚗 *{escape_markdown(title)}*")
    parts.append(f"_({platform_label})_")
    if price_text: parts.append(f"💶 *{escape_markdown(price_text)}*")
    for ml in market_lines:
        parts.append(ml)
    info = []
    if year: info.append(f"BJ {escape_markdown(year)}")
    if mileage_text: info.append(escape_markdown(mileage_text))
    if info: parts.append(" | ".join(info))
    if location_text: parts.append(f"📍 {escape_markdown(location_text)}")
    parts.append("")
    parts.append(f"👉 [INSERAT ANSEHEN]({link}) 👈")
    if template:
        parts.append("")
        parts.append("📋 *Nachricht zum Kopieren:*")
        parts.append(f"`{template}`")
    return "\n".join(parts)


def format_autoscout(ad):
    return format_as_engine(ad, "AutoScout24", "https://www.autoscout24.at")


def format_gebrauchtwagen(ad):
    return format_as_engine(ad, "gebrauchtwagen.at", "https://www.gebrauchtwagen.at")


def format_price_drop(title, old_price, new_price, ad_msg):
    diff = old_price - new_price
    pct = round(100 * diff / old_price) if old_price > 0 else 0
    header = (
        f"💰 *PREIS GESUNKEN!* 📉\n"
        f"*{escape_markdown(title)}*\n"
        f"Alt: € {_fmt_int(old_price)}\n"
        f"Neu: € {_fmt_int(new_price)} (-€ {_fmt_int(diff)}, -{pct}%)\n"
        f"━━━━━━━━━━━━━━━\n"
    )
    return header + ad_msg


def format_dead_listing(title, url):
    return (
        f"⚠️ *INSERAT VERSCHWUNDEN*\n"
        f"🚗 {escape_markdown(title)}\n"
        f"🚫 Anzeige nicht mehr verfügbar\n"
        f"🤔 Möglicher Scam ODER sehr schnell verkauft\n"
        f"\n"
        f"👉 [INSERAT ANSEHEN]({url}) 👈"
    )


def get_photo_willhaben(ad):
    img = ad.get("advertImage", {})
    if isinstance(img, dict):
        ref = img.get("referenceImageUrl")
        if ref: return ref
    media = get_wh_attr(ad, "ALL_IMAGE_URLS") or get_wh_attr(ad, "MMO")
    if media:
        first = media.split(";")[0] if ";" in media else media
        return first if first.startswith("http") else f"https://cache.willhaben.at/mmo/{first}"
    return None


def get_photo_as_engine(ad):
    images = ad.get("images") or ad.get("vehicle", {}).get("images") or []
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            for k in ("url", "src", "mainImage", "imageUrl", "raw"):
                if first.get(k):
                    u = first[k]
                    return u if u.startswith("http") else f"https:{u}"
        elif isinstance(first, str):
            return first if first.startswith("http") else f"https:{first}"
    return None


def send_telegram(message, photo_url=None):
    for chat_id in TELEGRAM_CHAT_IDS:
        if photo_url:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {"chat_id": chat_id, "photo": photo_url, "caption": message, "parse_mode": "Markdown"}
        else:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown", "disable_web_page_preview": False}
        try:
            r = requests.post(url, data=payload, timeout=20)
            if r.status_code != 200:
                payload.pop("parse_mode", None)
                r = requests.post(url, data=payload, timeout=20)
            if r.status_code != 200:
                print(f"  Telegram-Fehler an {chat_id}: {r.status_code} {r.text[:120]}")
        except Exception as e:
            print(f"  Telegram-Exception an {chat_id}: {e}")


def _do_link_request(url):
    try:
        r = requests.get(url, timeout=15, headers=HEADERS, allow_redirects=True)
        return r
    except Exception as e:
        print(f"  Link-Check Exception {url[:60]}: {type(e).__name__}")
        return None


def _has_dead_keywords(html):
    if not html:
        return None
    body_lower = html.lower()[:80000]
    for kw in STRONG_DEAD_KEYWORDS:
        if kw in body_lower:
            return kw
    return None


def has_live_indicators(html):
    """Prüft ob die HTML-Seite typische Inserate-Daten enthält (= lebt)."""
    if not html:
        return False
    body_lower = html.lower()[:200000]
    indicators = [
        "baujahr",
        "erstzulassung",
        "kilometerstand",
        "leistung in kw",
        "leistung in ps",
        "kraftstoff",
        "getriebe",
        "fahrzeugart",
        "verkäufer kontaktieren",
        "verkäufer anrufen",
        '"price"',
        '"mileage"',
        '"firstregistration"',
    ]
    found = sum(1 for kw in indicators if kw in body_lower)
    return found >= 3


def is_listing_alive(url):
    if not url:
        return True
    r = _do_link_request(url)
    if r is None:
        return True
    if r.status_code in (404, 410):
        print(f"  ↳ Status {r.status_code}: sicher tot")
        return False
    if r.status_code >= 400:
        print(f"  ↳ Status {r.status_code}: temporär, als lebendig markiert")
        return True
    found_kw = _has_dead_keywords(r.text)
    if not found_kw:
        return True
    if has_live_indicators(r.text):
        print(f"  ↳ Keyword '{found_kw[:40]}' gefunden ABER Inserat-Daten auch da → False Positive, lebendig")
        return True
    print(f"  ↳ Verdacht (Keyword '{found_kw[:40]}'), Double-Check in {LINK_CHECK_DOUBLE_DELAY}s...")
    time.sleep(LINK_CHECK_DOUBLE_DELAY)
    r2 = _do_link_request(url)
    if r2 is None:
        return True
    if r2.status_code in (404, 410):
        print(f"  ↳ Double-Check: Status {r2.status_code}, jetzt sicher tot")
        return False
    if r2.status_code >= 400:
        return True
    found_kw2 = _has_dead_keywords(r2.text)
    if not found_kw2:
        print(f"  ↳ Double-Check: jetzt OK, war False Positive")
        return True
    if has_live_indicators(r2.text):
        print(f"  ↳ Double-Check: Keyword da ABER Inserat-Daten auch → False Positive, lebendig")
        return True
    print(f"  ↳ Double-Check: nochmal tot bestätigt")
    return False
    

def check_dead_listings(seen):
    now = datetime.now(timezone.utc)
    dead_count = 0
    checks_done = 0
    for ad_id, info in list(seen.items()):
        if checks_done >= LINK_CHECK_LIMIT:
            break
        ts = info.get("ts")
        url = info.get("u")
        ch = info.get("ch")
        if not ts or not url or ch:
            continue
        ts_obj = parse_iso(ts)
        if not ts_obj:
            continue
        delta_min = (now - ts_obj).total_seconds() / 60
        if delta_min < LINK_CHECK_MIN_MIN or delta_min > LINK_CHECK_MAX_MIN:
            continue
        checks_done += 1
        title = info.get("t", "Unbekanntes Auto")
        print(f"  Prüfe: {ad_id} - {title[:50]}")
        alive = is_listing_alive(url)
        info["ch"] = "alive" if alive else "dead"
        if not alive:
            print(f"  💀 Tot bestätigt: {ad_id}")
            send_telegram(format_dead_listing(title, url))
            dead_count += 1
    if checks_done > 0:
        print(f"  Link-Check zusammenfassung: {checks_done} geprüft, {dead_count} tot")
    return dead_count


# ------------------- HAENDLER-PROFIL (Dev) -------------------
# Nur diese 4 Marken werden gepusht. Gescraped wird weiter breit (gut fuer Marktwert-DB).
DEALER_MODE = True
DEALER_BRANDS = {"VW", "Audi", "BMW", "Mercedes-Benz"}

LIMO_POS = ["limousine", "limo", "sedan", "stufenheck"]
LIMO_NEG = ["kombi", "avant", "touring", "variant", "sportback", "gran coupe",
            "gran coupe", "gran turismo", "shooting brake", "suv", "coupe", "coupe",
            "cabrio", "roadster", "van", "kompakt", "schraegheck", "fliessheck",
            "t-modell", "tourer", "allroad", "cross country", "kasten", "bus",
            "hatchback", "fastback", "liftback", "active tourer", "gran tourer", "scenic"]


def as_engine_body(ad):
    vehicle = ad.get("vehicle", {}) or {}
    tracking = ad.get("tracking", {}) or {}
    return _first_truthy(
        vehicle.get("bodyType"), vehicle.get("body"), vehicle.get("bodyTypeName"),
        tracking.get("bodyType"), tracking.get("body"), ad.get("bodyType"),
    ) or ""


def wh_body(ad):
    return (get_wh_attr(ad, "BODY") or get_wh_attr(ad, "BODY_DESCRIPTION")
            or get_wh_attr(ad, "CAR_TYPE") or get_wh_attr(ad, "BODY_TYPE")
            or get_wh_attr(ad, "BODY_DYNAMIC") or "")


def is_limousine(body, title):
    t = ((body or "") + " " + (title or "")).lower()
    if any(k in t for k in LIMO_NEG):
        return False
    if any(k in t for k in LIMO_POS):
        return True
    return False  # unbekannt -> konservativ als NICHT-Limousine behandeln


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
    # VW + Audi
    if year >= 2014:
        return True
    return year >= 2008 and km is not None and km <= 240000


def dealer_decision(meta, title):
    make = meta.get("ma")
    if make not in DEALER_BRANDS:
        return False
    return car_passes_dealer_rules(make, meta.get("body"), title, meta.get("y"), meta.get("km"))


def process_listings(listings, prefix, seen, first_run, format_func, photo_func, ooe_check, region_check, url_func, price_func, title_func, meta_func, sibling_prefix=None):
    new_count = 0
    new_ooe = 0
    price_drops = 0
    zombie_count = 0
    skipped_region = 0
    cross_dupes = 0
    dealer_skipped = 0
    now = now_iso()

    def is_cross_dupe(raw_id):
        # True wenn dasselbe Auto schon auf der Schwester-Plattform GEMELDET wurde
        # (z.B. gebrauchtwagen.at-Inserat, das schon als AutoScout24-Push rausging).
        # "skip"-Einträge (außerhalb Region) zählen NICHT als gemeldet.
        if not sibling_prefix:
            return False
        sib = seen.get(f"{sibling_prefix}:{raw_id}")
        return bool(sib) and sib.get("ch") != "skip"

    for ad in listings:
        raw_id = ad.get('id', '')
        if not raw_id:
            continue
        ad_id = f"{prefix}:{raw_id}"

        if not region_check(ad):
            if ad_id not in seen:
                seen[ad_id] = {"p": None, "f": now, "u": None, "t": None, "ts": None, "ch": "skip",
                               "ma": None, "mo": None, "y": None, "km": None}
            skipped_region += 1
            continue

        current_price = price_func(ad)
        current_url = url_func(ad)
        current_title = title_func(ad)
        meta = meta_func(ad)

        if ad_id in seen:
            info = seen[ad_id]

            if is_zombie_entry(info) and not first_run:
                info["p"] = current_price
                info["f"] = now
                info["u"] = current_url
                info["t"] = current_title
                info["ts"] = now
                info["ch"] = None
                info["ma"] = meta.get("ma")
                info["mo"] = meta.get("mo")
                info["y"] = meta.get("y")
                info["km"] = meta.get("km")
                info["b"] = meta.get("body")

                if is_cross_dupe(raw_id):
                    cross_dupes += 1
                    print(f"  ⏭️ Cross-Dedup: {ad_id} schon als {sibling_prefix}:{raw_id} gemeldet (Zombie, kein 2. Push)")
                    continue

                if DEALER_MODE and not dealer_decision(meta, current_title):
                    if meta.get("ma") in DEALER_BRANDS:
                        dealer_skipped += 1
                        print(f"  ⏭️ Dealer-Filter raus (Zombie): {ad_id} {meta.get('ma')} BJ{meta.get('y')} {meta.get('km')}km body='{meta.get('body')}'")
                    continue

                send_telegram(format_func(ad), photo_func(ad))
                zombie_count += 1
                if ooe_check(ad):
                    new_ooe += 1
                print(f"  🧟 Zombie-Push: {ad_id} - {current_title[:50]}")
                continue

            if not info.get("ma") and meta.get("ma"):
                info["ma"] = meta["ma"]
            if not info.get("mo") and meta.get("mo"):
                info["mo"] = meta["mo"]
            if info.get("y") is None and meta.get("y") is not None:
                info["y"] = meta["y"]
            if info.get("km") is None and meta.get("km") is not None:
                info["km"] = meta["km"]
            if not info.get("b") and meta.get("body"):
                info["b"] = meta.get("body")

            old_price = info.get("p")
            if old_price and current_price and current_price < old_price:
                if not first_run:
                    if is_cross_dupe(raw_id):
                        cross_dupes += 1
                        print(f"  ⏭️ Cross-Dedup: {ad_id} schon als {sibling_prefix}:{raw_id} gemeldet (Preis-Drop, kein 2. Push)")
                    elif DEALER_MODE and not dealer_decision(meta, current_title):
                        pass  # Dealer-Filter: kein Preis-Drop-Push
                    else:
                        ad_msg = format_func(ad)
                        drop_msg = format_price_drop(current_title, old_price, current_price, ad_msg)
                        send_telegram(drop_msg, photo_func(ad))
                        price_drops += 1
                        print(f"  💰 Preis-Drop: {ad_id} {old_price} → {current_price}")
                info["p"] = current_price
                info["ts"] = now
                info["ch"] = None
            continue

        seen[ad_id] = {
            "p": current_price,
            "f": now,
            "u": current_url,
            "t": current_title,
            "ts": None if first_run else now,
            "ch": None,
            "ma": meta.get("ma"),
            "mo": meta.get("mo"),
            "y": meta.get("y"),
            "km": meta.get("km"),
            "b": meta.get("body"),
        }
        if first_run:
            continue

        if is_cross_dupe(raw_id):
            cross_dupes += 1
            print(f"  ⏭️ Cross-Dedup: {ad_id} schon als {sibling_prefix}:{raw_id} gemeldet (kein 2. Push)")
            continue

        if DEALER_MODE and not dealer_decision(meta, current_title):
            if meta.get("ma") in DEALER_BRANDS:
                dealer_skipped += 1
                print(f"  ⏭️ Dealer-Filter raus: {ad_id} {meta.get('ma')} BJ{meta.get('y')} {meta.get('km')}km body='{meta.get('body')}'")
            continue

        send_telegram(format_func(ad), photo_func(ad))
        new_count += 1
        if ooe_check(ad):
            new_ooe += 1
        print(f"  Gemeldet: {ad_id}")

    if skipped_region:
        print(f"  Übersprungen (außerhalb Region): {skipped_region}")
    if cross_dupes:
        print(f"  ⏭️ Cross-Dedup übersprungen: {cross_dupes}")
    if dealer_skipped:
        print(f"  ⏭️ Dealer-Filter raus (Zielmarken): {dealer_skipped}")
    if zombie_count:
        print(f"  🧟 Zombies reaktiviert: {zombie_count}")
    return new_count + zombie_count, new_ooe, price_drops


def market_db_stats(seen):
    complete = 0
    for ad_id, info in seen.items():
        if ad_id == "__health__":
            continue
        if info.get("ma") and info.get("mo") and info.get("y") and info.get("km") and info.get("p"):
            complete += 1
    return complete


def update_platform_health(seen, found_counts, first_run):
    # Bot-Block-Warnung: warnt wenn eine Plattform 0 Inserate liefert (= Sperre/Captcha),
    # aber erst nach 2 Laeufen in Folge (Daempfung gegen einmalige Aussetzer).
    # Zaehlt die GELADENEN Inserate pro Plattform, NICHT die neuen Pushes.
    if first_run:
        return
    health = seen.get("__health__")
    if not isinstance(health, dict):
        health = {}
    labels = {"wh": "willhaben", "as": "AutoScout24", "gw": "gebrauchtwagen.at"}
    for key, label in labels.items():
        found = found_counts.get(key, 0)
        prev = health.get(key, 0)
        if found == 0:
            new = prev + 1
            health[key] = new
            if new == 2:
                send_telegram(
                    f"\U0001F6AB *{label}* liefert seit 2 Laeufen 0 Inserate, vermutlich Bot-Sperre.\n"
                    f"Der Bot laeuft weiter, die anderen Plattformen sind nicht betroffen."
                )
                print(f"  \U0001F6AB Health-Warnung gesendet: {label} (2 Laeufe in Folge 0 Inserate)")
            else:
                print(f"  \u26A0\uFE0F {label}: {new} Laeufe in Folge 0 Inserate")
        else:
            if prev >= 2:
                send_telegram(f"\u2705 *{label}* liefert wieder Inserate ({found}). Sperre vorbei.")
                print(f"  \u2705 Health-Recovery gesendet: {label}")
            health[key] = 0
    seen["__health__"] = health


def main():
    global SEEN_CACHE
    seen = load_seen()
    SEEN_CACHE = seen
    first_run = len(seen) == 0
    complete = market_db_stats(seen)
    print(f"Bereits bekannt: {len(seen)} Inserate")
    print(f"Marktwert-DB: {complete} Einträge mit vollständigen Metadaten")
    print(f"Empfänger-Anzahl: {len(TELEGRAM_CHAT_IDS)}")
    print(f"Template-Nachricht aktiv: {'JA' if CONTACT_PHONE else 'NEIN (CONTACT_PHONE fehlt)'}")

    total_new = 0
    total_ooe = 0
    total_drops = 0
    total_listings = 0
    found_counts = {"wh": 0, "as": 0, "gw": 0}

    print("\n=== willhaben ===")
    try:
        s = make_session("https://www.willhaben.at/", "willhaben")
        html = fetch_page(s, WILLHABEN_URL, "willhaben Suchseite")
        listings = extract_willhaben(html)
        print(f"Gefunden: {len(listings)} Inserate")
        found_counts["wh"] = len(listings)
        total_listings += len(listings)
        n, o, d = process_listings(listings, "wh", seen, first_run,
                                   format_willhaben, get_photo_willhaben,
                                   is_ooe_willhaben, is_target_willhaben,
                                   wh_url, wh_price, wh_title, wh_meta)
        total_new += n
        total_ooe += o
        total_drops += d
    except Exception as e:
        print(f"WILLHABEN FEHLER nach Retry: {e}")

    print("\n=== AutoScout24 ===")
    try:
        s = make_session("https://www.autoscout24.at/", "AutoScout24")
        for page in range(1, AUTOSCOUT_PAGES + 1):
            url = AUTOSCOUT_URL + f"&page={page}"
            try:
                html = fetch_page(s, url, f"AutoScout24 Seite {page}")
                listings = extract_as_engine(html)
                print(f"  Seite {page}: {len(listings)} Inserate")
                found_counts["as"] += len(listings)
                total_listings += len(listings)
                n, o, d = process_listings(listings, "as", seen, first_run,
                                           format_autoscout, get_photo_as_engine,
                                           is_ooe_as_engine, is_target_autoscout,
                                           autoscout_url, as_engine_price, as_engine_title, as_engine_meta)
                total_new += n
                total_ooe += o
                total_drops += d
                if page < AUTOSCOUT_PAGES:
                    time.sleep(2)
            except Exception as e:
                print(f"  Seite {page} Fehler nach Retry: {e}")
                break
    except Exception as e:
        print(f"AUTOSCOUT FEHLER: {e}")

    delay = random.randint(2, 6)
    print(f"\n⏰ Pause {delay}s vor gebrauchtwagen.at (gegen Bot-Detection)...")
    time.sleep(delay)
    print("\n=== gebrauchtwagen.at ===")
    try:
        s = make_session("https://www.gebrauchtwagen.at/", "gebrauchtwagen.at")
        for page in range(1, GEBRAUCHTWAGEN_PAGES + 1):
            url = GEBRAUCHTWAGEN_URL + f"&page={page}"
            try:
                html = fetch_page(s, url, f"gebrauchtwagen.at Seite {page}")
                listings = extract_as_engine(html)
                print(f"  Seite {page}: {len(listings)} Inserate")
                found_counts["gw"] += len(listings)
                total_listings += len(listings)
                n, o, d = process_listings(listings, "gw", seen, first_run,
                                           format_gebrauchtwagen, get_photo_as_engine,
                                           is_ooe_as_engine, is_target_gebrauchtwagen,
                                           gebrauchtwagen_url, as_engine_price, as_engine_title, as_engine_meta,
                                           sibling_prefix="as")
                total_new += n
                total_ooe += o
                total_drops += d
                if page < GEBRAUCHTWAGEN_PAGES:
                    time.sleep(2)
            except Exception as e:
                print(f"  Seite {page} Fehler nach Retry: {e}")
                break
    except Exception as e:
        print(f"GEBRAUCHTWAGEN FEHLER: {e}")

    print("\n=== Link-Check ===")
    if not first_run:
        try:
            check_dead_listings(seen)
        except Exception as e:
            print(f"Link-Check Fehler: {e}")
    else:
        print("  Übersprungen (first_run)")

    print("\n=== Plattform-Health ===")
    update_platform_health(seen, found_counts, first_run)

    seen = save_seen(seen)
    SEEN_CACHE = seen
    complete_after = market_db_stats(seen)

    if first_run and total_listings > 0:
        send_telegram(
            f"✅ Fahrzeug-Watcher v1.1 (Haendler-Profil) ist aktiv!\n"
            f"3 Plattformen + Cross-Push-Dedup + Block-Warnung + Template + Preis-Drop + Link-Check + Marktwert-DB + Zombie-Fix + Farb-Emojis + Anti-False-Positive + Header-Rotation.\n"
            f"Marken VW/Audi/BMW/Mercedes(nur Limo). Region: AT + DE/Bayern. Preis bis 15.000€.\n"
            f"{len(seen)} Inserate als 'bekannt' markiert.\n"
            f"📊 Marktwert-DB: {complete_after} vollständige Einträge\n"
            f"📤 Empfänger: {len(TELEGRAM_CHAT_IDS)}\n"
            f"📋 Template: {'aktiv' if CONTACT_PHONE else 'INAKTIV'}"
        )
    elif first_run:
        send_telegram("⚠️ Auto-Watcher läuft, hat aber 0 Inserate gefunden.")
    else:
        print(f"\nNeu: {total_new} | OÖ: {total_ooe} | Preis-Drops: {total_drops} | Marktwert-DB: {complete_after}")


if __name__ == "__main__":
    main()
