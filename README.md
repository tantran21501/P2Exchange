# POE2 Scout Currency Snapshot Server

Hourly GitHub Actions collector for the public POE2 Scout currency-exchange API.

## Files
- `scripts/fetch_snapshot.py` fetches Scout and writes JSON.
- `.github/workflows/fetch-currency-snapshot.yml` runs at 5 minutes past every UTC hour.
- `data/current.json` is the file your POE2 Expedition Radar should consume.
- `data/snapshots/YYYY-MM-DD/HH.json` keeps hourly history (30 days by default).
- `data/meta.json` describes the latest snapshot.

## Scout endpoints used
- `GET /Realms/{realm}/Filters` is not required; the collector uses `GET /{realm}/Leagues`.
- `GET /{realm}/Leagues/{leagueName}/ReferenceCurrencies`
- `GET /{realm}/Leagues/{leagueName}/ExchangeSnapshot`
- `GET /{realm}/Leagues/{leagueName}/SnapshotPairs`
- `GET /{realm}/Leagues/{leagueName}/Currencies/ByCategory`

These are public endpoints exposed by the current POE2 Scout OpenAPI.

## Setup
1. Create a GitHub repository and copy these files into it.
2. Enable Actions with **Read and write permissions** under Settings → Actions → General.
3. Optionally create repository variables:
   - `SCOUT_REALM` = `poe2` (default)
   - `SCOUT_LEAGUE` = exact league name; otherwise the script tries the active league
   - `SCOUT_USER_AGENT` = your project/contact identifier
   - `SNAPSHOT_RETENTION_DAYS` = `30`
4. Run the workflow manually once to verify the league and generated JSON.

## Client URL
For a public repo:
`https://raw.githubusercontent.com/<OWNER>/<REPO>/main/data/current.json`

Your client can cache this for 5–15 minutes and use `generated_at` to show the market timestamp.

## Notes
The collector keeps raw Scout responses and adds a small normalized layer. This makes the client less sensitive to response-wrapper changes. Missing prices must remain `null`, not zero.

For the Expedition Radar UI, calculate `total_value = unit_price * quantity`, choosing either EXALTED or DIVINE at the UI layer, then sort descending.

POE2 Scout's repository asks sustained API users to provide a User-Agent with contact information. Set `SCOUT_USER_AGENT` accordingly.

## `price_index` contract

The collector also creates a client-friendly section:

```json
{
  "price_index": {
    "base_currency": "exalted_orb",
    "divine_price_exalted": 123.45,
    "currencies": [
      {
        "api_id": "...",
        "name": "Chaos Orb",
        "price_exalted": 0.01,
        "price_divine": 0.000081,
        "current_quantity": 123
      }
    ]
  }
}
```

POE2 Scout's market analysis currently treats Exalted Orb as the base/numeraire,
so Divine is converted using the Divine Orb's current Exalted price. The raw
`ExchangeSnapshot` and `SnapshotPairs` are still retained for future liquidity
and pair-level logic.

For production use, match your Radar reward names to `price_index.currencies`
by `api_id` where possible; use the display name only as a fallback.
