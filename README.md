# P2Exchange snapshot archive

This repository preserves historical PoE 2 market snapshots and the scripts used to inspect their formats. Automated collection is retired: GitHub Actions workflows and Cloudflare schedule workers have been removed. Existing GitHub Actions run history and the committed `data/` snapshots remain available as reference material.

The Windows client now requests fresh prices directly from poe.ninja and POE2 Scout. This archive is not a client backend and does not need to be deployed.

## Preserved data

- `data/current.json` and `data/currency.json` are historical compact price exports.
- `data/snapshots/` contains archived category snapshots and manifests.
- `rewards.json` is the reward-name reference used by the historical collectors.

## Local research tools

Scripts under `scripts/` can parse and validate the archived source formats. They are not scheduled or connected to a project server.

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```
