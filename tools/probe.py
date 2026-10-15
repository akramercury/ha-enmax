#!/usr/bin/env python3
"""
Test the Enmax API client from your PC.

    python -m pip install aiohttp
    python probe.py

Asks for your Enmax email + password (password is not echoed or saved).
Writes raw responses to .\\out\\ (stays on your PC) and a scrubbed
out\\share_me.json that is safe to send back.
"""
import asyncio
import getpass
import json
import re
from pathlib import Path

import aiohttp

from enmax_api import EnmaxClient

OUT = Path(__file__).with_name("out")


def scrub(o):
    """Keep structure + numbers/dates, drop anything identifying."""
    pii = re.compile(r"(account|ba$|bp|address|name|email|phone|premise|site_id|contract|doc_id|file_name|postal|street|house|uuid|ref|guid|token)", re.I)
    if isinstance(o, dict):
        return {k: ("XXXX" if pii.search(k) and not isinstance(v, (dict, list, bool, type(None))) else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(v) for v in o]
    return o


def g(d, *path):
    for p in path:
        d = (d or {}).get(p)
    return d


async def main():
    user = input("Enmax email: ").strip()
    pw = getpass.getpass("Enmax password (hidden): ")
    OUT.mkdir(exist_ok=True)

    async with aiohttp.ClientSession() as s:
        c = EnmaxClient(s, user, pw)
        print("\n[1] Logging in ...", end=" ")
        await c.login()
        print("OK")

        print("[2] Accounts ...", end=" ")
        accts = await c.associated_accounts()
        a = next((x for x in accts if x.get("is_default")), accts[0])
        acct, bp = a["account_no"], a["bp_id"]
        print(f"{len(accts)} found, using ...{acct[-4:]}  municipal services: {a.get('has_municipal_services')}")

        results = {}
        for name, coro in [
            ("balance", c.balance(acct, bp)),
            ("plans", c.plans(acct)),
            ("energy_insights", c.energy_insights(acct)),
            ("invoices", c.invoices(acct)),
        ]:
            print(f"[3] {name} ...", end=" ")
            try:
                results[name] = await coro
                n = len(results[name]) if isinstance(results[name], list) else "ok"
                print(n)
            except Exception as e:  # keep going so we learn as much as possible
                results[name] = {"error": str(e)}
                print("FAILED:", e)
            (OUT / f"{name}.json").write_text(json.dumps(results[name], indent=2), encoding="utf-8")

        bills = results.get("invoices")
        if isinstance(bills, list):
            real = sorted([b for b in bills if b.get("doc_ind") == "I" and b.get("doc_id")],
                          key=lambda b: b.get("bill_date") or "", reverse=True)
            if real:
                b = real[0]
                print(f"[3] PDF for bill {(b.get('bill_date') or '')[:10]} ...", end=" ")
                try:
                    pdf = await c.invoice_pdf(acct, b["doc_id"])
                    (OUT / "latest_bill.pdf").write_bytes(pdf)
                    print(f"{len(pdf)//1024} KB -> out\\latest_bill.pdf")
                    results["pdf_ok"] = pdf[:4] == b"%PDF"
                except Exception as e:
                    print("FAILED:", e)
                    results["pdf_ok"] = str(e)

        print("[4] Refresh token ...", end=" ")
        try:
            c._expires_at = 0
            await c.refresh()
            print("OK")
            results["refresh_ok"] = True
        except Exception as e:
            print("FAILED:", e)
            results["refresh_ok"] = str(e)

    ins = results.get("energy_insights")
    if isinstance(ins, list) and ins:
        print("\nBill date    Elec kWh   Elec $    Gas GJ   Gas $   Water m3  Water $  Wastew $  Storm $  Waste $")
        for r in sorted(ins, key=lambda r: r.get("bill_date") or ""):
            m = r.get("municipal") or {}
            waste = sum((g(m, k, "amount") or 0) for k in ("black_cart_program", "blue_cart_program", "green_cart_program", "residential_garbage"))
            print(f"{(r.get('bill_date') or '')[:10]:<12}"
                  f"{g(r,'electricity','usage') or 0:>8.0f} {g(r,'electricity','bill_amount') or 0:>8.2f}"
                  f"{g(r,'natural_gas','usage') or 0:>9.2f} {g(r,'natural_gas','bill_amount') or 0:>7.2f}"
                  f"{g(m,'water','usage') or 0:>10.1f} {g(m,'water','amount') or 0:>8.2f}"
                  f"{g(m,'wastewater','amount') or 0:>9.2f} {g(m,'stormwater_management','amount') or 0:>8.2f} {waste:>8.2f}")

    share = {k: scrub(v) for k, v in results.items() if k != "plans"}
    if isinstance(share.get("energy_insights"), list):
        share["energy_insights"] = share["energy_insights"][-3:]   # last 3 bills is plenty
    if isinstance(share.get("invoices"), list):
        share["invoices"] = share["invoices"][:3]
    (OUT / "share_me.json").write_text(json.dumps(share, indent=2), encoding="utf-8")
    print(f"\nDone. Send me: {OUT / 'share_me.json'}")


if __name__ == "__main__":
    asyncio.run(main())
