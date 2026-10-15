# Enmax MyAccount API map (reverse engineered Oct 2026)

## Auth: Microsoft Entra External ID native auth (no browser)
- Tenant `4507be72-fb58-48dd-a5d6-db01565b8092`, client_id `81b6fef3-f5f6-4c82-9d79-122770617073` (public SPA client)
- Base `https://extauth.enmax.com/{tenant}/oauth2/v2.0/`, all `application/x-www-form-urlencoded`
  1. `initiate`  client_id, username, challenge_type=`password redirect` -> continuation_token
  2. `challenge` client_id, continuation_token, challenge_type=`password redirect` -> challenge_type=password, continuation_token
  3. `token`     client_id, continuation_token, grant_type=password, password, scope=`openid offline_access api://301c0425-005b-4ddc-80da-c8d1901bdc5b/.default`
     -> access_token (~3667 s), refresh_token, id_token
- Refresh: standard grant_type=refresh_token on `token` works (verified). Website itself uses `POST myaccount.enmax.com/api/account/access-token/refresh`.

## Data: `https://ciam.enmax.com/api/v1.3`, header `Authorization: Bearer <access_token>`
| Endpoint | Params | Notes |
|---|---|---|
| GET /associated/accounts | - | `associated_account.accounts[].account` -> account_no, bp_id, is_default, has_municipal_services |
| GET /profile/account/plans | account_no | site_details.site[] current_plan (rate, term, renewal_date); product_type 2 = electricity, 1 = gas |
| GET /profile/account/details | account_no, bp | customer info (PII) |
| GET /profile/account/balance | account_no, bp | bill.current_balance, amount_due, current_balance_due_date, last_bill_amount/date, last_payment_* |
| GET /energy/insights | account_no, start_date, end_date (YYYY-MM-DD) | `energy_insights[]`, one record per bill. **Max 24 bills**: start 2020 still returns from Nov 2024; start 2010 → HTTP 400 `energy-insights-001e` |
| GET /invoice/list | ba (=account_no) | `docs[].bills[]`: doc_ind ("I" = invoice), bill_date, bill_amount, doc_id, file_name (24 bills) |
| GET https://invoices.enmax.com/api/invoice | ba, docId | bill PDF (works, verified) |

## energy_insights[] record (verified against live data)
- `bill_date`
- `electricity`: usage (kWh), bill_amount, amount, rate, average_rate, days_billed, start_date, end_date, read_type (0 actual, 1 estimate), product_id, is_fixed_rate
- `natural_gas`: same fields, usage in GJ
- `municipal`: start_date, end_date, days_billed, total_bill_amount, service_charge,
  water{usage m3, amount, rate, start_date, end_date}, wastewater{usage, amount, start_date, end_date},
  stormwater_management{amount}, black_cart_program{amount}, blue_cart_program{amount},
  green_cart_program{amount}, residential_garbage{amount}

## Integration decisions (ha-enmax)
- Bills spread evenly per day over billing period → external statistics `enmax:<account>_<series>` (electricity/gas/water consumption + costs, waste cost).
- Gas stays in GJ (energy unit class) — HA Energy gas section must not contain volume (CCF/m³) sources.
- Because of the 24-bill limit, v0.3.0 merges older history from old Opower stats (`opower:enmx_elec_*`, `opower:enmx_gas_*`) on first import; gas CCF→GJ factor derived from overlap months. User's Opower history: Dec 2020 – Jul 2025.
- HA folder must be `custom_components/enmax` (user once had it as `enamx`).
