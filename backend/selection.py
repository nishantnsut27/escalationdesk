"""Failure-focused evidence blocks and exact, inspectable model excerpts."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re

from . import db
from .signatures import detect, signature_text, VERSION

VERSION_SELECTION = "failure-blocks-v1"
TEXT_FIELDS = {"message", "stack_trace", "error", "exception", "original"}
OMISSION = "\n[... omitted ...]\n"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def stamp(row):
    try:
        value = datetime.fromisoformat(row.get("timestamp"))
        return value.timestamp() if value.tzinfo else None
    except (ValueError, TypeError):
        return None


def failure(row):
    return (row.get("level") == "error" or row.get("status") == "error" or row.get("http_status", 0) >= 400
            or detect(row) != ["unknown"])


def repetition_shape(row):
    # Repetition can span requests, but distinct operations, statuses and
    # measurements must never inherit another observation's occurrence count.
    ignored = {"id", "timestamp", "started_at", "trace_id", "request_id", "span_id", "parent_id",
               "parent_span_id", "original", "source_id", "source_line", "source_end_line", "collection_id", "run_id"}
    return json.dumps({key: value for key, value in row.items() if key not in ignored}, sort_keys=True, ensure_ascii=False)


def select_blocks(rows, symptom="", maximum=100, *, version=VERSION_SELECTION, mappings=None, strict_identity=False):
    """Select context, never infer a causal relationship from proximity."""
    rows = list(rows)
    if version == 'failure-blocks-v2':
        return select_blocks_v2(rows, symptom, maximum, mappings=mappings)
    if version != VERSION_SELECTION:
        raise ValueError('Unknown evidence selection version.')
    terms = {term.lower() for term in re.findall(r"[\w.-]{4,}", symptom) if term.lower() not in {"with", "from", "this", "that", "have", "after", "before", "when"}}
    seeds = sorted(enumerate(rows), key=lambda pair: (
        not failure(pair[1]), -sum(term in signature_text(pair[1]).lower() for term in terms),
        not bool(pair[1].get("exception_type") or pair[1].get("stack_trace")), pair[0]))
    # Cover distinct observed failures and services before repeats of one error.
    anchors, shapes = [], set()
    for index, row in seeds:
        shape = (row["service"], tuple(detect(row)), row.get("message", "")[:200])
        if shape not in shapes:
            anchors.append((index, row))
            shapes.add(shape)
        if len(anchors) == 8:
            break
    chosen, reasons = {}, {}
    def add(row, reason, anchor):
        if row["id"] not in chosen and len(chosen) >= maximum:
            return
        chosen[row["id"]] = row
        reasons.setdefault(row["id"], [])
        entry = {"reason": reason, "anchor_id": anchor}
        if entry not in reasons[row["id"]]:
            reasons[row["id"]].append(entry)
    for _, row in anchors:
        add(row, "explicit_failure" if failure(row) else "symptom_or_unclassified", row["id"])
    # Reserve measured extremes and independent successes before dense request
    # context fills the bound. Their timing is not an inferred relationship.
    for _, anchor in anchors:
        samples = [row for row in rows if row["service"] == anchor["service"] and row["kind"] == "metric"]
        fields = {key for row in samples for key, value in row.items() if type(value) in (int, float)
                  and key not in {"source_line", "source_end_line", "requests", "failures"}}
        if samples:
            for row in (samples[0], samples[-1]):
                add(row, "available_measurement", anchor["id"])
        for key in sorted(fields):
            values = [row for row in samples if type(row.get(key)) in (int, float)]
            for row in (min(values, key=lambda row: row[key]), max(values, key=lambda row: row[key])):
                add(row, "available_measurement", anchor["id"])
        successes = [row for row in rows if row["kind"] == "log" and row["service"] == anchor["service"] and not failure(row)]
        if successes:
            for row in (successes[0], successes[-1]):
                add(row, "independent_success_or_conflict", anchor["id"])
    for index, anchor in anchors:
        trace, request, at = anchor.get("trace_id"), anchor.get("request_id"), stamp(anchor)
        if trace or request:
            key, value = ("trace_id", trace) if trace else ("request_id", request)
            context = [row for row in rows if row.get(key) == value]
            if strict_identity:
                from .evidence_contracts import namespace, trace_key
                context = [row for row in context if trace_key(row) == trace_key(anchor)] if trace else [row for row in context if namespace(row) == namespace(anchor)]
            reason = "same_" + key
        elif at is not None:
            context = [row for row in rows if row["service"] == anchor["service"] and stamp(row) is not None and abs(stamp(row) - at) <= 30]
            reason = "same_service_30s_context_only"
        else:
            context = rows[max(0, index - 5):index + 6]
            reason = "adjacent_5_context_only"
        # Parent spans, measurements and conflicting successes have equal access
        # to each block's bound; dense repeated errors cannot consume it first.
        buckets = {}
        for row in context:
            buckets.setdefault((row["kind"], row["service"], failure(row)), []).append(row)
        for n in range(max((len(bucket) for bucket in buckets.values()), default=0)):
            for bucket in buckets.values():
                if n < len(bucket):
                    add(bucket[n], reason, anchor["id"])
        for kind in ("metric", "span", "log"):
            candidates = [row for row in rows if row["kind"] == kind and row["service"] == anchor["service"] and (kind != "log" or not failure(row))]
            candidates.sort(key=lambda row: abs(stamp(row) - at) if stamp(row) is not None and at is not None else float("inf"))
            for row in candidates[:2]:
                add(row, "available_measurement" if kind != "log" else "independent_success_or_conflict", anchor["id"])
    groups = {}
    for row in rows:
        shape = repetition_shape(row)
        groups.setdefault(shape, []).append(row["id"])
    metadata = {}
    for row in chosen.values():
        shape = repetition_shape(row)
        occurrences = groups[shape]
        end_line = row.get("source_end_line")
        if end_line is None and row.get("source_line"):
            end_line = row["source_line"] + str(row.get("original", row.get("message", ""))).count("\n")
        metadata[row["id"]] = {"selection": reasons[row["id"]], "occurrence_ids": occurrences,
                               "occurrences": len(occurrences), "source_line": row.get("source_line"),
                               "source_end_line": end_line,
                               "signatures": detect(row), "signature_version": VERSION}
    return list(chosen.values()), {"version": VERSION_SELECTION, "total_observations": len(rows),
        "selected_observations": len(chosen), "omitted_observations": len(rows) - len(chosen), "sources": metadata,
        "note": "Proximity is context only, never a dependency or causal edge. Repeated IDs remain distinct observations."}


def select_blocks_v2(rows, symptom='', maximum=100, *, mappings=None):
    from .evidence_graph import build
    initial, metadata = select_blocks(rows, symptom, maximum, strict_identity=True)
    by_id = {r['id']: r for r in rows}
    graph = build(rows, mappings=mappings)
    neighbors = {}
    for edge in graph['edges']:
        if edge['relationship'] in {'parent_span', 'shared_cross_source_trace'}:
            neighbors.setdefault(edge['source'], []).append(edge['target'])
            neighbors.setdefault(edge['target'], []).append(edge['source'])
    ordered = []
    anchors = [r for r in initial if failure(r)]
    conflicts = [r for r in initial if not failure(r) and r['kind'] in {'metric', 'log'}]
    def add(row, reason):
        if row['id'] not in {r['id'] for r in ordered} and len(ordered) < maximum:
            ordered.append(row)
            metadata['sources'].setdefault(row['id'], {'selection': [], 'occurrence_ids': [row['id']], 'occurrences': 1})
            metadata['sources'][row['id']]['selection'].append({'reason': reason, 'anchor_id': row['id']})
    for row in anchors[:8]:
        add(row, 'rare_failure_anchor')
    for row in conflicts[:8]:
        add(row, 'reserved_measurement_or_counterevidence')
    for row in anchors[:8]:
        for ref in neighbors.get(row['id'], [])[:4]:
            add(by_id[ref], 'recorded_identity_neighborhood')
    metrics = {}
    for row in rows:
        if row['kind'] == 'metric' and type(row.get('value')) in (int, float):
            key = (row['service'], row.get('metric_name'), json.dumps(row.get('dimensions', {}), sort_keys=True))
            metrics.setdefault(key, []).append(row)
    for samples in metrics.values():
        for row in (samples[0], samples[-1], min(samples, key=lambda r: r['value']), max(samples, key=lambda r: r['value'])):
            add(row, 'metric_series_extreme_or_endpoint')
    for row in initial:
        add(row, 'diverse_representative')
    metadata.update(version='failure-blocks-v2', selected_observations=len(ordered),
                    omitted_observations=len(rows)-len(ordered), relationship_version=graph['version'])
    return ordered, metadata


def fetch_owned(tools):
    from .evidence_scope import rows as scope_rows
    rows = scope_rows(tools.owner, collection_id=tools.collection_id, run_id=tools.run_id)
    return [db.public_evidence(row) for row in rows]


def ranged_excerpt(value, ranges, previous=None):
    """Compose ranges through an earlier excerpt, excluding its omission markers."""
    if not previous:
        return OMISSION.join(value[a:b] for a, b in ranges), ranges, len(value)
    segments, cursor = [], 0
    for source_start, source_end in previous["ranges"]:
        length = source_end - source_start
        segments.append((cursor, cursor + length, source_start))
        cursor += length + len(OMISSION)
    pieces, mapped = [], []
    for start, end in ranges:
        for left, right, source_start in segments:
            a, b = max(start, left), min(end, right)
            if a < b:
                pieces.append(value[a:b])
                mapped.append([source_start + a - left, source_start + b - left])
    return OMISSION.join(pieces), mapped, previous["source_chars"]


def excerpt(row, budget=1600):
    """Retain real characters/ranges. Never silently erase an oversized source."""
    result = deepcopy(row)
    if len(encoded(result)) <= budget:
        return result
    prior = result.pop("_excerpt", {})
    original_ranges = deepcopy(prior)
    # Evidence can include arbitrarily large attribute maps. Keep them as a
    # searchable, ranged JSON excerpt instead of spending the whole input on it.
    for key, value in list(result.items()):
        if isinstance(value, (dict, list)) and len(encoded(value)) > 180 and key not in {"error_tags"}:
            result[key] = encoded(value).decode()
            prior[key] = {"ranges": [[0, len(result[key])]], "source_chars": len(result[key]),
                          "unit": "unicode_codepoints", "encoding": "json"}
    full_strings = {key: value for key, value in result.items() if isinstance(value, str)
                    and key not in {"id", "kind", "service", "timestamp", "trace_id", "span_id", "parent_id", "request_id"}}
    sizes = {key: len(value) for key, value in full_strings.items()}
    for _ in range(100):
        if len(encoded({**result, **({"_excerpt": prior} if prior else {})})) <= budget:
            break
        candidates = [key for key in sizes if sizes[key] > 32]
        if not candidates:
            break
        key = max(candidates, key=lambda key: sizes[key])
        full = full_strings[key]
        target = max(32, sizes[key] // 2)
        sizes[key] = target
        head, tail = max(16, target * 2 // 3), max(16, target // 3)
        ranges = [[0, head], [len(full) - tail, len(full)]]
        # Prefer an explicit nested cause when it lies outside the head/tail.
        cause = re.search(r"Caused by:|During handling|The above exception", full[head:len(full) - tail])
        if cause and target >= 180:
            start = head + cause.start()
            ranges.insert(1, [start, min(start + target // 3, len(full) - tail)])
        result[key], ranges, source_chars = ranged_excerpt(full, ranges, original_ranges.get(key))
        prior[key] = {**prior.get(key, {}), "ranges": ranges, "source_chars": source_chars, "unit": "unicode_codepoints"}
    if prior:
        result["_excerpt"] = prior
    if len(encoded(result)) > budget:
        essential = {"id", "kind", "service", "timestamp", "trace_id", "span_id", "parent_id", "request_id",
                     "message", "stack_trace", "exception_type", "error_type", "level", "status", "duration_ms"}
        omitted = []
        for key in sorted((key for key in result if key not in essential and key != "_excerpt"),
                          key=lambda key: len(encoded(result[key])), reverse=True):
            omitted.append(key)
            result.pop(key)
            prior.pop(key, None)
            result["_excerpt"] = prior
            result["_omitted_fields"] = {"count": len(omitted), "note": "Additional fields omitted; inspect the full retained source."}
            if len(encoded(result)) <= budget:
                break
    return result


def provenance(records, originals, metadata=None):
    sources = (metadata or {}).get("sources", {})
    result = []
    for record in records:
        source = originals.get(record["id"], record)
        fields = {}
        for key, value in record.items():
            if (key in TEXT_FIELDS or key in record.get("_excerpt", {})) and isinstance(value, str):
                fields[key] = record.get("_excerpt", {}).get(key, {"ranges": [[0, len(value)]], "source_chars": len(value), "unit": "unicode_codepoints"})
        result.append({"evidence_id": record["id"], "record": deepcopy(record), "fields": fields,
                       "source_sha256": hashlib.sha256(encoded(source)).hexdigest(),
                       "selection": sources.get(record["id"], {"selection": [{"reason": "bounded_evidence_tool"}], "source_line": source.get("source_line")})})
    return result
