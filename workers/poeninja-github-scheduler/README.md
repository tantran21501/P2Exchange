# POE Ninja GitHub Scheduler Worker

Cloudflare Worker Cron Trigger that dispatches the repository's poe.ninja snapshot GitHub Actions every hour.

## Schedule

- `7 * * * *`: dispatches `poeninja-reward-price-snapshot.yml`.
- `17 * * * *`: dispatches `poeninja-full-category-snapshot.yml`.

Cloudflare cron expressions run in UTC. The workflows still run on GitHub Actions and keep their existing Python fetch, validation, and commit steps.

## Setup

Create a fine-grained GitHub personal access token scoped to `tantran21501/P2Exchange` with `Actions: write`, then store it as a Cloudflare Worker secret:

```bash
npm install
npx wrangler secret put GITHUB_ACTIONS_DISPATCH_TOKEN
npx wrangler deploy
```

## Local development

Use `.dev.vars` for local testing only:

```text
GITHUB_ACTIONS_DISPATCH_TOKEN=github_pat_...
```

Run checks:

```bash
npm run typecheck
npm test
```

Test scheduled handlers locally:

```bash
npx wrangler dev --test-scheduled
curl "http://localhost:8787/__scheduled?cron=7+*+*+*+*"
curl "http://localhost:8787/__scheduled?cron=17+*+*+*+*"
```
