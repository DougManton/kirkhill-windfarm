from datetime import timedelta

DOMAIN = "kirkhill_windfarm"

CONF_API_TOKEN = "api_token"
CONF_INCOME_RATES = "income_rates"
CONF_EFFECTIVE_FROM = "effective_from"
CONF_RATE_PER_KWH = "rate_per_kwh"

API_BASE_URL = "https://dashboard.kirkhillcoop.org"
API_TIMEOUT = 30

SCAN_INTERVAL = timedelta(minutes=5)

PLATFORMS = ["sensor"]
