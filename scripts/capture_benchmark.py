"""Capture isolated loopback workloads without models, provider calls or platform DB writes."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CASES = {'pool': 'pool', 'upstream': 'slow', 'payment_timeout': 'incomplete', 'memory': 'memory', 'cpu': 'cpu',
         'queue': 'backlog', 'throttling': 'throttling', 'deployment': 'schema', 'tls': 'tls',
         'configuration': 'startup', 'incomplete': 'incomplete'}


async def capture(case, output):
    from backend.lab import LabRuntime
    directory = Path(output).resolve()
    if not directory.is_relative_to(ROOT/'.cache') and not directory.is_relative_to(ROOT/'benchmarks/private'):
        raise ValueError('Capture only into ignored .cache or benchmarks/private.')
    if directory.exists():
        raise ValueError('Use a new directory; previous evidence must not be overwritten.')
    runtime = LabRuntime(CASES[case], uuid.uuid4().hex, directory, lambda _: None)
    state, error = 'completed', None
    try:
        await asyncio.wait_for(runtime.execute(), timeout=120)
    except Exception as exc:
        state, error = 'failed', str(exc)[:300]
    value = {'version': 'benchmark-v2', 'case': case, 'state': state, 'error': error,
        'symptom': 'Investigate the recorded application behavior.', 'evidence': runtime.rows,
        'coverage': 'Controlled loopback capture with baseline, exercised workload and recovery. No external provider telemetry.'}
    path = directory/'capture.json'
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')
    (directory/'capture-manifest.json').write_text(json.dumps({'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'case': case, 'requests': runtime.requests, 'failures': runtime.failures, 'state': state}, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(path), 'state': state, 'observations': len(runtime.rows)}))
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=sorted(CASES), required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(capture(args.case, args.output)) == 'completed' else 1)
