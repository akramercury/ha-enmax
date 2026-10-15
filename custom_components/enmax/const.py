"""Constants for the Enmax integration."""
from datetime import date, timedelta

DOMAIN = "enmax"
CONF_ACCOUNT_NO = "account_no"
CONF_BP = "bp"
UPDATE_INTERVAL = timedelta(hours=6)
HISTORY_DAYS = 730  # regular refresh window (what MyAccount itself requests)
FULL_HISTORY_START = date(2010, 1, 1)  # first import asks Enmax for everything it has
CONF_STATS_VERSION = "stats_version"
STATS_VERSION = 2  # bump to force a full re-import of statistics

WASTE_KEYS = ("black_cart_program", "blue_cart_program", "green_cart_program", "residential_garbage")
