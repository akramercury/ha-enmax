"""Constants for the Enmax integration."""
from datetime import date, timedelta

DOMAIN = "enmax"
CONF_ACCOUNT_NO = "account_no"
CONF_BP = "bp"
UPDATE_INTERVAL = timedelta(hours=6)
HISTORY_DAYS = 730  # regular refresh window (what MyAccount itself requests)
CONF_STATS_VERSION = "stats_version"
STATS_VERSION = 3  # bump to force a full re-import of statistics

WASTE_KEYS = ("black_cart_program", "blue_cart_program", "green_cart_program", "residential_garbage")
