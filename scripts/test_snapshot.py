import json
import os
import tempfile
from pathlib import Path

os.environ['NINJA_LEAGUE']='runesofaldur'

import fetch_snapshot as fs

currency = {
    'core': {
        'primary': 'divine',
        'secondary': 'chaos',
        'rates': {'exalted': 250.0, 'chaos': 8.5},
        'items': [
            {'id': 'divine', 'name': 'Divine Orb'},
            {'id': 'exalted', 'name': 'Exalted Orb'},
            {'id': 'chaos', 'name': 'Chaos Orb'},
        ],
    },
    'lines': [
        {'id': 'divine', 'primaryValue': 1.0},
        {'id': 'exalted', 'primaryValue': 0.004},
        {'id': 'chaos', 'primaryValue': 0.1176470588},
    ],
}

runes = {
    'core': {
        'primary': 'divine',
        'secondary': 'chaos',
        'rates': {'exalted': 250.0},
        'items': [{'id': 'test-rune', 'name': "Test Rune"}],
    },
    'lines': [
        {'id': 'test-rune', 'primaryValue': 0.2, 'volumePrimaryValue': 10},
    ],
}

idx = fs.build_index(currency)
assert fs.metadata_items(currency)['divine']['name'] == 'Divine Orb'
ref = fs.derive_reference(currency, idx)
assert ref['exalted_primary_value'] == 0.004
assert ref['divine_primary_value'] == 1.0
assert abs(ref['exalted_per_divine'] - 250.0) < 1e-9

ridx = fs.build_index(runes)
ex, div = fs.reward_price(ridx['testrune'], ref)
assert abs(ex - 50.0) < 1e-9
assert abs(div - 0.2) < 1e-9

print('PASS: list-shaped core.items + primaryValue anchor conversion')

# Fallback test: no anchor lines, primary=divine, rates.exalted=250 means 1D=250E.
currency_fallback = {
    "core": {
        "primary": "divine",
        "secondary": "chaos",
        "rates": {"exalted": 250.0, "chaos": 8.5},
        "items": [{"id": "divine", "name": "Divine Orb"}, {"id": "exalted", "name": "Exalted Orb"}],
    },
    "lines": [],
}
ref2 = fs.derive_reference(currency_fallback, fs.build_index(currency_fallback))
assert abs(ref2["exalted_primary_value"] - 0.004) < 1e-12
assert abs(ref2["divine_primary_value"] - 1.0) < 1e-12
assert abs(ref2["exalted_per_divine"] - 250.0) < 1e-9
print('PASS: core.rates fallback conversion')
