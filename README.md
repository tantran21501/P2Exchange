# POE2 Expedition / Runeshape Reward Snapshot

Hourly GitHub Action that fetches the compact Path of Exile 2 economy data needed to price the Runeshape reward catalog in Exalted and Divine.

## Data source

Uses the documented public poe.ninja PoE2 economy API:

- `GET /poe2/api/economy/leagues`
- `GET /poe2/api/economy/exchange/current/overview?league={id}&type={type}`

Supported categories used by this project:

- `Currency`
- `Runes`
- `UncutGems`
- `Expedition`
- `Verisium` (only if rewards.json uses this type)

poe.ninja says PoE2 economy data refreshes roughly hourly, so the workflow runs once per hour.

## Important implementation detail

Do **not** use the HTML URL:

`https://poe.ninja/poe2/economy/...`

The Action uses `/poe2/api/economy/...` instead.

Also do not assume `core.primary` is Exalted. `primaryValue` is quoted in whatever currency `core.primary` currently names. The script resolves Exalted and Divine in the Currency overview and calculates:

`reward_exalted = reward_primary / exalted_primary_value`

`reward_divine = reward_primary / divine_primary_value`

If the anchor line is missing, it falls back to `core.rates` using the documented rate direction (`units of that currency per 1 primary`).

## League configuration

Recommended repository variables:

```text
NINJA_LEAGUE=runesofaldur
```

For Hardcore:

```text
NINJA_LEAGUE=runesofaldurhc
```

You can also leave `NINJA_LEAGUE` empty and use `NINJA_LEAGUE_NAME`, or let the script select the first active temporary league returned by `/economy/leagues`.

The workflow_dispatch form accepts `league_id` and `league_name` for manual testing.

## Output

`data/current.json` is intentionally compact and contains only the reward rows needed by the client, plus the reference conversion and minimal market metadata.

`data/snapshots/YYYY-MM-DD/HH.json` stores the hourly history. Old snapshots are removed according to `SNAPSHOT_RETENTION_DAYS` (default 30).

## Local test

```bash
cd scripts
python test_snapshot.py
```

The tests cover both the normal anchor-line path and the `core.rates` fallback path.

## GitHub Action

`.github/workflows/poe2-reward-price-snapshot.yml` runs at minute 5 of every hour and can also be triggered manually.

The Action commits only:

```text
data/current.json
data/meta.json
data/snapshots/**
```

No API key is required for the documented poe.ninja economy endpoints. Use a descriptive User-Agent and keep request volume low.
