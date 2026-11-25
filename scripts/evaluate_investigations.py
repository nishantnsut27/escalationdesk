"""Version-two benchmark: frozen evidence, explicit live runs, complete failures."""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.benchmark_scoring import citations, score, numeric_summary, usage_tokens


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_manifest(root=ROOT):
    paths = sorted([*root.glob('backend/*.py'), *root.glob('backend/migrations/*.sql'), root/'requirements.txt'])
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def query_snapshot(records, *, kind=None, service=None, limit=100, trace_id=None, start_time=None, end_time=None, **options):
    from backend.evidence_contracts import timestamp
    from backend.signatures import detect
    start, end = timestamp(start_time), timestamp(end_time)
    def matches(row):
        at = timestamp(row.get('timestamp'))
        return ((not kind or row['kind'] == kind) and (not service or row['service'] == service)
            and (not trace_id or row.get('trace_id') == trace_id)
            and all(not options.get(k) or row.get(k) == options[k] for k in ('request_id', 'level'))
            and (not options.get('query') or options['query'].lower() in str(row.get('message', '')).lower())
            and (not options.get('error_type') or options['error_type'] in detect(row))
            and (not start or at and at >= start) and (not end or at and at <= end))
    found = sorted((r for r in records if matches(r)), key=lambda r: (timestamp(r.get('timestamp')) or '~', r['id']))
    offset = options.get('offset', 0)
    return deepcopy(found[offset:offset+limit])


def summarize(attempts):
    completed = [a for a in attempts if a.get('state') == 'completed']
    reviewed = [a for a in completed if a.get('review')]
    result = {'attempts': len(attempts), 'completed': len(completed), 'failed': len(attempts)-len(completed),
              'semantically_reviewed': len(reviewed), 'unreviewed': len(completed)-len(reviewed)}
    for field in ('mechanism_correct', 'service_correct', 'abstention_correct'):
        values = [a['review'][field] for a in reviewed if type(a['review'].get(field)) is bool]
        result[field] = {'correct': sum(values), 'reviewed': len(values), 'rate': sum(values)/len(values) if values else None}
    result['note'] = 'Missing semantic reviews remain unmeasured. Failed attempts are included in the denominator report.'
    for field in ('duration_ms', 'tool_call_count'):
        result[field] = numeric_summary([a.get(field) for a in attempts])
    for field in ('citation_validity', 'evidence_precision', 'retrieved_evidence_recall', 'supplied_evidence_recall'):
        result[field] = numeric_summary([a.get('metrics', {}).get(field) for a in attempts])
    for field in ('prompt_tokens', 'completion_tokens'):
        result[field] = numeric_summary([usage_tokens(a, field) for a in attempts])
    return result


def implementation(arm, baseline):
    if arm != 'A':
        return importlib.import_module('backend.investigator'), ROOT
    if not baseline:
        raise ValueError('Arm A requires a frozen baseline directory with manifest.json.')
    directory = Path(baseline).resolve()
    manifest = read(directory/'manifest.json')
    for path, expected in manifest['files'].items():
        candidate = (directory/path).resolve()
        if not candidate.is_relative_to(directory) or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen baseline source digest mismatch.')
    spec = importlib.util.spec_from_file_location('frozen_backend', directory/'backend/__init__.py', submodule_search_locations=[str(directory/'backend')])
    package = importlib.util.module_from_spec(spec); sys.modules['frozen_backend'] = package; spec.loader.exec_module(package)
    return importlib.import_module('frozen_backend.investigator'), directory


async def run(args):
    if not args.live:
        raise ValueError('Running the benchmark requires --live; recorded inspection never spends provider credits.')
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT/'.cache') and not output.is_relative_to(ROOT/'benchmarks/results'):
        raise ValueError('Keep provider results in .cache or ignored benchmarks/results.')
    if output.exists():
        raise ValueError('Never overwrite a retained benchmark attempt.')
    from backend.provider import groq_http_client
    capture = read(args.capture)
    records = capture.get('cross_source_evidence') if args.arm == 'D' else capture.get('evidence')
    if not isinstance(records, list) or not 1 <= len(records) <= 5000:
        raise ValueError('Supply 1-5000 captured observations; arm D requires cross_source_evidence.')
    if len(json.dumps(records).encode()) > 10*1024*1024 or any(not isinstance(r, dict) or not all(k in r for k in ('id','kind','service','timestamp')) for r in records):
        raise ValueError('Invalid captured evidence or snapshot larger than 10 MiB.')
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('Captured evidence IDs must be unique.')
    module, source_root = implementation(args.arm, args.baseline)
    evidence = importlib.import_module(module.__package__+'.evidence')
    class SnapshotTools(evidence.EvidenceTools):
        def __init__(self):
            super().__init__('benchmark', 'benchmark-owner', groq=True)
            self.services = {r['service'] for r in records}
            self.collection_id = 'benchmark-snapshot'
            self.collection = {'id': self.collection_id, 'symptom': capture.get('symptom', ''), 'summary': json.dumps({
                'observations': len(records), 'services': sorted(self.services), 'kinds': sorted({r['kind'] for r in records}),
                'coverage': capture.get('coverage', 'Recorded controlled capture; completeness is not assumed.'),
                'mappings': capture.get('mappings', {})})}
            self.definitions = deepcopy(evidence.DEFINITIONS)
            for definition in self.definitions:
                properties = definition['function']['parameters']['properties']
                if 'service' in properties:
                    properties['service']['enum'] = sorted(self.services)
                if 'trace_id' in properties:
                    properties['trace_id'] = {'type': 'string', 'maxLength': 128}
        def snapshot(self):
            return deepcopy(records)
    def query(run, owner, kind=None, service=None, limit=100, trace_id=None, start_time=None, end_time=None, **options):
        return query_snapshot(records, kind=kind, service=service, limit=limit, trace_id=trace_id,
            start_time=start_time, end_time=end_time, **options)
    result = {'version': 'benchmark-v2', 'arm': args.arm, 'case': capture.get('case'),
              'evidence_digest': hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
              'source_files': source_manifest(source_root), 'state': 'failed', 'activity': [], 'review': None,
              'capture_sha256': hashlib.sha256(Path(args.capture).read_bytes()).hexdigest(), 'relationship_mode': args.relationships}
    usage = {'workflow_version': 'v1' if args.arm == 'A' else 'v2',
             'selection_version': 'failure-blocks-v2' if args.arm in {'C','D'} else 'failure-blocks-v1',
             'relationship_mode': args.relationships}
    def record(name, arguments, value):
        result['activity'].append({'tool': name, 'arguments': arguments, 'result': value})
    started = time.monotonic()
    try:
        async with groq_http_client() as http:
            def snapshot_scalar(sql, arguments=()):
                if 'COUNT(*)' in sql and "kind='metric'" in sql:
                    return {'n': sum(r['kind'] == 'metric' for r in records)}
                raise ValueError('Benchmark attempted a database read outside its frozen snapshot.')
            with patch.object(evidence.db, 'evidence', query), patch.object(evidence.db, 'one', snapshot_scalar):
                async with asyncio.timeout(300):
                    report, usage = await module.Investigator(http).groq(SnapshotTools(), record, usage)
        result.update(state='completed', report=report, citation_validity=citations(report) <= set(usage.get('supplied_evidence_ids', [])))
    except Exception as exc:
        result['error'] = str(exc)[:300]
    result.update(usage=usage, duration_ms=round((time.monotonic()-started)*1000),
                  tool_call_count=sum(a['tool'] in evidence.TOOL_NAMES for a in result['activity']))
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT/'.cache') and not output.is_relative_to(ROOT/'benchmarks/results'):
        raise ValueError('Keep provider results in .cache or ignored benchmarks/results.')
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('Never overwrite a retained benchmark attempt.')
    with output.open('x', encoding='utf-8') as file:
        file.write(json.dumps(result, indent=2))
    print(json.dumps({'state': result['state'], 'output': str(output), 'semantic_review': 'pending'}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    running = commands.add_parser('run'); running.add_argument('--arm', choices=list('ABCD'), required=True)
    running.add_argument('--capture', required=True); running.add_argument('--output', required=True)
    running.add_argument('--baseline'); running.add_argument('--live', action='store_true')
    running.add_argument('--relationships', choices=['enabled','disabled'], default='enabled')
    summary = commands.add_parser('summarize'); summary.add_argument('files', nargs='+')
    scoring = commands.add_parser('score'); scoring.add_argument('--attempt', required=True); scoring.add_argument('--review', required=True)
    scoring.add_argument('--truth', required=True); scoring.add_argument('--output', required=True)
    commands.add_parser('manifest')
    args = parser.parse_args()
    if args.command == 'run':
        asyncio.run(run(args))
    elif args.command == 'summarize':
        attempts = [read(path) for path in args.files]
        print(json.dumps({'all': summarize(attempts), 'by_arm': {arm: summarize([a for a in attempts if a.get('arm') == arm]) for arm in 'ABCD'}}, indent=2))
    elif args.command == 'score':
        review = read(args.review)
        if review.get('attempt_sha256') != hashlib.sha256(Path(args.attempt).read_bytes()).hexdigest():
            raise ValueError('Review must identify the exact retained attempt SHA-256.')
        output = Path(args.output).resolve()
        if not output.is_relative_to(ROOT/'.cache') and not output.is_relative_to(ROOT/'benchmarks/results'):
            raise ValueError('Keep scored results private.')
        value = score(read(args.attempt), review, read(args.truth))
        with output.open('x', encoding='utf-8') as file:
            file.write(json.dumps(value, indent=2))
        print(json.dumps({'output': str(output), 'state': value['state']}))
    else:
        print(json.dumps(source_manifest(), indent=2))


if __name__ == '__main__':
    main()
