"""Marshmallow request schemas for every /kleinanzeigen/* route.

Generic fields come from the shared top-level schema_fields.py; this module
adds the Kleinanzeigen resolvers and the per-route schemas. Every schema's
load() output is the kwargs dict its endpoint function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url` / `id` pair) — refs.py lists every accepted form:
  query      keywords | a kleinanzeigen.de search link
  listing    listing id | listing link
  seller     user id | seller link (…/s-bestandsliste.html?userId=…)
  store      store id | store slug | …/pro/<slug> link
  category   category id | category name | category / search link
  location   city / district name | postal code | id:<location id> | search link

Pagination: `page` (1-based) + `page_size` (1-100) on every listing list;
responses carry count / total_pages / next. Kleinanzeigen serves the first
10,000 results of a search (page x page_size <= 10000).
"""
from marshmallow import ValidationError, fields, validate, validates_schema

from schema_fields import (BaseSchema, ChoiceField, CommaListField, Flag, LimitField, NonNegativeNumber,
                           PageField, PageSizeField, PositiveInt, Price, QueryField, RefField,
                           StrippedString)
from kleinanzeigen import refs

# Choice tables as literals; the publishing tooling reads enums from this file's AST.
SORTS = ["newest", "price_asc", "price_desc", "distance"]
INVENTORY_SORTS = ["newest", "price_asc", "price_desc"]
SELLER_TYPES = ["private", "commercial"]
AD_TYPES = ["offer", "wanted"]
CAR_FUELS = {"petrol": "benzin", "diesel": "diesel", "cng": "cng", "lpg": "lpg", "hybrid": "hybrid",
             "electric": "elektro", "other": "andere"}
CAR_TRANSMISSIONS = {"automatic": "automatik", "manual": "manuell"}
CAR_BODY_TYPES = {"small_car": "kleinwagen", "sedan": "limousine", "estate": "kombi",
                  "convertible": "cabrio", "suv": "suv", "van": "bus", "coupe": "coupe", "other": "andere"}
CAR_COLORS = {"beige": "beige", "blue": "blau", "brown": "braun", "yellow": "gelb", "gold": "gold",
              "grey": "grau", "green": "grün", "orange": "orange", "red": "rot", "black": "schwarz",
              "silver": "silber", "purple": "violet", "white": "weiß", "other": "sonstige"}
CAR_CONDITIONS = {"undamaged": "nein", "damaged": "ja"}
REAL_ESTATE_TYPES = ["apartment_rent", "apartment_buy", "house_rent", "house_buy"]
REAL_ESTATE_FEATURES = ["furnished", "balcony", "terrace", "built_in_kitchen", "bathtub", "guest_wc",
                        "barrier_free", "floor_heating", "old_building", "new_building", "lift",
                        "celler_loft", "garage", "garden", "pets_allowed", "wg_possible", "wbs",
                        "grannyflat", "historical", "rented"]
JOB_FIELDS = {"apprenticeship": "118", "construction_trades": "111", "office_admin": "114",
              "hospitality_tourism": "110", "customer_service": "105", "mini_side_jobs": "107",
              "internships": "125", "social_care": "123", "transport_logistics": "247",
              "sales": "117", "other": "109"}

PAGE_SIZE_MAX = 100
WINDOW = 10000
BATCH_MAX = 20
VIEWS_MAX = 50


# ---- ref fields -------------------------------------------------------------------------

class ListingRefField(RefField):
    resolver = staticmethod(refs.resolve_listing)


class SellerRefField(RefField):
    resolver = staticmethod(refs.resolve_seller)


class StoreRefField(RefField):
    resolver = staticmethod(refs.resolve_store)


class CategoryRefField(RefField):
    resolver = staticmethod(refs.resolve_category)


class LocationRefField(RefField):
    resolver = staticmethod(refs.resolve_location)


class QueryOrLinkField(RefField):
    """Keywords, or a kleinanzeigen.de search link (-> the params it encodes)."""
    resolver = staticmethod(refs.resolve_query)

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, str) and len(value) > 600:
            raise ValidationError("Longer than 600 characters.")
        return super()._deserialize(value, attr, data, **kwargs)


class FiltersField(RefField):
    """'autos.marke:bmw,autos.ez:2015-2020' -> {'autos.marke': 'bmw', 'autos.ez': '2015,2020'}."""
    resolver = staticmethod(refs.parse_filters)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


class ListingListField(fields.Field):
    """Comma-separated listing ids / links -> [ids], deduped, order kept."""

    def __init__(self, max_items, **kwargs):
        kwargs.setdefault("required", True)
        super().__init__(**kwargs)
        self.max_items = max_items

    def _deserialize(self, value, attr, data, **kwargs):
        ids = []
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                listing_id = refs.resolve_listing(part)
            except ValueError as e:
                raise ValidationError(f"'{part}': {e}")
            if listing_id not in ids:
                ids.append(listing_id)
        if not ids:
            raise ValidationError("Must not be empty.")
        if len(ids) > self.max_items:
            raise ValidationError(f"At most {self.max_items} listings per call.")
        return ids


class TextField(StrippedString):
    """Optional short free-text value (a car make, a model, a property type)."""

    def __init__(self, max_length=60, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        kwargs.setdefault("validate", validate.Length(max=max_length))
        super().__init__(**kwargs)


def _optional_query():
    return QueryField(required=False, load_default=None)


def _window(data):
    if data.get("page") and data.get("page_size") and data["page"] * data["page_size"] > WINDOW:
        raise ValidationError(f"Kleinanzeigen serves the first {WINDOW} results of a search: "
                              f"page x page_size must stay at or below {WINDOW}.", "page")


class PagedSchema(BaseSchema):
    page = PageField(max_page=WINDOW)
    page_size = PageSizeField(default=25, max_size=PAGE_SIZE_MAX)

    @validates_schema
    def _check_window(self, data, **kwargs):
        _window(data)


# ---- search -----------------------------------------------------------------------------

class SearchSchema(BaseSchema):
    query = QueryOrLinkField(required=False, load_default=None)
    category = CategoryRefField(required=False, load_default=None)
    location = LocationRefField(required=False, load_default=None)
    radius = NonNegativeNumber(validate=validate.Range(min=0, max=500))
    latitude = fields.Float(load_default=None, validate=validate.Range(min=-90, max=90))
    longitude = fields.Float(load_default=None, validate=validate.Range(min=-180, max=180))
    min_price = Price()
    max_price = Price()
    free_only = Flag()
    sort = ChoiceField(SORTS)
    seller_type = ChoiceField(SELLER_TYPES)
    ad_type = ChoiceField(AD_TYPES)
    has_shipping = Flag()
    has_photos = Flag()
    buy_now_only = Flag()
    include_top_ads = Flag()
    filters = FiltersField()
    # no default: a search link's own page applies unless `page` is passed
    page = PositiveInt(max_value=WINDOW)
    page_size = PageSizeField(default=25, max_size=PAGE_SIZE_MAX)

    @validates_schema
    def _check(self, data, **kwargs):
        _window(data)
        if (data.get("latitude") is None) != (data.get("longitude") is None):
            raise ValidationError("Pass latitude and longitude together.", "latitude")
        if data.get("location") and data.get("latitude") is not None:
            raise ValidationError("Pass either location or latitude + longitude.", "location")


class SuggestionsSchema(BaseSchema):
    query = QueryField(max_length=100)
    limit = LimitField(default=10, max_size=20)


# ---- listings ---------------------------------------------------------------------------

class ListingSchema(BaseSchema):
    listing = ListingRefField()


class ListingBatchSchema(BaseSchema):
    listings = ListingListField(BATCH_MAX)


class ListingViewsSchema(BaseSchema):
    listings = ListingListField(VIEWS_MAX)


# ---- sellers / stores -------------------------------------------------------------------

class SellerSchema(BaseSchema):
    seller = SellerRefField()


class InventorySchema(PagedSchema):
    query = _optional_query()
    category = CategoryRefField(required=False, load_default=None)
    min_price = Price()
    max_price = Price()
    sort = ChoiceField(INVENTORY_SORTS, load_default="newest")


class SellerListingsSchema(InventorySchema):
    seller = SellerRefField()


class StoreSchema(BaseSchema):
    store = StoreRefField()


class StoreListingsSchema(InventorySchema):
    store = StoreRefField()


# ---- reference data ---------------------------------------------------------------------

class CategoriesSchema(BaseSchema):
    category = CategoryRefField(required=False, load_default=None)


class CategoryFiltersSchema(BaseSchema):
    category = CategoryRefField()


class LocationSearchSchema(BaseSchema):
    query = QueryField(max_length=100)
    limit = LimitField(default=20, max_size=50)


class LocationSchema(BaseSchema):
    location = LocationRefField()


class CoordinatesSchema(BaseSchema):
    latitude = fields.Float(required=True, validate=validate.Range(min=-90, max=90))
    longitude = fields.Float(required=True, validate=validate.Range(min=-180, max=180))


class EmptySchema(BaseSchema):
    pass


# ---- verticals --------------------------------------------------------------------------

class VerticalSchema(PagedSchema):
    query = _optional_query()
    location = LocationRefField(required=False, load_default=None)
    radius = NonNegativeNumber(validate=validate.Range(min=0, max=500))
    seller_type = ChoiceField(SELLER_TYPES)
    sort = ChoiceField(SORTS, load_default="newest")

    @validates_schema
    def _check_place(self, data, **kwargs):
        if not data.get("location"):
            if data.get("radius") is not None:
                raise ValidationError("radius needs a location.", "radius")
            if data.get("sort") == "distance":
                raise ValidationError("sort=distance needs a location.", "sort")


class CarsSchema(VerticalSchema):
    make = TextField()
    model = TextField()
    fuel = ChoiceField(CAR_FUELS)
    transmission = ChoiceField(CAR_TRANSMISSIONS)
    body_type = ChoiceField(CAR_BODY_TYPES)
    color = ChoiceField(CAR_COLORS)
    condition = ChoiceField(CAR_CONDITIONS)
    min_year = PositiveInt(validate=validate.Range(min=1910, max=2100))
    max_year = PositiveInt(validate=validate.Range(min=1910, max=2100))
    min_mileage = fields.Integer(load_default=None, strict=False, validate=validate.Range(min=0, max=2000000))
    max_mileage = fields.Integer(load_default=None, strict=False, validate=validate.Range(min=0, max=2000000))
    min_power = fields.Integer(load_default=None, strict=False, validate=validate.Range(min=0, max=20000))
    max_power = fields.Integer(load_default=None, strict=False, validate=validate.Range(min=0, max=20000))
    min_price = Price()
    max_price = Price()
    has_photos = Flag()
    filters = FiltersField()


class RealEstateSchema(VerticalSchema):
    type = ChoiceField(REAL_ESTATE_TYPES, load_default="apartment_rent")
    min_price = Price()
    max_price = Price()
    min_rooms = NonNegativeNumber()
    max_rooms = NonNegativeNumber()
    min_area = NonNegativeNumber()
    max_area = NonNegativeNumber()
    min_plot_area = NonNegativeNumber()
    max_plot_area = NonNegativeNumber()
    min_year_built = PositiveInt(max_value=2100)
    max_year_built = PositiveInt(max_value=2100)
    property_type = TextField()
    features = CommaListField(allowed=REAL_ESTATE_FEATURES, upper=False, max_items=10)
    has_photos = Flag()
    filters = FiltersField()


class JobsSchema(VerticalSchema):
    field = ChoiceField(JOB_FIELDS)
    ad_type = ChoiceField(AD_TYPES)
