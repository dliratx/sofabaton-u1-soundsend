"""Prepare the protocol-only regression fixture; no BLE or device writes."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / 'fixtures/volume-roundtrip.jsonl'
target = root / 'captures/volume-roundtrip-01.jsonl'
expected = [json.loads(line) for line in source.read_text().splitlines()]
if target.exists():
    actual = [dict(event=row['event'], hex=row['hex'])
              for row in (json.loads(line) for line in target.read_text().splitlines())
              if row.get('event') in ('tx', 'rx')]
    if actual != expected:
        raise SystemExit('Existing capture differs; left unchanged.')
    print('Existing capture contains the expected protocol fixture; left unchanged.')
else:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        stream.write(source.read_text())
    print('Prepared the protocol-only offline fixture.')
