"""Pure parsers: Craigslist API payloads -> the public /craigslist/* shapes.
No network here (refdata's static category table is the only lookup), so
everything is testable from fixtures (test_parsers.py).

Conventions: snake_case, `link` not `url`, booleans read as questions,
ISO-8601 UTC timestamps, prices as a number + a separate `currency`, null
for anything missing (never "" / "N/A" / an omitted key).

Skipped upstream fields, on purpose:
  search:  firstImageEncodedSize (image aspect hint for the site's lazy
           layout), dedupeKey (opaque duplicate-cluster number: the
           hide_duplicates filter is the usable form), priceString (the
           same price pre-formatted), pageTitle / humanReadableParams /
           hubLinks / filterButtons (page chrome), cacheId / cacheTs /
           configId (transport state).
  listing: attributes[].forSearch + specialType (the site's own filter ids
           for "more like this" links), priceString, seeMyOther's target
           (the poster's other listings need a logged-in session), the
           <showcontactinfo> placeholder (contact details are only released
           by the captcha-gated reply flow — `has_contact_info` says they
           exist).
"""
import html as html_lib
import re
from datetime import datetime, timedelta, timezone

from craigslist import refdata, refs

PER_PAGE = 120
CHUNK = 360
MAX_RESULTS = 10000

# Site sort token <-> public sort name, and the id the batch call carries.
SORTS = {
    "newest": "date",
    "oldest": "dateoldest",
    "price_low": "priceasc",
    "price_high": "pricedsc",
    "distance": "dist",
    "relevance": "rel",
    "upcoming": "upcoming",
}
SORT_NAMES = {token: name for name, token in SORTS.items()}
SORT_BATCH_IDS = {"date": 1, "dateoldest": 2, "dist": 3, "priceasc": 4, "pricedsc": 5, "rel": 6, "upcoming": 7}

# Fallback currency per country for the few places the API does not say
# (a listing fetched on its own); search responses carry it per site.
COUNTRY_CURRENCIES = {
    "US": "USD", "CA": "CAD", "GB": "GBP", "AU": "AUD", "NZ": "NZD", "IN": "INR", "MX": "MXN",
    "JP": "JPY", "CN": "CNY", "HK": "HKD", "SG": "SGD", "KR": "KRW", "PH": "PHP", "TH": "THB",
    "BR": "BRL", "AR": "ARS", "CL": "CLP", "CO": "COP", "PE": "PEN", "ZA": "ZAR", "IL": "ILS",
    "TR": "TRY", "RU": "RUB", "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN",
    "CZ": "CZK", "HU": "HUF", "AE": "AED", "EG": "EGP", "ID": "IDR", "MY": "MYR", "VN": "VND",
    "TW": "TWD", "PK": "PKR", "BD": "BDT", "UA": "UAH", "RO": "RON", "BG": "BGN", "IS": "ISK",
    "DE": "EUR", "FR": "EUR", "IT": "EUR", "ES": "EUR", "NL": "EUR", "BE": "EUR", "AT": "EUR",
    "IE": "EUR", "PT": "EUR", "FI": "EUR", "GR": "EUR", "LU": "EUR", "HR": "EUR",
}


# ---- primitives --------------------------------------------------------------------

def text(value):
    if value is None or isinstance(value, bool):
        return None
    value = " ".join(str(value).split())
    return value or None


def to_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    digits = re.sub(r"[^\d\-]", "", str(value).split(".")[0])
    try:
        return int(digits)
    except ValueError:
        return None


def to_number(value):
    """'1,250.5' / '$350' / 2 -> a number (int when whole), else None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
        if not match:
            return None
        try:
            number = float(match.group(0).replace(",", ""))
        except ValueError:
            return None
    return int(number) if number == int(number) else number


def to_coordinate(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def iso_timestamp(epoch):
    """Unix seconds -> '2026-10-03T07:43:04Z' (UTC)."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or epoch <= 0:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return None


def iso_date(value):
    """'2026-10-31' passthrough (validated), else None."""
    value = text(value)
    if value and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return None


def html_to_text(body):
    """A listing body (HTML with <br>, lists, links and the
    <showcontactinfo> placeholder) -> plain text with line breaks kept."""
    if not isinstance(body, str) or not body.strip():
        return None
    out = re.sub(r"<showcontactinfo\b[^>]*>.*?</showcontactinfo>", "", body, flags=re.S | re.I)
    out = re.sub(r"<(script|style)\b.*?</\1>", "", out, flags=re.S | re.I)
    out = re.sub(r"\s+", " ", out)                       # source line breaks are not content
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.I)
    out = re.sub(r"</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", out, flags=re.I)
    out = re.sub(r"<li\b[^>]*>", "- ", out, flags=re.I)
    out = re.sub(r"<[^>]+>", "", out)
    out = html_lib.unescape(out).replace("\xa0", " ")
    lines = [" ".join(line.split()) for line in out.split("\n")]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return out or None


def images(image_ids):
    """Image ids -> full-size links."""
    out = []
    for image_id in image_ids if isinstance(image_ids, list) else []:
        link = refs.image_link(image_id)
        if link and link not in out:
            out.append(link)
    return out


def currency_of(area_meta=None, country=None):
    code = text((area_meta or {}).get("currency")) if isinstance(area_meta, dict) else None
    return code or COUNTRY_CURRENCIES.get((country or "").upper())


def area_unit(area_meta):
    """The site's floor-area unit ('ft' / 'm') as 'sqft' / 'sqm'."""
    unit = (area_meta or {}).get("areaUnits") if isinstance(area_meta, dict) else None
    return {"ft": "sqft", "m": "sqm"}.get(unit)


def category_of(category_id=None, code=None, name=None):
    """{id, code, name, section} from whichever identifiers are known."""
    row = refdata.category_by_id(category_id) if category_id is not None else None
    if row is None and code:
        row = refdata.category(code)
    return {
        "id": category_id if category_id is not None else (row or {}).get("id"),
        "code": (row or {}).get("code") or text(code),
        "name": text(name) or (row or {}).get("name"),
        "section": (row or {}).get("section"),
    }


# ---- search --------------------------------------------------------------------------

def _day(min_date_epoch, offset):
    """Event / open-house dates travel as day offsets from decode.minDate."""
    if not isinstance(min_date_epoch, (int, float)) or not isinstance(offset, (int, float)):
        return None
    try:
        base = datetime.fromtimestamp(min_date_epoch, tz=timezone.utc)
        return (base + timedelta(days=offset)).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _apply_tag(item, tag, min_date):
    """One tagged array of a search result ([tag id, values...]), per the
    site's own decoder."""
    if not isinstance(tag, list) or not tag:
        return
    kind, values = tag[0], tag[1:]
    first = values[0] if values else None
    if kind == 13:
        item["uuid"] = text(first)
    elif kind == 1:
        item["start_date"] = _day(min_date, first)
        item["end_date"] = _day(min_date, values[1] if len(values) > 1 else first)
    elif kind == 2:
        item["open_house_dates"] = [d for d in (_day(min_date, v) for v in values) if d]
    elif kind == 3:
        item["sale_dates"] = [d for d in (_day(min_date, v) for v in values) if d]
    elif kind == 4:
        item["image_ids"] = [v for v in values if isinstance(v, str)]
    elif kind == 5:
        item["bedrooms"] = first if isinstance(first, int) and not isinstance(first, bool) else None
        sqft = values[1] if len(values) > 1 else None
        item["area"] = sqft if isinstance(sqft, (int, float)) and not isinstance(sqft, bool) and sqft > 0 else None
    elif kind == 6:
        item["slug"] = text(first)
    elif kind == 7:
        item["compensation"] = text(first)
    elif kind == 8:
        item["company_name"] = text(first)
    elif kind == 9:
        item["odometer"] = to_int(first)
    elif kind == 11:
        item["monthly_payment"] = to_number(first)
    elif kind == 12:
        item["job_title"] = text(first)
    # 10 = the price pre-formatted ("$2,700"): the numeric price is already there.


def _location_of(code, decode):
    """'1:42:3~40.71~-73.76' -> the listing's site, subarea, place name,
    neighborhood and coordinates."""
    out = {"hostname": None, "area_id": None, "subarea": None, "name": None, "neighborhood": None,
           "latitude": None, "longitude": None}
    if not isinstance(code, str):
        return out
    parts = code.split("~")
    indexes = parts[0].split(":")

    def pick(table, position):
        rows = decode.get(table) if isinstance(decode, dict) else None
        try:
            index = int(indexes[position])
        except (IndexError, ValueError):
            return None
        if not isinstance(rows, list) or not 0 <= index < len(rows):
            return None
        return rows[index]

    site = pick("locations", 0)
    if isinstance(site, list) and site:
        out["area_id"] = site[0] if isinstance(site[0], int) else None
        out["hostname"] = text(site[1]) if len(site) > 1 else None
        out["subarea"] = text(site[2]) if len(site) > 2 else None
    name = pick("locationDescriptions", 1)
    out["name"] = text(name) if isinstance(name, str) else None
    hood = pick("neighborhoods", 2)
    out["neighborhood"] = text(hood) if isinstance(hood, str) else None
    if len(parts) >= 3:
        out["latitude"], out["longitude"] = to_coordinate(parts[1]), to_coordinate(parts[2])
    return out


def decode_digest(row, decode):
    """The six fixed fields every search row starts with -> a partial item."""
    if not isinstance(row, list) or len(row) < 5:
        return None
    decode = decode if isinstance(decode, dict) else {}
    min_id, min_posted = decode.get("minPostingId"), decode.get("minPostedDate")
    if not isinstance(row[0], int) or not isinstance(min_id, int):
        return None
    price = row[3] if isinstance(row[3], (int, float)) and not isinstance(row[3], bool) and row[3] >= 0 else None
    return {
        "post_id": min_id + row[0],
        "posted_at": iso_timestamp(min_posted + row[1]) if isinstance(min_posted, int) and isinstance(row[1], int) else None,
        "category_id": row[2] if isinstance(row[2], int) else None,
        "price": price,
        "location": _location_of(row[4], decode),
    }


def decode_full_row(row, decode):
    """A row of the `full` search call: digest + trailing title / tagged
    arrays (a negative trailing int is the duplicate-cluster key, skipped)."""
    item = decode_digest(row, decode)
    if item is None:
        return None
    min_date = (decode or {}).get("minDate")
    for extra in row[6:]:
        if isinstance(extra, str):
            item["title"] = text(extra)
        elif isinstance(extra, list):
            _apply_tag(item, extra, min_date)
    return item


def decode_batch(data, min_date=None):
    """The `batch` call ({batch: [[id delta, title, [image ids], tags...]],
    minPostingId}) -> {post_id: detail fields}."""
    out = {}
    if not isinstance(data, dict):
        return out
    min_id = data.get("minPostingId")
    for row in data.get("batch") or []:
        if not isinstance(row, list) or len(row) < 2 or not isinstance(row[0], int) or not isinstance(min_id, int):
            continue
        detail = {"title": text(row[1]) if isinstance(row[1], str) else None}
        if len(row) > 2 and isinstance(row[2], list):
            detail["image_ids"] = [v for v in row[2] if isinstance(v, str)]
        for extra in row[3:]:
            if isinstance(extra, list):
                _apply_tag(detail, extra, min_date)
        out[min_id + row[0]] = detail
    return out


def _section_details(section, item, unit):
    """The fields only one section's listings carry, as one typed object."""
    if section == "housing":
        return {
            "bedrooms": item.get("bedrooms"),
            "area": item.get("area"),
            "area_unit": unit if item.get("area") is not None else None,
            "open_house_dates": item.get("open_house_dates") or [],
        }
    if section == "for_sale":
        return {
            "odometer": item.get("odometer"),
            "monthly_payment": item.get("monthly_payment"),
            "sale_dates": item.get("sale_dates") or [],
        }
    if section == "jobs":
        return {
            "job_title": item.get("job_title"),
            "company_name": item.get("company_name"),
            "compensation": item.get("compensation"),
        }
    if section == "gigs":
        return {"compensation": item.get("compensation")}
    if section == "events":
        return {"start_date": item.get("start_date"), "end_date": item.get("end_date")}
    return None


def listing_summary(item, *, areas=None, center=None, first_nearby_id=None, nearby_flag=False):
    """A decoded search item -> the public search-result row.

    `areas` is the response's per-site metadata ({area id: {currency,
    areaUnits, distanceUnits}}); `center` = (latitude, longitude, unit) adds
    the distance from the searched point."""
    loc = item.get("location") or {}
    meta = (areas or {}).get(str(loc.get("area_id"))) if isinstance(areas, dict) else None
    site = refdata.site_by_id(loc.get("area_id")) if loc.get("area_id") is not None else None
    category = category_of(item.get("category_id"))
    uuid = item.get("uuid")
    image_links = images(item.get("image_ids"))
    distance = None
    if center and loc.get("latitude") is not None and loc.get("longitude") is not None:
        km = refdata.distance_km(center[0], center[1], loc["latitude"], loc["longitude"])
        if km is not None:
            distance = round(km / 1.609344 if center[2] == "mi" else km, 1)
    price = item.get("price")
    return {
        "id": uuid,
        "post_id": item.get("post_id"),
        "title": item.get("title"),
        "link": refs.view_link(item.get("slug"), uuid),
        "price": price,
        "currency": currency_of(meta, (site or {}).get("country")) if price is not None else None,
        "posted_at": item.get("posted_at"),
        "is_nearby_result": bool(nearby_flag),
        "category": category,
        "details": _section_details(category.get("section"), item, area_unit(meta)),
        "location": {
            "site": loc.get("hostname"),
            "subarea": loc.get("subarea"),
            "name": loc.get("name"),
            "neighborhood": loc.get("neighborhood"),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "distance": distance,
            "distance_unit": center[2] if center and distance is not None else None,
        },
        "thumbnail_link": refs.image_link((item.get("image_ids") or [None])[0], "300x300"),
        "image_count": len(image_links),
        "images": image_links,
    }


def search_location(location, areas=None):
    """The response's resolved search location."""
    if not isinstance(location, dict):
        return None
    meta = (areas or {}).get(str(location.get("areaId"))) if isinstance(areas, dict) else None
    hostname = text(location.get("url"))
    hostname = hostname.split(".")[0] if hostname else None
    return {
        "site": hostname,
        "name": text(location.get("city")),
        "region": text(location.get("region")),
        "country": text(location.get("country")),
        "postal_code": text(location.get("postal")),
        "latitude": to_coordinate(location.get("lat")),
        "longitude": to_coordinate(location.get("lon")),
        "radius": to_number(location.get("radius")),
        "distance_unit": text((meta or {}).get("distanceUnits")),
        "currency": currency_of(meta, location.get("country")),
        "link": refs.site_link(hostname),
    }


# ---- filters ---------------------------------------------------------------------------

def slug(label):
    """'w/d in unit' -> 'w_d_in_unit', "employee's choice" -> 'employees_choice'."""
    value = re.sub(r"['’]", "", str(label or "").lower())
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or None


def filter_definitions(filters, index):
    """The site's filter form for one category -> rows that say which
    /craigslist/* param drives each filter and which values it takes.
    `index` maps an upstream filter name to {"param": public param,
    "values": {upstream id: public value}, "value": the one value a
    checkbox stands for (event_type=music)}; a filter missing from it is
    reported with is_supported false."""
    out = []

    def add(name, label, kind, options=None):
        if name in ("query", "vicinity", "sort", "subarea") or not name:
            return
        entry = index.get(name) or {}
        out.append({
            "param": entry.get("param"),
            "value": entry.get("value"),
            "label": text(label),
            "type": kind,
            "options": options or [],
            "is_supported": entry.get("param") is not None,
        })

    for row in filters if isinstance(filters, list) else []:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        if kind == "group":
            for field in row.get("form") or []:
                if isinstance(field, dict):
                    add(field.get("name"), field.get("label"), "boolean")
        elif kind == "range":
            label = row.get("label") or row.get("name")
            for bound in ("minVal", "maxVal"):
                spec = row.get(bound)
                if isinstance(spec, dict):
                    add(spec.get("name"), f"{'min' if bound == 'minVal' else 'max'} {label}" if label else None, "number")
        elif row.get("options"):
            values = (index.get(row.get("name")) or {}).get("values") or {}
            options = []
            for option in row["options"]:
                if not isinstance(option, dict) or option.get("value") in ("", "all", None):
                    continue
                value = values.get(str(option.get("value"))) or slug(option.get("label"))
                if value:
                    options.append({"value": value, "label": text(option.get("label"))})
            add(row.get("name"), row.get("label"), "multi_choice" if kind == "multi" else "choice", options)
        else:
            add(row.get("name"), row.get("label"), "boolean" if kind == "boolean" else "text")
    return out


def category_counts(items):
    """sapi/categories/count -> sections with per-category result counts."""
    out = []
    for section in items if isinstance(items, list) else []:
        if not isinstance(section, dict):
            continue
        hub = refdata.category(section.get("abbreviation"))
        rows = []
        for cat in section.get("items") or []:
            if isinstance(cat, dict) and cat.get("abbreviation"):
                rows.append({"code": text(cat.get("abbreviation")), "name": text(cat.get("label")),
                             "count": to_int(cat.get("count")) or 0})
        rows.sort(key=lambda r: -r["count"])
        out.append({
            "section": (hub or {}).get("section"),
            "code": text(section.get("abbreviation")),
            "name": text(section.get("label")),
            "count": to_int(section.get("count")) or 0,
            "categories": rows,
        })
    out.sort(key=lambda r: -r["count"])
    return out


# ---- listing details ---------------------------------------------------------------------

# Upstream attribute key -> public key. Check-mark attributes become
# booleans, so their public names read as questions.
ATTRIBUTE_NAMES = {
    "auto_make_model": "make_model",
    "auto_year": "year",
    "auto_miles": "odometer",
    "auto_cylinders": "cylinders",
    "auto_drivetrain": "drivetrain",
    "auto_fuel_type": "fuel_type",
    "auto_paint": "paint_color",
    "auto_title_status": "title_status",
    "auto_transmission": "transmission",
    "auto_bodytype": "body_type",
    "auto_vin": "vin",
    "auto_size": "size_class",
    "motorcycle_motor_type": "motor_type",
    "motorcycle_street_legal": "is_street_legal",
    "boat_length_overall": "length_overall",
    "boat_propulsion_type": "propulsion_type",
    "sna_type": "vehicle_type",
    "sale_manufacturer": "manufacturer",
    "sale_model": "model",
    "sale_size": "size",
    "sale_time": "start_time",
    "crypto_currency_ok": "is_cryptocurrency_accepted",
    "delivery_available": "is_delivery_available",
    "area": "area",
    "movein_date": "available_date",
    "pets_cat": "are_cats_allowed",
    "pets_dog": "are_dogs_allowed",
    "no_smoking": "is_non_smoking",
    "airconditioning": "has_air_conditioning",
    "wheelchaccess": "is_wheelchair_accessible",
    "ev_charging": "has_ev_charging",
    "private_room": "has_private_room",
    "private_bath": "has_private_bath",
    "broker_fee": "has_no_broker_fee",
    "application_fee": "has_no_application_fee",
    "remuneration": "compensation",
    "is_telecommuting": "is_remote",
    "education_level_completed": "education_level",
    "offered_in_person": "is_offered_in_person",
    "offered_virtually": "is_offered_virtually",
    "resumes_available_morning": "is_available_mornings",
    "resumes_available_afternoon": "is_available_afternoons",
    "resumes_available_evening": "is_available_evenings",
    "resumes_available_overnight": "is_available_overnight",
    "resumes_available_weekdays": "is_available_weekdays",
    "resumes_available_weekends": "is_available_weekends",
}
_INT_ATTRIBUTES = {"auto_year", "auto_miles", "bedrooms", "engine_displacement_cc", "year_manufactured"}
_NUMBER_ATTRIBUTES = {"bathrooms", "boat_length_overall", "rv_length"}
_NEGATIVE_VALUE = re.compile(r"^(no |not |room not )", re.I)


def attributes(rows):
    """The listing's attribute rows -> one {public key: typed value} object.

    A check mark becomes true; "private room" / "no private bath" style
    values become booleans; years, mileage and room counts become numbers;
    a date range becomes start_date / end_date; a date list becomes dates.
    Unknown keys pass through under the site's own key."""
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key = text(row.get("postingAttributeKey"))
        if not key:
            continue
        value = row.get("value")
        special = row.get("specialType")
        name = ATTRIBUTE_NAMES.get(key, key)
        if special == "dateRange":
            start, _, end = str(value or "").partition(",")
            out["start_date"], out["end_date"] = iso_date(start), iso_date(end or start)
        elif special == "dateList":
            out["dates"] = [d for d in (iso_date(v) for v in str(value or "").split(",")) if d]
        elif special == "date":
            out[name] = iso_date(value)
        elif isinstance(value, str) and value.strip() in ("✓", "✔"):
            out[name] = True
        elif key in ("private_room", "private_bath"):
            out[name] = not _NEGATIVE_VALUE.match(str(value or "")) if text(value) else None
        elif key == "area":
            out["area"] = to_number(value)
            unit = "sqm" if "m²" in str(value) else "sqft" if "ft" in str(value) else None
            out["area_unit"] = unit if out["area"] is not None else None
        elif key in _INT_ATTRIBUTES:
            number = to_int(value)
            out[name] = number if number is not None else text(value)
        elif key in _NUMBER_ATTRIBUTES:
            number = to_number(value)
            out[name] = number if number is not None else text(value)
        else:
            out[name] = text(value) if isinstance(value, str) else value
    return out


def _notices(rows):
    out = []
    for row in rows if isinstance(rows, list) else []:
        value = text(row.get("text") or row.get("message") or row.get("label")) if isinstance(row, dict) else text(row)
        if value:
            out.append(value)
    return out


def _was_updated(posting):
    """updatedDate equals recordCreated on a listing that was never edited
    (the page shows "updated" only when they differ)."""
    updated, created = posting.get("updatedDate"), posting.get("recordCreated")
    if not isinstance(updated, (int, float)) or isinstance(updated, bool):
        return False
    return not isinstance(created, (int, float)) or updated - created > 1


def listing_details(posting):
    """One rapi posting -> the public listing object."""
    if not isinstance(posting, dict):
        return None
    loc = posting.get("location") if isinstance(posting.get("location"), dict) else {}
    site = refdata.site_by_id(loc.get("areaId")) if loc.get("areaId") is not None else None
    uuid = text(posting.get("postingUuid"))
    link = text(posting.get("url")) or refs.view_link(None, uuid)
    price = posting.get("price")
    price = price if isinstance(price, (int, float)) and not isinstance(price, bool) and price >= 0 else None
    street = posting.get("streetAddress")
    body = posting.get("body")
    posted = posting.get("postedDate")
    created = posting.get("recordCreated")
    return {
        "id": uuid,
        "post_id": posting.get("postingId") if isinstance(posting.get("postingId"), int) else None,
        "title": text(posting.get("title")),
        "link": link,
        "description": html_to_text(body),
        "price": price,
        "currency": currency_of(None, (site or {}).get("country")) if price is not None else None,
        "posted_at": iso_timestamp(posted),
        "updated_at": iso_timestamp(posting.get("updatedDate")) if _was_updated(posting) else None,
        "created_at": iso_timestamp(created),
        "repost_of_post_id": posting.get("repostOf") if isinstance(posting.get("repostOf"), int) and posting.get("repostOf") else None,
        "is_repost": bool(posting.get("repostOf")),
        "has_contact_info": bool(posting.get("hasContactInfo")) or "<showcontactinfo" in str(body or ""),
        "has_other_listings_by_poster": bool(posting.get("seeMyOther")),
        "company_name": text(posting.get("companyName")),
        "is_disability_friendly": True if posting.get("disabilityOk") else None,
        "is_open_to_recruiters": True if posting.get("recruitersOk") else None,
        "category": category_of(posting.get("categoryId") if isinstance(posting.get("categoryId"), int) else None,
                                posting.get("categoryAbbr"), posting.get("category")),
        "attributes": attributes(posting.get("attributes")),
        "location": {
            "site": text(loc.get("hostname")),
            "site_name": text(loc.get("area")),
            "subarea": text(loc.get("subareaAbbr")),
            "subarea_name": text(loc.get("subArea")),
            "name": text(loc.get("description")),
            "neighborhood": text(loc.get("neighborhood")),
            "address": text(loc.get("displayAddress")),
            "street_address": text(street) if isinstance(street, str) else None,
            "region": (site or {}).get("region"),
            "country": (site or {}).get("country"),
            "latitude": to_coordinate(loc.get("lat")),
            "longitude": to_coordinate(loc.get("lon")),
            "is_exact_location": loc.get("type") == "pin" if loc.get("type") else None,
        },
        "image_count": len(images(posting.get("images"))),
        "images": images(posting.get("images")),
        "notices": _notices(posting.get("notices")),
    }


# ---- sites ---------------------------------------------------------------------------------

def site_public(row, distance_km=None):
    """A refdata site row -> the public location object."""
    out = {
        "id": row.get("id"),
        "site": row.get("hostname"),
        "abbreviation": row.get("abbreviation"),
        "name": row.get("name"),
        "short_name": row.get("short_name"),
        "link": row.get("link"),
        "region": row.get("region"),
        "country": row.get("country"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "timezone": row.get("timezone"),
        "subareas": [dict(s) for s in row.get("subareas") or []],
    }
    if distance_km is not None:
        out["distance_km"] = round(distance_km, 1)
        out["distance_miles"] = round(distance_km / 1.609344, 1)
    return out
