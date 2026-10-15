# Enmax (Calgary) for Home Assistant

Unofficial Home Assistant integration for **ENMAX MyAccount** in Calgary, Alberta. It brings your whole Enmax bill into Home Assistant: electricity, natural gas, and every City of Calgary charge on it (water, wastewater, stormwater, and black/blue/green cart programs).

- **Energy dashboard:** electricity (kWh), gas (GJ) and water (m³), each with costs.
- **Bill sensors:** balance, amount due, due date, last bill and payment, and each line of the latest bill.
- **Custom dashboard:** a ready-made "Enmax Bill" dashboard with monthly cost and usage history.

> Not affiliated with or endorsed by ENMAX. It uses the same API that myaccount.enmax.com uses.

## How it works

| What | Endpoint |
|---|---|
| Sign in | Microsoft Entra External ID native auth at `extauth.enmax.com` (initiate → challenge → token), using your MyAccount email and password |
| Usage and cost per bill | `GET ciam.enmax.com/api/v1.3/energy/insights` |
| Balance and due date | `GET ciam.enmax.com/api/v1.3/profile/account/balance` |
| Bill list / PDF | `GET ciam.enmax.com/api/v1.3/invoice/list`, `GET invoices.enmax.com/api/invoice` |
| Plans | `GET ciam.enmax.com/api/v1.3/profile/account/plans` |

Enmax only publishes **per-bill (about monthly)** data, about a day after each bill is issued. The integration spreads each bill evenly across the days of its billing period and writes it as long-term statistics. Monthly totals match your bills exactly. Daily values are averages, not real meter reads.

It polls every 6 hours.

## Install

### HACS (custom repository)
1. HACS → ⋮ → **Custom repositories** → add `https://github.com/YOUR_GITHUB_USER/ha-enmax`, category **Integration**.
2. Install **Enmax (Calgary)**, then restart Home Assistant.

### Manual
Copy `custom_components/enmax` into your Home Assistant `config/custom_components/` folder, then restart.

## Set up
1. **Settings → Devices & services → Add integration → Enmax (Calgary)**.
2. Sign in with your MyAccount email and password.
3. Wait a minute or two. The first import asks Enmax for **all** the history it has. If Enmax refuses, it falls back to the last 2 years. The log shows `Enmax full import: N bills from … to …`.

### Energy dashboard
**Settings → Dashboards → Energy**:

| Section | Statistic | Cost |
|---|---|---|
| Electricity grid → Grid consumption | `Enmax Electricity consumption` | *Use an entity tracking the total costs* → `Enmax Electricity cost` |
| Gas consumption | `Enmax Gas consumption` | `Enmax Gas cost` |
| Water consumption | `Enmax Water consumption` | `Enmax Water cost (incl. wastewater & stormwater)` |

### Bill dashboard
1. **Settings → Dashboards → Add dashboard → New dashboard from scratch**, then open it.
2. Click ✏️ → ⋮ → **Raw configuration editor**.
3. Paste `dashboards/enmax_dashboard.yaml`.
4. Replace `ACCOUNT_NO` with your 12-digit Enmax account number.

## Statistics created
`enmax:<account>_electricity_consumption`, `_electricity_cost`, `_gas_consumption`, `_gas_cost`, `_water_consumption`, `_water_cost`, `_waste_cost`

To re-import from scratch, delete them in **Developer tools → Statistics**, then reload the integration.

## Troubleshooting / development
`tools/probe.py` tests the API from any PC, outside Home Assistant:

```bash
pip install aiohttp
cd tools && python probe.py
```

It prints a per-bill table and writes `out/share_me.json`, a scrubbed sample that is safe to attach to issues.

For debug logs:
```yaml
logger:
  logs:
    custom_components.enmax: debug
```

## License
MIT
