# POE2 Expedition / Runeshape Reward Price Snapshot

This version does **not** call the legacy POE2 Scout `/poe2/Leagues/.../Currencies/...` route.
That route currently returns HTTP 400 for `divine-orb` on the public API.

The hourly job uses the documented public **poe.ninja PoE2 economy exchange overview**:

- `Currency`
- `Runes`
- `UncutGems`

The Runeshape reward catalog is filtered locally, so the committed snapshot contains only the rewards in `data/rewards.json`, not the full economy payload.

Prices are normalized to both Exalted and Divine using the Exalted/Divine anchor lines from the same `Currency` snapshot. No hard-coded EX/DIV rate is used.

## Run

```bash
python scripts/fetch_snapshot.py
```

Set `NINJA_LEAGUE` (or the GitHub Actions variable `NINJA_LEAGUE`) to the exact league display name.

## Why 171 rewards were unresolved before

The old pipeline tried to resolve Rune/Alloy/Gem names through POE2 Scout `/Items` and then fetched Scout `/Currencies/{apiId}` for Divine. Those are not the right pricing path for this reward pool. The poe.ninja exchange API explicitly exposes `Runes`, `UncutGems`, and other PoE2 economy categories, so the new pipeline fetches each category once and performs exact local name matching.
