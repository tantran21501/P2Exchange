# POE2 Expedition Reward Snapshot

Hourly GitHub Action for a compact Runeshape reward price feed.

## Sources

- POE2DB Runeshape Combinations: https://poe2db.tw/Runeshape_Combinations
- RuneshapePriceChecker: https://github.com/Barragek0/RuneshapePriceChecker
- POE2 Scout API: https://api.poe2scout.com/swagger

The current Runeshape dataset is 321 combinations and is grouped by PoE2DB into 92 Currency, 131 Runes, 14 Alloys, 61 Gems and 23 generic Unique reward buckets. Generic Unique buckets are deliberately excluded because the reward is not a deterministic unique item.

## Runtime flow

1. `build_rewards.py` refreshes the compact catalog from the Runeshape page.
2. `resolve_reward_ids.py` resolves Scout identifiers for non-currency items and stores them in `data/rewards.json`.
3. `fetch_snapshot.py` fetches Divine once and then one price endpoint per reward.
4. Reward price is normalized to Exalted and converted to Divine with:

   `price_divine = price_exalted / divine_price_exalted`

The hourly snapshot never downloads the full Scout economy table.

## Important

The Scout API exposes both currency and item endpoints in its current Swagger:
- `/Currencies/{apiId}`
- `/Items/{itemId}`

The public RuneshapePriceChecker project also documents that POE2 Scout supplies currency, expedition, rune, verisium, uncut gem and unique pricing.

If Scout changes the aggregate `/Items` search contract, only `resolve_reward_ids.py` needs adjustment; the hourly snapshot remains per-item.
