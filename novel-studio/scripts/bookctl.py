#!/usr/bin/env python3
"""Read-only whole-book finalization evidence checks; standard library only.

manifest --book DIR returns {ok:true, chapters:[{number,path,sha256}]} for all
current formal chapters, sorted by number. Requires nonempty chapters and a
passing chapterctl.check, including completed material synchronization.

check --book DIR [--record RELATIVE] validates that same current chapter list
against 审稿与修订/全书定稿记录.json (or another JSON file in 审稿与修订).
Record schema:
{schema_version:1, chapters:[{number,path,sha256}],
 reviewer:{task_id,agent_id}, reading_scope:"full", verdict:"ready",
 reading_report:{path,sha256}, revision_report:{path,sha256}, task_stage?:str}

The chapter list must match exactly, including order. Both reports must be
nonempty UTF-8 Markdown files inside 审稿与修订, with matching SHA-256 hashes.
They may name the same comprehensive report. The declared reviewer must differ
from every current chapter writer by both agent_id and task_id.

This optional finalization gate is separate from ordinary chapter adoption,
chapterctl.check and export. Neither command writes state or evidence. They
compare inventories before/after validation to detect concurrent changes.
Managed books additionally require current whole-book task evidence for check;
manifest does not require finalization tasks or a finalization record.
JSON results and nonzero failure exits check file/version evidence only: the
host must verify actual reviewer identity, full reading, report meaning and
author agreement. No literary score, award certification or identity proof.
"""
import argparse
import json
from pathlib import Path

import chapterctl as cc
import novelctl as nc

REVIEW_DIRECTORY = '审稿与修订'
DEFAULT_RECORD = REVIEW_DIRECTORY + '/全书定稿记录.json'


def evidence_path(root, value, suffix):
    path = nc.relative_path(root, value)
    parts = Path(value).parts
    cc.require(len(parts) >= 2 and parts[0] == REVIEW_DIRECTORY,
               'evidence must be inside ' + REVIEW_DIRECTORY)
    cc.require(path.suffix.lower() == suffix, 'evidence must be ' + suffix)
    cc.require(path.is_file(), 'missing evidence file: ' + value)
    return path


def validate_record(root, name, state, chapters):
    record = cc.read_json(evidence_path(root, name, '.json'))
    cc.require(type(record.get('schema_version')) is int and
               record['schema_version'] == 1, 'record schema_version must be 1')
    declared = record.get('chapters')
    cc.require(isinstance(declared, list) and all(
        isinstance(ch, dict) and set(ch) == {'number', 'path', 'sha256'} and
        type(ch.get('number')) is int for ch in declared),
        'invalid record chapter list')
    cc.require(declared == chapters, 'record chapters do not match current book')
    reviewer = cc.obj(record.get('reviewer'))
    cc.require(all(cc.nonempty(reviewer.get(k)) for k in ('task_id', 'agent_id')),
               'invalid whole-book reviewer identity')
    cc.require(all(reviewer['task_id'] != ch['writer_task'] and
                   reviewer['agent_id'] != ch['writer_agent']
                   for ch in state['chapters']),
               'writer cannot review own whole book')
    cc.require(record.get('reading_scope') == 'full', 'reading_scope must be full')
    cc.require(record.get('verdict') == 'ready', 'whole-book verdict must be ready')
    for field in ('reading_report', 'revision_report'):
        report = cc.obj(record.get(field))
        path = evidence_path(root, report.get('path'), '.md')
        cc.require(bool(path.read_text(encoding='utf-8').strip()),
                   'empty ' + field)
        cc.require(nc.digest(path) == report.get('sha256'),
                   field + ' sha256 mismatch')
    return record


def inspect_book(book, record=None, *, verify_snapshots=True):
    cc.require(record is None or verify_snapshots,
               'snapshot verification is required for whole-book finalization')
    root = nc.root_path(book)
    before = nc.inventory(root, skip_snapshots=not verify_snapshots)
    try:
        checked = (cc.check(root) if verify_snapshots else
                   cc.check(root, verify_snapshots=False))
        cc.require(checked['ok'], 'chapterctl check failed: ' + '; '.join(checked['issues']))
        state = nc.load_state(root)
        cc.require(bool(state['chapters']), 'book has no formal chapters')
        chapters = [{key: ch[key] for key in ('number', 'path', 'sha256')}
                    for ch in sorted(state['chapters'], key=lambda ch: ch['number'])]
        if record is not None:
            validated = validate_record(root, record, state, chapters)
            import taskctl
            taskctl.require_finalization(root, validated)
        result = dict(ok=True, chapters=chapters)
        if not verify_snapshots:
            result['snapshots_verified'] = False
        if record is not None:
            result['record'] = record
        return result
    finally:
        cc.require(nc.inventory(root, skip_snapshots=not verify_snapshots) == before,
                   'book changed during validation')


def manifest(book, *, verify_snapshots=True):
    return inspect_book(book, verify_snapshots=verify_snapshots)


def check(book, record=DEFAULT_RECORD):
    cc.require(cc.nonempty(record), 'invalid record path')
    return inspect_book(book, record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('manifest', 'check'):
        sub = subs.add_parser(command)
        sub.add_argument('--book', required=True)
        if command == 'manifest':
            sub.add_argument('--skip-snapshots', action='store_true',
                             help='skip snapshot payloads in this read-only manifest')
        if command == 'check':
            sub.add_argument('--record', default=DEFAULT_RECORD,
                             help='book-relative JSON evidence path inside 审稿与修订')
    args = parser.parse_args(argv)
    try:
        result = (manifest(args.book, verify_snapshots=not args.skip_snapshots) if args.command == 'manifest'
                  else check(args.book, args.record))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
