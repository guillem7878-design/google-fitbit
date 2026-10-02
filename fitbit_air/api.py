"""Cliente mínimo de la Google Health API v4."""

import time
from datetime import date
from typing import Iterator

import requests

from . import auth, config

MAX_RETRIES = 3


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status


class HealthClient:
    def __init__(self, token_fn=auth.access_token):
        self._token_fn = token_fn
        self._session = requests.Session()

    def _request(self, method: str, path: str, *, params=None, json=None) -> dict:
        refreshed = False
        last = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                time.sleep(2 ** (attempt - 1))
            r = self._session.request(
                method,
                config.API_BASE + path,
                params=params,
                json=json,
                headers={"Authorization": "Bearer " + self._token_fn(force_refresh=refreshed)},
                timeout=60,
            )
            if r.status_code < 300:
                return r.json() if r.content else {}
            last = ApiError(r.status_code, r.text)
            if r.status_code in (401, 403) and not refreshed:
                # Token caducado en el servidor antes que en local: renovar una vez.
                refreshed = True
                continue
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = r.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    time.sleep(int(retry_after))
                continue
            raise last
        raise last

    def list_points(self, data_type: str, filter_expr: str, page_size: int) -> Iterator[dict]:
        params = {"filter": filter_expr, "pageSize": page_size}
        while True:
            body = self._request("GET", f"/users/me/dataTypes/{data_type}/dataPoints", params=params)
            yield from body.get("dataPoints", [])
            token = body.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token

    def daily_rollup(self, data_type: str, start: date, end_exclusive: date) -> Iterator[dict]:
        def civil(d: date) -> dict:
            return {"date": {"year": d.year, "month": d.month, "day": d.day}}

        payload = {"range": {"start": civil(start), "end": civil(end_exclusive)}, "windowSizeDays": 1}
        while True:
            body = self._request(
                "POST", f"/users/me/dataTypes/{data_type}/dataPoints:dailyRollUp", json=payload
            )
            yield from body.get("rollupDataPoints", [])
            token = body.get("nextPageToken")
            if not token:
                return
            payload["pageToken"] = token
