from __future__ import annotations

import os
from typing import Any

import requests


ODSAY_PREVIEW_URL = "https://api.odsay.com/v1/api/searchPubTransPathT"


def _classify_exception(exc: Exception) -> dict[str, Any]:
    winerror = getattr(exc, "winerror", None)
    if winerror == 10013:
        return {
            "status": "ENV_NETWORK_BLOCKED",
            "provider": "odsay",
            "response_received": False,
            "http_status": None,
            "exception_class": exc.__class__.__name__,
            "exception_message": str(exc),
            "winerror_10013": True,
        }
    return {
        "status": "REQUEST_EXCEPTION",
        "provider": "odsay",
        "response_received": False,
        "http_status": None,
        "exception_class": exc.__class__.__name__,
        "exception_message": str(exc),
        "winerror_10013": False,
    }


def run_preflight() -> dict[str, Any]:
    api_key = (os.getenv("ODSAY_API_KEY") or "").strip()
    if not api_key:
        return {
            "status": "MISSING_API_KEY",
            "provider": "odsay",
            "response_received": False,
            "http_status": None,
            "exception_class": None,
            "exception_message": "ODSAY_API_KEY is not set",
            "winerror_10013": False,
        }

    params = {
        "SX": "126.9780",
        "SY": "37.5665",
        "EX": "126.98955",
        "EY": "37.56585",
        "apiKey": api_key,
    }

    try:
        response = requests.get(ODSAY_PREVIEW_URL, params=params, timeout=5)
        http_status = response.status_code
        if http_status in {403, 429}:
            return {
                "status": "HTTP_REJECTED",
                "provider": "odsay",
                "response_received": True,
                "http_status": http_status,
                "exception_class": None,
                "exception_message": None,
                "winerror_10013": False,
            }
        if 200 <= http_status < 300:
            return {
                "status": "OK",
                "provider": "odsay",
                "response_received": True,
                "http_status": http_status,
                "exception_class": None,
                "exception_message": None,
                "winerror_10013": False,
            }
        return {
            "status": "REQUEST_EXCEPTION",
            "provider": "odsay",
            "response_received": True,
            "http_status": http_status,
            "exception_class": None,
            "exception_message": response.text[:200] if getattr(response, "text", None) else None,
            "winerror_10013": False,
        }
    except Exception as exc:
        return _classify_exception(exc)
