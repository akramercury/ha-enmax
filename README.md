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
1. HACS → ⋮ → **Custom repositories** → add `https://github.com/akramercury//enmax-energy-insights`, category **Integration**.
2. Install **Enmax (Calgary)**, then restart Home Assistant.

### Manual
Copy `custom_components/enmax` into your Home Assistant `config/custom_components/` folder, then restart.

## Set up
1. **Settings → Devices & services → Add integration → Enmax (Calgary)**.
2. Sign in with your MyAccount email and password.
3. Wait a minute or two while the first import loads the 24 bills (about 2 years) that Enmax provides.

### Keeping older history from the built-in Opower/Enmax integration
Enmax's API only returns the latest **24 bills**. If you used the built-in **Opower (Enmax)** integration before, its statistics still hold your older electricity and gas history. On the first import, this integration:

- copies the Opower electricity and gas data from **before** the first Enmax bill into the Enmax statistics, so the overlap months are counted only once;
- converts Opower's gas (labelled CCF) to GJ, using the months both sources cover to work out the factor;
- keeps that history afterwards, even if you remove Opower.

Once merged, remove the Opower entries from the Energy dashboard and use only the Enmax ones. Don't delete Opower's statistics until the merge has run. The log shows `merged N points of old history`.

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
