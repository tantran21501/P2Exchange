# POE2 Scout Pair GitHub Scheduler

Dedicated Cloudflare Worker that dispatches `poe2scout-pair-snapshot.yml` at minute 27 UTC of
every hour. The poe.ninja category workflow runs at minute 17, so the target hourly folder exists
before compact pair enrichment begins.

Configure a fine-grained token for `tantran21501/P2Exchange` with `Actions: write`:

```bash
npm ci
npx wrangler secret put GITHUB_ACTIONS_DISPATCH_TOKEN
npm test
npm run typecheck
npm run deploy
```

The Worker never fetches or stores market data. It performs one GitHub workflow dispatch per hour.
