"""
Enmax MyAccount API client (reverse engineered from myaccount.enmax.com, Oct 2026).

Auth:  Microsoft Entra External ID "native authentication" (3 POSTs, no browser)
         POST {AUTH}/oauth2/v2.0/initiate   -> continuation_token
         POST {AUTH}/oauth2/v2.0/challenge  -> challenge_type=password, continuation_token
         POST {AUTH}/oauth2/v2.0/token      -> access_token (~1h), refresh_token
Data:  https://ciam.enmax.com/api/v1.3/...  with  Authorization: Bearer <access_token>

This module only depends on aiohttp so it can be reused as-is inside the
Home Assistant custom integration.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import aiohttp

TENANT = "4507be72-fb58-48dd-a5d6-db01565b8092"
CLIENT_ID = "81b6fef3-f5f6-4c82-9d79-122770617073"
AUTH_BASE = f"https://extauth.enmax.com/{TENANT}/oauth2/v2.0"
SCOPE = "openid offline_access api://301c0425-005b-4ddc-80da-c8d1901bdc5b/.default"
API_BASE = "https://ciam.enmax.com/api/v1.3"
INVOICE_URL = "https://invoices.enmax.com/api/invoice"
ORIGIN = "https://myaccount.enmax.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class EnmaxError(Exception):
    """Generic API error."""


class EnmaxAuthError(EnmaxError):
    """Bad credentials or login flow not supported (e.g. MFA required)."""


class EnmaxClient:
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._s = session
        self._username = username
        self._password = password
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self._expires_at = 0.0

    # ------------------------------------------------------------------ auth
    async def _auth_post(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "User-Agent": UA,
        }
        async with self._s.post(f"{AUTH_BASE}/{path}", data=data, headers=headers) as r:
            body = await r.json(content_type=None)
            if r.status >= 400:
                desc = body.get("error_description") or body.get("error") or str(body)
                if body.get("error") in ("invalid_grant", "user_not_found", "invalid_request"):
                    raise EnmaxAuthError(desc.splitlines()[0])
                raise EnmaxError(f"{path} HTTP {r.status}: {desc.splitlines()[0]}")
            return body

    async def login(self) -> None:
        init = await self._auth_post("initiate", {
            "client_id": CLIENT_ID,
            "username": self._username,
            "challenge_type": "password redirect",
        })
        if init.get("challenge_type") == "redirect":
            raise EnmaxAuthError("Enmax requires browser login for this account (redirect)")

        chal = await self._auth_post("challenge", {
            "client_id": CLIENT_ID,
            "continuation_token": init["continuation_token"],
            "challenge_type": "password redirect",
        })
        if chal.get("challenge_type") != "password":
            raise EnmaxAuthError(f"Unsupported challenge: {chal.get('challenge_type')}")

        tok = await self._auth_post("token", {
            "client_id": CLIENT_ID,
            "continuation_token": chal["continuation_token"],
            "grant_type": "password",
            "password": self._password,
            "scope": SCOPE,
        })
        self._store(tok)

    async def refresh(self) -> None:
        if not self.refresh_token:
            await self.login()
            return
        try:
            tok = await self._auth_post("token", {
                "client_id": CLIENT_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "scope": SCOPE,
            })
            self._store(tok)
        except EnmaxError:
            await self.login()

    def _store(self, tok: dict[str, Any]) -> None:
        if not tok.get("access_token"):
            raise EnmaxAuthError("No access_token in response")
        self.access_token = tok["access_token"]
        self.refresh_token = tok.get("refresh_token", self.refresh_token)
        self._expires_at = time.time() + int(tok.get("expires_in", 3600)) - 120

    async def ensure_token(self) -> None:
        if not self.access_token:
            await self.login()
        elif time.time() >= self._expires_at:
            await self.refresh()

    # ------------------------------------------------------------------ data
    async def _get(self, path: str, params: dict[str, str] | None = None, _retry: bool = True) -> dict[str, Any]:
        await self.ensure_token()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "User-Agent": UA,
        }
        async with self._s.get(f"{API_BASE}{path}", params=params, headers=headers) as r:
            if r.status == 401 and _retry:
                self._expires_at = 0
                return await self._get(path, params, _retry=False)
            body = await r.json(content_type=None)
            if r.status >= 400:
                raise EnmaxError(f"GET {path} HTTP {r.status}: {body.get('code')} {body.get('error_id')}")
            return body

    async def associated_accounts(self) -> list[dict[str, Any]]:
        d = await self._get("/associated/accounts")
        return [a["account"] for a in d["associated_account"]["accounts"]]

    async def plans(self, account_no: str) -> dict[str, Any]:
        return await self._get("/profile/account/plans", {"account_no": account_no})

    async def balance(self, account_no: str, bp: str) -> dict[str, Any]:
        return (await self._get("/profile/account/balance", {"account_no": account_no, "bp": bp}))["bill"]

    async def energy_insights(self, account_no: str, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
        """One record per bill: electricity / natural_gas / municipal (water, wastewater, waste carts, stormwater)."""
        end = end or date.today()
        start = start or end - timedelta(days=730)
        d = await self._get("/energy/insights", {
            "account_no": account_no,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        })
        return d.get("energy_insights") or []

    async def invoices(self, account_no: str) -> list[dict[str, Any]]:
        """Bill list. Real invoices have doc_ind == "I"; each has bill_date, bill_amount, doc_id, file_name."""
        d = await self._get("/invoice/list", {"ba": account_no})
        return [b for doc in d.get("docs") or [] for b in doc.get("bills") or []]

    async def invoice_pdf(self, account_no: str, doc_id: str) -> bytes:
        """Download one bill as PDF bytes."""
        await self.ensure_token()
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json, text/plain, */*",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "User-Agent": UA,
        }
        async with self._s.get(INVOICE_URL, params={"ba": account_no, "docId": doc_id}, headers=headers) as r:
            if r.status >= 400:
                raise EnmaxError(f"invoice PDF HTTP {r.status}")
            return await r.read()
