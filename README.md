# 📋 Craigslist Scraper

Craigslist Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Craigslist data for free.

## ✨ What Can I Get?

- 🔎 **Search 700+ Craigslist sites in 79 countries** — by city, ZIP code or GPS point, up to 10,000 results per search
- 🚗 **Cars, apartments & jobs with real filters** — make, year, mileage, bedrooms, pets, remote work and 60+ more
- 📄 **Full listing details** — description, price, typed attributes, address, GPS coordinates & every photo
- 🧭 **134 categories mapped for you** — result counts per category, autocomplete, filters & nearest sites

## 🚗 Example: A Full Craigslist Listing

```json
{
  "listing": {
    "id": "4VSgLW9TwFMYT8hZVnRX2P",
    "post_id": 7977383178,
    "title": "2021 Tesla Model 3 Standard Range Plus",
    "link": "https://www.craigslist.org/view/d/los-angeles-2021-tesla-model-standard/4VSgLW9TwFMYT8hZVnRX2P",
    "description": "2021 Tesla Model 3 Standard Range Plus\n108,300 miles\nClean Title\n\nMidnight Silver Metallic Exterior\nBlack Interior\n\nFeatures include:\n• Glass Panoramic Roof\n• Heated Front Seats\n• Tesla Supercharger Access ...",
    "price": 17800,
    "currency": "USD",
    "posted_at": "2026-10-03T17:37:34Z",
    "is_repost": true,
    "has_contact_info": true,
    "category": { "id": 145, "code": "cto", "name": "cars & trucks - by owner", "section": "for_sale" },
    "attributes": {
      "make_model": "tesla model 3",
      "year": 2021,
      "condition": "good",
      "fuel_type": "electric",
      "odometer": 108300,
      "paint_color": "grey",
      "title_status": "clean",
      "transmission": "automatic",
      "body_type": "sedan"
    },
    "location": {
      "site": "losangeles",
      "subarea_name": "central LA",
      "name": "Los Angeles",
      "region": "CA",
      "country": "US",
      "latitude": 34.06179,
      "longitude": -118.300309,
      "is_exact_location": true
    },
    "image_count": 13,
    "images": [
      "https://images.craigslist.org/00u0u_a3oZNWgHiZH_1320MM_1200x900.jpg",
      "https://images.craigslist.org/00M0M_9K1qH0dbi3b_1320MM_1200x900.jpg"
    ]
  }
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Craigslist Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/craigslist-scraper
cd craigslist-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/search?location=newyork&query=macbook%20pro"
```

```json
{
  "count": 402,
  "per_page": 120,
  "current_page": 1,
  "total_pages": 4,
  "next": "http://localhost:8000/search?location=newyork&query=macbook+pro&page=2",
  "sort": "relevance",
  "location": { "site": "newyork", "name": "new york city", "region": "NY", "country": "US", "currency": "USD" },
  "listings": [
    {
      "id": "2qJUPp9Uu8oVLNFTQfRvWJ",
      "title": "Apple MacBook Pro 13.3” Retina Intel Core i5 2.7GHz 8GB RAM 128GB SSD",
      "link": "https://www.craigslist.org/view/d/ridgewood-apple-macbook-pro-133-retina/2qJUPp9Uu8oVLNFTQfRvWJ",
      "price": 195,
      "currency": "USD",
      "posted_at": "2026-10-05T03:17:40Z",
      "category": { "id": 7, "code": "sys", "name": "computers - by owner", "section": "for_sale" },
      "location": { "site": "newyork", "subarea": "que", "name": "Ridgewood 11385 Queens", "latitude": 40.7021, "longitude": -73.8896 },
      "image_count": 14
    }
  ]
}
```

*Trimmed for readability.* Pass any listing `id` to `/listings/details` to get the full listing.

All 19 endpoints are now live at `http://localhost:8000`.

No API key, no proxies. If you run it on a cloud server and Craigslist answers HTTP 403, set `CRAIGSLIST_PROXY` to a residential proxy URL.

## 📚 Endpoints

19 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Listing Details | `/listings/details` | Everything on one listing page, photos included |
| Search Listings | `/search` | 120 listings per page from any category |
| Search Vehicles | `/vehicles/search` | Cars, bikes, boats & RVs with 40+ filters |
| Search Housing | `/housing/search` | Rentals and homes by rent, beds, pets, laundry |
| Search For Sale | `/for-sale/search` | Items by price, condition and seller type |
| Search Jobs | `/jobs/search` | Jobs with pay, company and remote filter |
| Search Gigs / Services | `/gigs/search`, `/services/search` | Paid gigs and local service ads |
| Search Events / Community / Resumes | `/events/search`, `/community/search`, `/resumes/search` | Events by date, lost & found, job seekers |
| Listings Batch | `/listings/batch` | Up to 20 full listings in one call |
| Search Counts | `/search/counts` | How many results each category holds |
| Search Suggestions | `/search/suggestions` | Autocomplete, plus vehicle make and model |
| Search Filters | `/search/filters` | Every filter a category supports, with values |
| Locations | `/locations`, `/locations/search`, `/locations/nearby` | All 700+ sites, site lookup, nearest sites |
| Categories | `/categories` | All 134 category codes in 8 sections |

`location` takes a Craigslist site (`newyork`), any link on it, a postal code (`94103`) or `latitude,longitude` — the last two search a radius.

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-craigslist-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-craigslist-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-craigslist-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/vehicles/search",
    params={"location": "losangeles", "make_model": "tesla model 3", "fuel_type": "electric"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Craigslist Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Craigslist%20Scraper%20API.)

[![Ask Us by Email about Craigslist Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Craigslist%20Scraper%20API&body=I%20need%20help%20using%20the%20Craigslist%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/craigslist-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/craigslist-scraper)
