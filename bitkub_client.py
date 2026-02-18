"""
Shared Bitkub API client.

Used by portfolio_balance.py and crypto_dca.py to avoid duplicating
HMAC signing, server-time sync, and FX-rate fetching logic.
"""
import hashlib
import hmac
import json
import os
import time
from typing import Optional
import requests

API_KEY = os.environ.get("BITKUB_API_KEY")
API_SECRET = os.environ.get("BITKUB_API_SECRET")
BASE_URL = "https://api.bitkub.com"


# ---------------------------------------------------------------------------
# Server time
# ---------------------------------------------------------------------------

def get_server_time() -> int:
    """Return Bitkub server timestamp (seconds). Falls back to local time."""
    try:
        r = requests.get(f"{BASE_URL}/api/v3/servertime", timeout=5)
        return int(r.text)
    except Exception:
        return int(time.time())


# ---------------------------------------------------------------------------
# Authenticated request
# ---------------------------------------------------------------------------

def bitkub_request(method: str, endpoint: str, payload: Optional[dict] = None, params: Optional[dict] = None):
    """
    Make an authenticated request to the Bitkub API.

    Supports GET (with optional query-string params) and POST (with JSON body).
    Returns the parsed JSON response dict, or raises on network errors.
    On HTTP errors it tries to return the error JSON so callers can inspect
    the Bitkub error code; if that also fails the original HTTPError is re-raised.
    """
    if not API_KEY or not API_SECRET:
        raise ValueError("Missing BITKUB_API_KEY or BITKUB_API_SECRET")

    ts = str(get_server_time())

    # Build the path used in the HMAC signature
    if method == "GET" and params:
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        sig_path = f"{endpoint}?{query_string}"
    else:
        sig_path = endpoint

    payload_str = json.dumps(payload, separators=(",", ":")) if payload else ""
    sig_message = f"{ts}{method}{sig_path}{payload_str}"

    signature = hmac.new(
        API_SECRET.encode("utf-8"),
        sig_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-BTK-APIKEY": API_KEY,
        "X-BTK-TIMESTAMP": ts,
        "X-BTK-SIGN": signature,
    }

    url = BASE_URL + sig_path
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        else:
            response = requests.request(method, url, headers=headers, data=payload_str, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as exc:
        try:
            return response.json()  # Return error dict so callers can check error code
        except Exception:
            raise exc


# ---------------------------------------------------------------------------
# FX rates
# ---------------------------------------------------------------------------

def get_thb_usd_rate() -> float:
    """
    Return the current THB → USD exchange rate.

    Tries two public APIs in order; returns 0.0 and prints an error if both fail.
    """
    # Primary: Frankfurter
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=THB&to=USD", timeout=5)
        data = r.json()
        if "rates" in data and "USD" in data["rates"]:
            return float(data["rates"]["USD"])
    except Exception as exc:
        print(f"Primary FX source failed: {exc}")

    # Secondary: Open Exchange Rate
    try:
        r = requests.get("https://open.er-api.com/v6/latest/THB", timeout=5)
        data = r.json()
        if "rates" in data and "USD" in data["rates"]:
            return float(data["rates"]["USD"])
    except Exception as exc:
        print(f"Secondary FX source failed: {exc}")

    print("❌ ERROR: All FX rate sources failed. USD values will be unavailable.")
    return 0.0


def get_historical_thb_usd_rate(date_str: str) -> float:
    """
    Return the THB → USD rate for *date_str* (YYYY-MM-DD).

    Falls back to the current rate when historical data is unavailable.
    """
    try:
        r = requests.get(
            f"https://api.frankfurter.app/{date_str}?from=THB&to=USD", timeout=5
        )
        data = r.json()
        if "rates" in data and "USD" in data["rates"]:
            return float(data["rates"]["USD"])
    except Exception as exc:
        print(f"Historical FX rate failed for {date_str}: {exc}")

    return get_thb_usd_rate()
