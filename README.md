# POE2 Expedition / Runeshape Reward Snapshot

Hourly poe.ninja snapshots that fetch the compact Path of Exile 2 economy data needed to price the Runeshape reward catalog in Exalted and Divine.

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

poe.ninja says PoE2 economy data refreshes roughly hourly, so a Cloudflare Worker dispatches the snapshot workflows once per hour.

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

The workflow_dispatch form accepts `league` and `league_name` for manual testing.

## Output

`data/current.json` is intentionally compact and contains only the reward rows needed by the client, plus the reference conversion and minimal market metadata.

`data/currency.json` is the even smaller client-facing reward price payload.

`data/snapshots/DDMMYY_HH/<Category>.json` stores raw hourly poe.ninja category snapshots in UTC. Each snapshot folder also contains `_manifest.json` with the selected league, generation time, fetched categories, and item/line counts.

At minute 27, a separate POE2 Scout workflow enriches the same hourly folder with compact
observed pairs connecting items to Divine, Exalted, or Chaos. Raw `SnapshotPairs` payloads are
never committed; only rates, directional capacity, traded volume and observation time are kept.

## Local test

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```

The tests cover the reward pricing conversion paths and the raw category snapshot writer.

## GitHub Action

GitHub's native scheduled workflows are not used for poe.ninja snapshots. A Cloudflare Worker in `workers/poeninja-github-scheduler` owns the hourly schedule and dispatches the workflows through GitHub's `workflow_dispatch` API.

`.github/workflows/poeninja-reward-price-snapshot.yml` updates reward pricing when dispatched by the Worker at minute 7 of every hour and can also be triggered manually.

`.github/workflows/poeninja-full-category-snapshot.yml` stores the raw category snapshots when dispatched by the Worker at minute 17 of every hour and can also be triggered manually.

`.github/workflows/poe2scout-pair-snapshot.yml` adds compact pair books when dispatched at minute 27.

The reward pricing Action commits only:

```text
data/current.json
data/currency.json
```

The full category snapshot Action commits only:

```text
data/snapshots/**
```

No API key is required for the documented poe.ninja economy endpoints. Use a descriptive User-Agent and keep request volume low.




#### POENINJA:

## Category:
Currency
Fragments
Abyss
UncutGems
LineageSupportGems
Essences
SoulCores
Idols
Runes
Ritual
Expedition
Delirium
Breach
Verisium

# Currency
curl -G 'https://poe.ninja/poe2/api/economy/exchange/current/overview' \
  --data-urlencode 'league=Runes of Aldur' \
  --data-urlencode 'type=Currency'

# Runes
curl -G 'https://poe.ninja/poe2/api/economy/exchange/current/overview' \
  --data-urlencode 'league=Runes of Aldur' \
  --data-urlencode 'type=Runes'

# Expedition
curl -G 'https://poe.ninja/poe2/api/economy/exchange/current/overview' \
  --data-urlencode 'league=Runes of Aldur' \
  --data-urlencode 'type=Expedition'

# Verisium -> Alloy
curl -G 'https://poe.ninja/poe2/api/economy/exchange/current/overview' \
  --data-urlencode 'league=Runes of Aldur' \
  --data-urlencode 'type=Verisium'

# Uncut Gems -> Gems
curl -G 'https://poe.ninja/poe2/api/economy/exchange/current/overview' \
  --data-urlencode 'league=Runes of Aldur' \
  --data-urlencode 'type=UncutGems'
