#!/usr/bin/env python3
"""Read a task ledger through taskctl's parser without changing the book.

taskpeek --book BOOK [--stage ID] [--task ID_OR_KIND ...] [--verify]
prints the actual cursor and selected task records as JSON. --verify compares
all selected inputs, outputs, checks and nested evidence hashes, then
uses novelctl's existing book/index checker (without full snapshot rehashing).
It is an orchestrator tool: its output may contain private paths and decisions.
An unsuccessful verification still returns the observed task state; never
repeat a successful submit merely because a wrapper or later read-back failed.
"""
import argparse
import copy
import json

import novelctl as nc
import taskctl as tc


def verify_evidence(root, task, hashes=None):
    """Find declared path/hash evidence, including nested outline candidates.

    Keep each reference's source but hash identical paths only once. Narrative
    dictionaries with a path alone, non-SHA text or summary content are not
    evidence declarations. A bad nested path is a verification failure rather
    than a reason to discard an otherwise successfully read task state.
    """
    records = []
    hashes = {} if hashes is None else hashes

    def is_evidence(item):
        sha = item.get('sha256')
        return (isinstance(item.get('path'), str) and isinstance(sha, str)
                and len(sha) == 64 and all(char in '0123456789abcdef' for char in sha))

    def visit(item, source):
        if isinstance(item, list):
            for child in item:
                visit(child, source)
            return
        if not isinstance(item, dict):
            return
        if is_evidence(item):
            relative = item['path']
            if relative not in hashes:
                try:
                    path = nc.relative_path(root, relative)
                    hashes[relative] = (nc.digest(path) if path.is_file() else None, None)
                except (ValueError, OSError) as exc:
                    hashes[relative] = (None, str(exc))
            actual, error = hashes[relative]
            record = dict(source=source, path=relative, sha256=item['sha256'],
                          actual_sha256=actual, ok=not error and actual == item['sha256'])
            if error:
                record['error'] = error
            records.append(record)
        for key, value in item.items():
            if key not in ('path', 'sha256', 'summary'):
                visit(value, source + '.' + key)

    for source in ('inputs', 'outputs'):
        for item in task[source]:
            visit(item, source)
    for name, item in task.get('checks', {}).items():
        visit(item, 'checks.' + name)
    return records


def peek(book, stage=None, tasks=None, verify=False):
    """Read one coherent snapshot; status is evidence, not an acceptance action."""
    with tc.reading(book) as root:
        data, (stages, _) = tc.load(root)
        selected_stage = stages.get(stage or data['current_stage'])
        tc.require(selected_stage is not None, 'unknown stage')
        selected = selected_stage['tasks']
        if tasks:
            resolved = []
            for selector in tasks:
                matches = [item for item in selected
                           if item['id'] == selector or item['kind'] == selector]
                tc.require(len(matches) == 1, 'task selector must match exactly one task: ' + selector)
                if matches[0] not in resolved:
                    resolved.append(matches[0])
            selected = resolved
        result = dict(ok=True, read_ok=True, current_stage=data['current_stage'],
                      current_task=data['current_task'],
                      stage=selected_stage['id'],
                      stage_status=selected_stage['status'],
                      workflow=selected_stage['workflow'],
                      tasks=copy.deepcopy(selected))
        if verify:
            evidence = []
            hashes = {}
            for task in selected:
                evidence.extend(dict(task=task['id'], **item)
                                for item in verify_evidence(root, task, hashes))
            issues = nc.check_book(root, allow_pending=True, verify_snapshots=False)
            result['validation'] = dict(
                ok=all(item['ok'] for item in evidence) and not issues,
                evidence=evidence, book_issues=issues,
                scope='selected task evidence and live book/index checks; full snapshots excluded')
            result['ok'] = result['validation']['ok']
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--book', required=True)
    parser.add_argument('--stage')
    parser.add_argument('--task', action='append', help='exact task ID or unique kind; repeat to select several')
    parser.add_argument('--verify', action='store_true', help='verify selected hashes and live book/index integrity')
    args = parser.parse_args(argv)
    try:
        result = peek(args.book, args.stage, args.task, args.verify)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 1
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps(dict(ok=False, read_ok=False, error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
