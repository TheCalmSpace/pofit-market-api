import csv
import io
import json
import logging
import os
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

NSE_HOME = "https://www.nseindia.com"
NSE_API_BASE = "https://www.nseindia.com/api"
NSE_NEXT_API = f"{NSE_API_BASE}/NextApi/apiClient/GetQuoteApi"
NSE_SEC_LIST_URL = "https://nsearchives.nseindia.com/content/equities/sec_list.csv"
NSE_ACTIVE_SEC_URL = "https://nsearchives.nseindia.com/content/equities/List_of_Active_Securities_CM_DEBT.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

RATE_LIMIT_RPS = 3
RETRY_TOTAL = 3
RETRY_BACKOFF_FACTOR = 1.0
REQUEST_TIMEOUT = 15


class NSError(Exception):
    pass


class NSERateLimitError(NSError):
    pass


class NSEClient:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        retry = Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._last_request_time = 0.0

    def _rate_limit(self) -> None:
        min_interval = 1.0 / RATE_LIMIT_RPS
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    def _init_cookies(self) -> None:
        try:
            resp = self._session.get(NSE_HOME, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to initialize NSE cookies: %s", exc)

    def _request(self, url: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        self._rate_limit()
        try:
            resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                raise NSERateLimitError("NSE rate limit hit")
            resp.raise_for_status()
            return resp.json()
        except NSERateLimitError:
            raise
        except requests.exceptions.Timeout as exc:
            raise NSError(f"NSE request timeout: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise NSError(f"NSE request failed: {exc}") from exc

    def fetch_sec_list(self) -> List[Dict[str, str]]:
        logger.info("Downloading NSE securities list")
        try:
            resp = self._session.get(NSE_SEC_LIST_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            raise NSError(f"Failed to download sec_list.csv: {exc}") from exc

        content = resp.content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
        logger.info("Downloaded %d securities from sec_list.csv", len(rows))
        return rows

    def equity_meta_info(self, symbol: str) -> Dict[str, Any]:
        url = f"{NSE_NEXT_API}"
        params = {"functionName": "getMetaData", "symbol": symbol.upper()}
        try:
            data = self._request(url, params=params)
        except NSError as exc:
            logger.warning("equityMetaInfo failed for %s: %s", symbol, exc)
            return {"symbol": symbol.upper(), "error": str(exc)}
        return data

    def fetch_active_securities_isin(self) -> Dict[str, str]:
        logger.info("Downloading NSE active securities ISIN map")
        try:
            resp = self._session.get(NSE_ACTIVE_SEC_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("Failed to download active securities CSV: %s", exc)
            return {}

        content = resp.content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))
        isin_map: Dict[str, str] = {}
        for row in reader:
            name = (row.get("Company Name") or "").strip().upper()
            isin = (row.get("ISIN") or "").strip()
            inst_type = (row.get("Instrument Type") or "").strip()
            if name and isin and inst_type == "Equity":
                isin_map[name] = isin
        logger.info("Loaded %d ISIN mappings from active securities", len(isin_map))
        return isin_map

    @staticmethod
    def normalize_series(series: str) -> str:
        mapping = {
            "EQ": "EQ",
            "BE": "BE",
            "BZ": "BZ",
            "SM": "SM",
            "ST": "ST",
            "SZ": "SZ",
            "IT": "IT",
            "IV": "IV",
        }
        return mapping.get(series.upper(), series.upper())

    @staticmethod
    def is_traded_series(series: str) -> bool:
        return series.upper() in {"EQ", "BE", "BZ", "SM", "ST", "SZ", "IT", "IV"}

    @staticmethod
    def normalize_company_name(name: str) -> str:
        return " ".join(name.upper().split())

    @staticmethod
    def parse_meta_status(meta: Dict[str, Any]) -> str:
        if meta.get("isDelisted") == "true":
            return "DELISTED"
        if meta.get("isSuspended") == "true":
            return "SUSPENDED"
        if meta.get("error"):
            return "UNKNOWN"
        return "LISTED"

    @staticmethod
    def map_series_to_exchange(series: str) -> str:
        return "NSE"

    @staticmethod
    def infer_sector_industry(meta: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        sector = meta.get("sector")
        industry = meta.get("industry")
        if isinstance(sector, str) and sector.strip():
            sector = sector.strip()
        else:
            sector = None
        if isinstance(industry, str) and industry.strip():
            industry = industry.strip()
        else:
            industry = None
        return sector, industry
