"""Marshmallow request schemas for every /craigslist/* route. Generic fields
come from the shared schema_fields.py; this module adds the Craigslist
resolvers. Every schema's load() output is the kwargs dict its endpoint
function takes.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair):

    location   a Craigslist site (newyork | nyc | "new york"), any link on
               that site, a postal code (94103) or latitude,longitude
               (40.7128,-74.0060) — the last two search a radius
    listing    a listing id from a search result OR a craigslist.org
               listing link
    category   a category code (cta) OR its name (cars & trucks)

Multi-select filters take comma-separated values (condition=new,like_new).
Choice values are literal lists here on purpose: the listing tooling reads
them from this file's AST.
"""
from marshmallow import ValidationError, post_load, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CommaListField, DateField, Flag, LimitField,
                           NonNegativeNumber, PageField, PositiveInt, QueryField, RefField,
                           StrippedString)
from craigslist import refdata, refs

SECTIONS = ["for_sale", "housing", "jobs", "gigs", "services", "community", "events", "resumes"]


# ---- id-or-link fields -------------------------------------------------------------

class LocationField(RefField):
    resolver = staticmethod(refs.resolve_location)


class ListingField(RefField):
    resolver = staticmethod(refs.resolve_listing)


class ListingListField(RefField):
    resolver = staticmethod(refs.resolve_listings)


class SubareaField(StrippedString):
    """Three-letter subarea code of the site (brk = Brooklyn on newyork)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refs.resolve_code(value, "subarea")
        except ValueError as e:
            raise ValidationError(str(e))


class CategoryField(StrippedString):
    """Category code or name, optionally restricted to one section."""

    def __init__(self, section=None, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self.section = section

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        try:
            return refdata.resolve_category(value, self.section)
        except ValueError as e:
            raise ValidationError(str(e))


def _optional_query(max_length=180):
    return QueryField(max_length=max_length, required=False, load_default=None)


# ---- search ------------------------------------------------------------------------

class _SearchBase(BaseSchema):
    """Params every listing search shares."""
    location = LocationField()
    query = _optional_query()
    subarea = SubareaField()
    radius = PositiveInt(max_value=1000)
    sort = ChoiceField(["newest", "oldest", "price_low", "price_high", "distance", "relevance", "upcoming"])
    has_image = Flag()
    posted_today = Flag()
    title_only = Flag()
    hide_duplicates = Flag()
    page = PageField(max_page=84)

    @validates_schema
    def _bounds(self, data, **kwargs):
        for low, high in (("min_price", "max_price"), ("min_year", "max_year"), ("min_odometer", "max_odometer"),
                          ("min_bedrooms", "max_bedrooms"), ("min_bathrooms", "max_bathrooms"),
                          ("min_area", "max_area"), ("min_length", "max_length"),
                          ("min_engine_cc", "max_engine_cc"), ("min_monthly_payment", "max_monthly_payment")):
            if data.get(low) is not None and data.get(high) is not None and data[low] > data[high]:
                raise ValidationError(f"{low} must not exceed {high}.", low)
        location = data.get("location") or {}
        if location.get("kind") == "area" and data.get("radius") is not None:
            raise ValidationError("radius needs a postal code or latitude,longitude in location.", "radius")
        if location.get("kind") != "area" and data.get("subarea"):
            raise ValidationError("subarea needs a Craigslist site in location.", "subarea")
        if data.get("sort") == "relevance" and not data.get("query"):
            raise ValidationError("sort=relevance needs a query.", "sort")


class SearchSchema(_SearchBase):
    category = CategoryField()
    min_price = NonNegativeNumber()
    max_price = NonNegativeNumber()


class ForSaleSearchSchema(_SearchBase):
    category = CategoryField(section="for_sale")
    min_price = NonNegativeNumber()
    max_price = NonNegativeNumber()
    seller_type = ChoiceField(["owner", "dealer"])
    condition = CommaListField(value_map={"new": "10", "like_new": "20", "excellent": "30", "good": "40",
                                          "fair": "50", "salvage": "60"})
    make_model = _optional_query(max_length=100)
    is_free = Flag()
    delivery_available = Flag()
    cryptocurrency_accepted = Flag()
    language = CommaListField(value_map={"af": "1", "ca": "2", "da": "3", "de": "4", "en": "5", "es": "6",
                                         "fi": "7", "fr": "8", "it": "9", "nl": "10", "no": "11", "pt": "12",
                                         "sv": "13", "tl": "14", "tr": "15", "zh": "16", "ar": "17", "ja": "18",
                                         "ko": "19", "ru": "20", "vi": "21"}, upper=False, max_items=21)


class VehicleSearchSchema(_SearchBase):
    vehicle_type = ChoiceField({"cars": "cta", "motorcycles": "mca", "boats": "boo", "rvs": "rva",
                                "trailers": "tra", "atvs": "sna", "heavy_equipment": "hva",
                                "aviation": "ava", "auto_parts": "pta", "wheels_tires": "wta",
                                "motorcycle_parts": "mpa", "boat_parts": "bpa"}, load_default="cta")
    min_price = NonNegativeNumber()
    max_price = NonNegativeNumber()
    seller_type = ChoiceField(["owner", "dealer"])
    make_model = _optional_query(max_length=100)
    min_year = PositiveInt(max_value=2100)
    max_year = PositiveInt(max_value=2100)
    min_odometer = NonNegativeNumber()
    max_odometer = NonNegativeNumber()
    min_monthly_payment = NonNegativeNumber()
    max_monthly_payment = NonNegativeNumber()
    condition = CommaListField(value_map={"new": "10", "like_new": "20", "excellent": "30", "good": "40",
                                          "fair": "50", "salvage": "60"})
    transmission = CommaListField(value_map={"manual": "1", "automatic": "2", "other": "3"})
    fuel_type = CommaListField(value_map={"gas": "1", "diesel": "2", "hybrid": "3", "electric": "4", "other": "6"})
    drivetrain = CommaListField(value_map={"fwd": "1", "rwd": "2", "4wd": "3"})
    body_type = CommaListField(value_map={"bus": "1", "convertible": "2", "coupe": "3", "hatchback": "4",
                                          "minivan": "5", "offroad": "6", "pickup": "7", "sedan": "8",
                                          "truck": "9", "suv": "10", "wagon": "11", "van": "12", "other": "13"},
                               max_items=13)
    title_status = CommaListField(value_map={"clean": "1", "salvage": "2", "rebuilt": "3", "parts_only": "4",
                                             "lien": "5", "missing": "6"})
    paint_color = CommaListField(value_map={"black": "1", "blue": "2", "brown": "20", "green": "3", "grey": "4",
                                            "orange": "5", "purple": "6", "red": "7", "silver": "8",
                                            "white": "9", "yellow": "10", "custom": "11"}, max_items=12)
    cylinders = CommaListField(value_map={"3": "1", "4": "2", "5": "3", "6": "4", "8": "5", "10": "6",
                                          "12": "7", "other": "8"})
    min_engine_cc = NonNegativeNumber()
    max_engine_cc = NonNegativeNumber()
    min_length = NonNegativeNumber()
    max_length = NonNegativeNumber()
    motorcycle_type = CommaListField(value_map={"adventure": "16", "bobber": "1", "cafe_racer": "2",
                                                "chopper": "3", "cruiser": "4", "dirtbike": "5",
                                                "dual_sport": "6", "scooter_moped": "8", "sport_bike": "9",
                                                "sport_touring": "10", "standard": "11", "touring": "13",
                                                "trike": "14", "other": "15"}, max_items=14)
    boat_type = CommaListField(value_map={"commercial_boat": "2", "houseboat": "3", "kayak_canoe_sup": "4",
                                          "luxury_classic_yacht": "5", "personal_watercraft": "6",
                                          "pontoon_boat": "7", "powerboat": "8", "rowboat": "9",
                                          "rowing_shell": "10", "sailboat": "11", "ski_boat": "12",
                                          "small_outboard_fishing": "13", "trawler": "14", "other": "1"},
                              max_items=14)
    propulsion_type = CommaListField(value_map={"sail": "1", "power": "2", "human": "3"})
    rv_type = CommaListField(value_map={"class_a": "1", "class_b": "2", "class_c": "3",
                                        "fifth_wheel_trailer": "4", "travel_trailer": "5",
                                        "hybrid_trailer": "6", "folding_popup_trailer": "7",
                                        "teardrop_compact_trailer": "8", "toy_hauler": "9",
                                        "truck_camper": "10", "other": "11"}, max_items=11)
    atv_type = CommaListField(value_map={"snowmobile": "6", "atv": "1", "side_by_side_utv": "5",
                                         "golf_cart": "4", "go_kart": "3", "dune_buggy": "2", "other": "7"})
    motor_type = CommaListField(value_map={"gas": "1", "electric": "2", "other": "3"})
    is_street_legal = Flag()
    delivery_available = Flag()
    cryptocurrency_accepted = Flag()
    language = CommaListField(value_map={"af": "1", "ca": "2", "da": "3", "de": "4", "en": "5", "es": "6",
                                         "fi": "7", "fr": "8", "it": "9", "nl": "10", "no": "11", "pt": "12",
                                         "sv": "13", "tl": "14", "tr": "15", "zh": "16", "ar": "17", "ja": "18",
                                         "ko": "19", "ru": "20", "vi": "21"}, upper=False, max_items=21)

    @post_load
    def _category(self, data, **kwargs):
        data["category"] = data.pop("vehicle_type")
        return data


class HousingSearchSchema(_SearchBase):
    category = CategoryField(section="housing")
    min_price = NonNegativeNumber()
    max_price = NonNegativeNumber()
    min_bedrooms = PositiveInt(max_value=8)
    max_bedrooms = PositiveInt(max_value=8)
    min_bathrooms = PositiveInt(max_value=8)
    max_bathrooms = PositiveInt(max_value=8)
    min_area = NonNegativeNumber()
    max_area = NonNegativeNumber()
    seller_type = ChoiceField(["owner", "dealer"])
    housing_type = CommaListField(value_map={"apartment": "1", "condo": "2", "cottage_cabin": "3", "duplex": "4",
                                             "flat": "5", "house": "6", "in_law": "7", "loft": "8",
                                             "townhouse": "9", "manufactured": "10", "assisted_living": "11",
                                             "land": "12"}, max_items=12)
    laundry = CommaListField(value_map={"in_unit": "1", "hookups": "4", "in_building": "2", "on_site": "3",
                                        "none": "5"})
    parking = CommaListField(value_map={"carport": "1", "attached_garage": "2", "detached_garage": "3",
                                        "off_street": "4", "street": "5", "valet": "6", "none": "7"})
    rent_period = CommaListField(value_map={"daily": "1", "weekly": "2", "monthly": "3"})
    availability = ChoiceField({"within_30_days": "1", "beyond_30_days": "2"})
    open_house_date = DateField()
    cats_allowed = Flag()
    dogs_allowed = Flag()
    is_furnished = Flag()
    no_smoking = Flag()
    wheelchair_accessible = Flag()
    air_conditioning = Flag()
    ev_charging = Flag()
    private_room = Flag()
    private_bath = Flag()
    no_broker_fee = Flag()
    no_application_fee = Flag()
    show_duplicates = Flag()


class JobSearchSchema(_SearchBase):
    category = CategoryField(section="jobs")
    employment_type = CommaListField(value_map={"full_time": "1", "part_time": "2", "contract": "3",
                                                "employees_choice": "4"})
    is_remote = Flag()
    is_internship = Flag()
    is_nonprofit = Flag()


class GigSearchSchema(_SearchBase):
    category = CategoryField(section="gigs")
    is_paid = Flag()


class ServiceSearchSchema(_SearchBase):
    category = CategoryField(section="services")


class CommunitySearchSchema(_SearchBase):
    category = CategoryField(section="community")
    lost_or_found = ChoiceField({"lost": "1", "found": "2"})


class EventSearchSchema(_SearchBase):
    category = CategoryField(section="events")
    date = DateField()
    event_type = CommaListField(value_map={"art": "event_art", "athletics": "event_athletics",
                                           "career": "event_career", "dance": "event_dance",
                                           "festival": "event_festival",
                                           "fitness_wellness": "event_fitness_wellness",
                                           "food_drink": "event_food", "free": "event_free",
                                           "charitable": "event_fundraiser_vol", "tech": "event_geek",
                                           "kid_friendly": "event_kidfriendly", "literary": "event_literary",
                                           "music": "event_music", "outdoor": "event_outdoor",
                                           "sale": "event_sale", "singles": "event_singles",
                                           "sustainability": "event_sustainability"}, max_items=17)


class ResumeSearchSchema(_SearchBase):
    education_level = CommaListField(value_map={"less_than_high_school": "1", "high_school": "2",
                                                "some_college": "3", "associates": "4", "bachelors": "5",
                                                "masters": "6", "doctoral": "7"})
    availability = CommaListField(value_map={"mornings": "resumes_available_morning",
                                             "afternoons": "resumes_available_afternoon",
                                             "evenings": "resumes_available_evening",
                                             "overnight": "resumes_available_overnight",
                                             "weekdays": "resumes_available_weekdays",
                                             "weekends": "resumes_available_weekends"})


# ---- search helpers ------------------------------------------------------------------

class FiltersSchema(BaseSchema):
    category = CategoryField(load_default="sss")
    location = LocationField(required=False, load_default=None)


class SuggestionsSchema(BaseSchema):
    query = QueryField(max_length=100)
    type = ChoiceField({"search": "search", "make_model": "makemodel"}, load_default="search")
    category = CategoryField()
    location = LocationField(required=False, load_default=None)

    @validates_schema
    def _site_only(self, data, **kwargs):
        location = data.get("location")
        if location and location.get("kind") != "area":
            raise ValidationError("location must be a Craigslist site (newyork) for suggestions.", "location")
        if (data.get("category") or data.get("type") == "makemodel") and not location:
            raise ValidationError("location is required with category or type=make_model.", "location")


class CountsSchema(BaseSchema):
    query = QueryField(max_length=180)
    location = LocationField()
    subarea = SubareaField()
    radius = PositiveInt(max_value=1000)

    @validates_schema
    def _geo(self, data, **kwargs):
        location = data.get("location") or {}
        if location.get("kind") == "area" and data.get("radius") is not None:
            raise ValidationError("radius needs a postal code or latitude,longitude in location.", "radius")
        if location.get("kind") != "area" and data.get("subarea"):
            raise ValidationError("subarea needs a Craigslist site in location.", "subarea")


# ---- listings ------------------------------------------------------------------------

class ListingSchema(BaseSchema):
    listing = ListingField()


class ListingBatchSchema(BaseSchema):
    listings = ListingListField()


# ---- locations / categories ----------------------------------------------------------

class LocationsSchema(BaseSchema):
    country = StrippedString(required=False, load_default=None)
    region = StrippedString(required=False, load_default=None)

    @post_load
    def _upper(self, data, **kwargs):
        for key in ("country", "region"):
            if data.get(key):
                data[key] = data[key].upper()
        return data


class LocationSearchSchema(BaseSchema):
    query = QueryField(max_length=60)
    limit = LimitField(default=10, max_size=50)


class NearbyLocationsSchema(BaseSchema):
    location = LocationField()
    limit = LimitField(default=10, max_size=50)

    @validates_schema
    def _coordinates(self, data, **kwargs):
        if (data.get("location") or {}).get("kind") == "postal":
            raise ValidationError("location must be latitude,longitude (40.7128,-74.0060) or a Craigslist site.",
                                  "location")


class CategoriesSchema(BaseSchema):
    section = ChoiceField(["for_sale", "housing", "jobs", "gigs", "services", "community", "events", "resumes"])
