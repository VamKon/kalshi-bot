"""
Shared helpers for Streamlit pages — API calls + formatting.
"""
import os
from typing import Any, Optional

import httpx
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
API_BASE = f"{BACKEND_URL}/api/v1"


@st.cache_data(ttl=60)
def fetch(path: str, params: Optional[dict] = None) -> Any:
    """GET from the backend API with 60-second Streamlit cache."""
    try:
        resp = httpx.get(f"{API_BASE}{path}", params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        st.error(f"API error ({path}): {exc}")
        return None


def post(path: str, params: Optional[dict] = None, json: Optional[dict] = None) -> Any:
    """POST to the backend API (not cached)."""
    try:
        resp = httpx.post(f"{API_BASE}{path}", params=params, json=json, timeout=30.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        st.error(f"API error (POST {path}): {exc}")
        return None


def patch(path: str, json: dict) -> Any:
    """PATCH to the backend API."""
    try:
        resp = httpx.patch(f"{API_BASE}{path}", json=json, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        st.error(f"API error (PATCH {path}): {exc}")
        return None


def put(path: str, json: dict) -> Any:
    """PUT to the backend API."""
    try:
        resp = httpx.put(f"{API_BASE}{path}", json=json, timeout=10.0)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        st.error(f"API error (PUT {path}): {exc}")
        return None


def pnl_color(value: float) -> str:
    """Return green or red depending on P&L sign."""
    return "green" if value >= 0 else "red"


def fmt_usd(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:,.2f}"
