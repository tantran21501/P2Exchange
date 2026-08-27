#!/usr/bin/env python3
"""Deprecated compatibility stub.

The hourly pipeline no longer resolves POE2 Scout item IDs.  It uses the
public poe.ninja PoE2 exchange overview categories and matches the compact
Runeshape reward catalog by name.  This file is intentionally retained so
older checkouts do not fail if they reference it, but it performs no writes.
"""
print('[INFO] resolve_reward_ids.py is deprecated; poe.ninja category matching is used by fetch_snapshot.py')
