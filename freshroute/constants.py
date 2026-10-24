"""Shared constants for FreshRoute AI."""

APP_NAME = "FreshRoute AI"
TAGLINE = "Smarter Meals. Less Waste."

SOURCES = ("demo", "upload", "manual")
SOURCE_LABELS = {
    "demo": "Synthetic demo",
    "upload": "CSV upload",
    "manual": "Manual entry",
}

# Data scopes control which record sources feed the dashboard, analytics and models.
SCOPES = ("all", "demo", "mine")
SCOPE_LABELS = {
    "all": "All records (demo + yours)",
    "demo": "Synthetic demo data only",
    "mine": "My data only (uploads + manual)",
}
SCOPE_SOURCES = {"all": None, "demo": ["demo"], "mine": ["upload", "manual"]}

REQUIRED_COLUMNS = ["date", "meal", "food_item", "quantity_prepared", "quantity_consumed"]
OPTIONAL_COLUMNS = ["unit", "student_count", "leftovers", "redistributed", "category", "notes"]
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS

COLUMN_SYNONYMS = {
    "item": "food_item",
    "food": "food_item",
    "food_name": "food_item",
    "dish": "food_item",
    "prepared": "quantity_prepared",
    "qty_prepared": "quantity_prepared",
    "consumed": "quantity_consumed",
    "qty_consumed": "quantity_consumed",
    "served": "quantity_consumed",
    "students": "student_count",
    "attendance": "student_count",
    "leftover": "leftovers",
    "quantity_left": "leftovers",
    "donated": "redistributed",
}

UNIT_SYNONYMS = {
    "serving": "servings",
    "portion": "servings",
    "portions": "servings",
    "plate": "plates",
    "piece": "pieces",
    "pc": "pieces",
    "pcs": "pieces",
    "litre": "litres",
    "liter": "litres",
    "liters": "litres",
    "l": "litres",
    "kgs": "kg",
    "kilogram": "kg",
    "kilograms": "kg",
}

MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_CSV_ROWS = 20000
PREVIEW_ROWS = 50
MAX_REPORTED_ERRORS = 100

MAX_ITEM_NAME_LEN = 60
MAX_NOTES_LEN = 300
MAX_QUANTITY = 1_000_000

DEFAULT_SETTINGS = {
    "cafeteria_name": "My College Cafeteria",
    "meals": ["Breakfast", "Lunch", "Snacks", "Dinner"],
    "units": ["servings", "kg", "pieces", "litres", "plates"],
    "default_unit": "servings",
    "safety_buffer_pct": 5.0,
    "min_prep_default": 0.0,
    "max_reduction_pct": 25.0,
    "acceptable_waste_pct": 5.0,
    "high_waste_threshold_pct": 15.0,
    "active_scope": "all",
    "demo_seeded": False,
}

NAV_ITEMS = [
    ("dashboard", "Dashboard", "/dashboard", "grid"),
    ("import", "Import data", "/import", "upload"),
    ("forecast", "Forecast", "/forecast", "spark"),
    ("plan", "Prep plan", "/plan", "clipboard"),
    ("time_machine", "Time Machine", "/time-machine", "clock"),
    ("records", "Waste records", "/records", "table"),
    ("analytics", "Analytics", "/analytics", "chart"),
    ("reports", "Reports", "/reports", "doc"),
    ("settings", "Settings", "/settings", "cog"),
]
