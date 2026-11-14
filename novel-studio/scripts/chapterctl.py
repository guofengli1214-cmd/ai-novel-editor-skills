#!/usr/bin/env python3
"""Mechanical chapter adoption gates; standard library only.

Subagents: adopt/resume/finish --book DIR --request JSON.
Main agent: check --book DIR (read only). JSON is an inline object or @file.
Host MUST independently compare actual writer/reviewer agent identities: JSON
identities and authorizations are assertions, not authenticated credentials.

adopt: {number,target,draft,expected_sha256,writer:{task_id,agent_id},reviews:[],
authorization:{mode,source,approved_sha256?},change_record,task_stage?,prose_check?}. Modes: per_chapter,
trial (approved_sha256 required), continuous; Chinese aliases 逐章/试写/连续.
source is a nonempty authorization provenance string. Paths are book-relative.
Managed books require taskctl adoption/synchronization gates. task_stage selects
the current chapter/trial stage; otherwise taskctl matches it by chapter number.
Review: {schema_version:1,writer_task,reviewer:{task_id,agent_id},draft_sha256,
coverage:[literary,continuity],verdict:pass|revise|blocked,
issues:[{id,severity,description,evidence,status}]}. severity=hard|soft|info; status=open|resolved|waived; hard requires resolved.
New workflow-v2 stages require review schema 2: reviewed_draft/current_draft
{path,sha256}, reading {mode:full|incremental,scope}, report {path,sha256}, and
issue priority major|optional. Major retention needs authorization, reason and
evidence. Incremental reading needs a full schema-2 baseline, actual change map
and a new reading report; different writer tasks require a full current read.
prose_check {path,sha256} binds the exact result recomputed with the prepared
policy. Adopted v2 chapters retain fixed policy/report evidence for later check.
diff --book DIR --old REL --new REL --summary TEXT prints a canonical change-map
JSON without writing files. See references/task-management.md and file-protocol.md.
finish: {operation_id}. The adopt change_record JSON must contain draft_sha256,
categories (all CATEGORIES: updated|unchanged; pending is rejected;
summaries/history/indexes/handoff MUST be updated after every adoption),
checked_sources:[relative paths], updated_files:[{path,sha256}],
category_files:{updated_category:[paths in updated_files]},
handoff:{path,chapter:int}. Handoff text includes 第N章, 第000N章 or formal filename.

Backups and durable journal live under 历史版本/chapter-<operation_id>.
Pending contains the full request, hashes and backup paths. Any pending blocks
adopt, including retries. resume: {operation_id} validates a checksum archive,
the original state, current evidence and adoption task before completing the same
interrupted installation; it never creates a new operation. finish only accepts
an installed chapter. Never manufacture
subagent completion records. Concurrent cooperating writers are serialized;
Stale locks require unlock with confirm_no_writer:true and expected_pending:[IDs].
Unlock preserves pending operations and their journals; recovery remains explicit.
quarantine --request {operation_id,confirm_unused:true,reason,verification:{agent_id,source},expected_pending?:[IDs]}
preserves an unreferenced orphan archive in a unique sibling directory. It scans
state, complete ledgers, other archives and snapshots, and rejects any real reference
to the target; pending IDs must exactly match expected_pending (default []).
Only generated index links are ignored and refreshed.
New Windows locks bind the owner PID to its process creation time. Legacy locks
without that identity retain the conservative live-PID check; no force bypass.
Other tools/agents must not mutate the book during either writing command.

Minimal JSON examples; replace H with actual draft SHA-256:
adopt: {"number":1,"target":"第1章.md","draft":"草稿与备选/1.md",
"expected_sha256":null,"writer":{"task_id":"w1","agent_id":"a1"},
"reviews":["审稿与修订/1.json"],"authorization":{"mode":"trial",
"source":"author approval reference","approved_sha256":"H"},
"change_record":"连贯性记录/1.json"}
review: {"schema_version":1,"writer_task":"w1","reviewer":{"task_id":"r1",
"agent_id":"a2"},"draft_sha256":"H","coverage":["literary","continuity"],
"verdict":"pass","issues":[]}
finish: {"operation_id":"ID returned by adopt"}
change_record: {"draft_sha256":"H","categories":{"intent":"unchanged",
"characters":"unchanged","growth":"unchanged","relations":"unchanged",
"world":"unchanged","storylines":"unchanged","events":"unchanged",
"clues":"unchanged","knowledge":"unchanged","resources":"unchanged",
"curve":"unchanged","pacing":"unchanged","summaries":"updated",
"history":"updated","indexes":"updated","handoff":"updated"},
"checked_sources":["第1章.md"],"updated_files":[{"path":"创作约定/交接.md",
"sha256":"actual handoff hash"},{"path":"摘要/第1章.md","sha256":"actual summary hash"},
{"path":"历史版本/采用记录.md","sha256":"actual history hash"},
{"path":"索引.md","sha256":"actual index hash"}],
"category_files":{"summaries":["摘要/第1章.md"],"history":["历史版本/采用记录.md"],
"indexes":["索引.md"],"handoff":["创作约定/交接.md"]},
"handoff":{"path":"创作约定/交接.md","chapter":1}}
"""
import argparse
import copy
from contextlib import contextmanager
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

import novelctl as nc

CATEGORIES = ('intent characters growth relations world storylines events clues '
              'knowledge resources curve pacing summaries history indexes handoff').split()
REQUIRED_UPDATED = ('summaries', 'history', 'indexes', 'handoff')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def obj(value):
    require(isinstance(value, dict), 'expected JSON object')
    return value


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def read_json(path):
    return obj(json.loads(path.read_text(encoding='utf-8-sig')))


def save(path, value):
    nc.atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    # Windows 不能以 os.open 打开目录做 fsync（PermissionError），只在 POSIX 上同步目录项。
    if os.name == 'nt':
        return
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def file_path(root, value):
    path = nc.relative_path(root, value)
    require(path.is_file(), 'missing regular file: ' + str(value))
    return path


def lock_path(root):
    # Outside the book so this implementation detail never enters its indexes.
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
    return root.parent / ('.chapterctl-' + key + '.lock')


def windows_process_state(pid, *, include_start=False):
    """Read liveness and optional creation FILETIME from the same query handle."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: no process with this PID.
            return False, None
        if error == 5:  # ERROR_ACCESS_DENIED: conservatively assume alive.
            return True, None
        raise ValueError('cannot verify lock owner PID: Windows error ' + str(error))
    try:
        code = wintypes.DWORD()
        require(kernel.GetExitCodeProcess(handle, ctypes.byref(code)), 'cannot query lock owner PID')
        if code.value != 259:  # STILL_ACTIVE
            return False, None
        if not include_start:
            return True, None
        created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        require(kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                       ctypes.byref(kernel_time), ctypes.byref(user_time)),
                'cannot query lock owner process creation time')
        started = (created.dwHighDateTime << 32) | created.dwLowDateTime
        require(started > 0, 'invalid lock owner process creation time')
        return True, started
    finally:
        kernel.CloseHandle(handle)


def process_alive(pid, *, expected_start_time=None):
    require(type(pid) is int and 0 < pid <= 0xFFFFFFFF, 'invalid lock owner PID; inspect lock manually')
    if expected_start_time is not None:
        require(type(expected_start_time) is int and 0 < expected_start_time < 2**64,
                'invalid lock owner process_start_time; inspect lock manually')
    if os.name == 'nt':
        alive, started = windows_process_state(pid, include_start=expected_start_time is not None)
        # Access denied (no start time) is not evidence that the owner exited.
        return alive and (expected_start_time is None or started is None or
                          started == expected_start_time)
    require(expected_start_time is None, 'cannot verify Windows process creation time on this platform')
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        raise ValueError('invalid lock owner PID; inspect lock manually')
    return True


@contextmanager
def locked(root):
    lock = lock_path(root)
    recovery = lock.with_name(lock.name + '.unlock')
    require(not recovery.exists(), 'lock recovery in progress: ' + str(recovery))
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError('writer lock exists: ' + str(lock) + '; stop all writers, inspect pending_operations, then use chapterctl unlock --request with confirm_no_writer:true and expected_pending:[operation IDs]')
    identity = lock.stat()
    owner = dict(pid=os.getpid(), book=str(root), token=uuid.uuid4().hex)
    saved = False
    try:
        if os.name == 'nt':
            alive, started = windows_process_state(owner['pid'], include_start=True)
            require(alive and started is not None, 'cannot record lock owner process creation time')
            owner['process_start_time'] = started
        save(lock / 'owner.json', owner)
        saved = True
        # A recovery may have claimed an empty lock between mkdir and save.
        require(not recovery.exists(), 'lock recovery in progress: ' + str(recovery))
        current = lock.stat()
        require((current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
                and read_json(lock / 'owner.json') == owner, 'writer lock changed during acquisition')
        yield
    finally:
        if lock.exists() and not nc.is_link_or_reparse(lock):
            current = lock.stat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                owner_file = lock / 'owner.json'
                if owner_file.is_file() and not nc.is_link_or_reparse(owner_file) and read_json(owner_file) == owner:
                    owner_file.unlink()
                    lock.rmdir()
                elif not saved and not any(lock.iterdir()):
                    lock.rmdir()


def unlock(book, request):
    """Release an abandoned writer lock without altering chapter recovery data."""
    root = nc.root_path(book)
    req = obj(request)
    require(req.get('confirm_no_writer') is True, 'unlock requires confirm_no_writer:true after stopping all writers')
    expected = req.get('expected_pending')
    require(isinstance(expected, list) and all(nonempty(x) for x in expected)
            and len(set(expected)) == len(expected), 'expected_pending must list current operation IDs, or []')
    lock = lock_path(root)
    recovery = lock.with_name(lock.name + '.unlock')
    try:
        recovery.mkdir()
    except FileExistsError:
        raise ValueError('lock recovery in progress: ' + str(recovery) + '; inspect it manually if abandoned')
    try:
        pending = nc.load_state(root)['pending_operations']
        actual = [obj(item).get('operation_id') for item in pending]
        require(all(nonempty(x) for x in actual) and sorted(expected) == sorted(actual),
                'expected_pending does not match current pending_operations')
        require(lock.exists() and not nc.is_link_or_reparse(lock) and lock.is_dir(), 'writer lock missing or unsafe')
        entries = list(lock.iterdir())
        require(all(p.name == 'owner.json' for p in entries), 'unknown lock files; inspect manually without deleting recovery data')
        owner_file = lock / 'owner.json'
        if entries:
            require(not nc.is_link_or_reparse(owner_file) and owner_file.is_file(), 'unsafe lock owner file')
            owner = read_json(owner_file)
            require(owner.get('book') == str(root), 'lock owner book mismatch')
            require('process_start_time' not in owner or owner['process_start_time'] is not None,
                    'invalid lock owner process_start_time; inspect lock manually')
            require(not process_alive(owner.get('pid'), expected_start_time=owner.get('process_start_time')),
                    'lock owner PID is alive; stop writer before unlocking')
            owner_file.unlink()
        # Never recursively remove unknown files. Concurrent recovery attempts
        # are excluded by the sibling guard, including for an ownerless lock.
        lock.rmdir()
        return dict(ok=True, unlocked=True, pending_operations=actual,
                    recovery_notice='Pending operations and journals are unchanged; recover or finish them before adopting.')
    finally:
        recovery.rmdir()


def quarantine(book, request):
    """Preserve an unreferenced orphan outside the work; never waive evidence."""
    root = nc.root_path(book)
    req = obj(request)
    operation = req.get('operation_id')
    require(isinstance(operation, str) and re.fullmatch('[0-9a-f]{32}', operation),
            'invalid adoption operation_id')
    require(req.get('confirm_unused') is True and nonempty(req.get('reason')),
            'quarantine requires confirm_unused:true and a reason')
    verification = obj(req.get('verification'))
    require(nonempty(verification.get('agent_id')) and nonempty(verification.get('source')),
            'quarantine requires host verification provenance')
    expected = req.get('expected_pending', [])
    require(isinstance(expected, list) and all(nonempty(value) for value in expected)
            and len(set(expected)) == len(expected), 'invalid expected_pending operation IDs')
    with locked(root):
        state = nc.load_state(root)
        actual = [obj(item).get('operation_id') for item in state['pending_operations']]
        require(all(nonempty(value) for value in actual) and sorted(actual) == sorted(expected),
                'pending operations do not match expected_pending; inspect and explicitly list all IDs')
        require(operation not in actual, 'cannot quarantine a pending operation')
        source_rel = nc.HISTORY + '/chapter-' + operation
        source = nc.relative_path(root, source_rel)
        require(source.is_dir(), 'adoption archive directory missing')
        before = nc.inventory(root)
        references = []
        token = operation.encode('ascii')
        for name in before[0]:
            if name == source_rel or name.startswith(source_rel + '/'):
                continue
            path = nc.relative_path(root, name)
            data = path.read_bytes()
            if path.name == '索引.md':
                # Generated discovery links are not proof of an adopted result;
                # all author annotations, ledgers and snapshot manifests count.
                text = data.decode('utf-8')
                if nc.BEGIN in text and nc.END in text:
                    text = text[:text.index(nc.BEGIN)] + text[text.index(nc.END) + len(nc.END):]
                data = text.encode('utf-8')
            if token in data:
                references.append(name)
        require(not references, 'adoption archive is still referenced: ' + ', '.join(references))
        legacy = [chapter for chapter in state['chapters'] if not chapter.get('adoption_operation_id')]
        if legacy:
            try:
                journal = read_json(source / 'journal.json')
            except (ValueError, OSError, TypeError, KeyError) as exc:
                raise ValueError('cannot prove archive unused while legacy formal chapters lack operation pointers') from exc
            require(not any(chapter['sha256'] == journal.get('draft_sha256') and
                            (chapter['number'] == journal.get('number') or chapter['path'] == journal.get('target'))
                            for chapter in legacy),
                    'adoption archive is still referenced or ambiguous for a legacy formal chapter')
        history_index = nc.relative_path(root, nc.HISTORY + '/索引.md')
        if history_index.exists():
            text = history_index.read_text(encoding='utf-8')
            require(text.count(nc.BEGIN) == text.count(nc.END) and text.count(nc.BEGIN) <= 1
                    and (nc.BEGIN not in text or text.index(nc.BEGIN) < text.index(nc.END)),
                    'malformed history index; preserve and repair before quarantine')
        preserved = nc.inventory(source)
        destination = root.parent / ('.' + root.name + '-quarantine-' + uuid.uuid4().hex)
        destination.mkdir()
        receipt = dict(schema_version=1, kind='unreferenced_adoption_quarantine',
                       book=str(root), operation_id=operation, original_path=source_rel,
                       reason=req['reason'], verification=verification,
                       files=preserved[0], directories=preserved[1])
        save(destination / 'quarantine.json', receipt)
        require(nc.inventory(root) == before, 'book changed during quarantine verification')
        os.rename(source, destination / source.name)
        require(nc.inventory(destination / source.name) == preserved,
                'quarantine copy changed; preserved evidence requires inspection')
        try:
            nc.index_directory(root, root / nc.HISTORY)
        except (ValueError, OSError) as exc:
            raise ValueError('archive preserved at ' + str(destination) + '; refresh history index before continuing: ' + str(exc)) from exc
        return dict(ok=True, operation_id=operation, pending_operations=actual, quarantine=str(destination),
                    receipt=str(destination / 'quarantine.json'),
                    notice='Archive preserved outside work; no authorizations or completion records were created.')


def evidence_file(root, value, label):
    value = obj(value)
    require(nonempty(value.get('path')) and isinstance(value.get('sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', value['sha256']), 'invalid ' + label + ' evidence')
    path = file_path(root, value['path'])
    require(bool(path.read_bytes().strip()), 'empty ' + label + ' evidence')
    require(nc.digest(path) == value['sha256'], 'stale ' + label + ' evidence')
    return path


def journal_digest(journal):
    """The phase may advance after installation; all other journal data is fixed."""
    stable = {key: value for key, value in journal.items() if key != 'phase'}
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def validate_adoption_archive(root, operation_id, pending=None):
    """Validate new checksum archives and the hashes available in legacy journals.

    No live chapter, review or material file is read: a later legitimate revision
    must not invalidate an earlier operation's immutable recovery evidence.
    """
    require(isinstance(operation_id, str) and re.fullmatch('[0-9a-f]{32}', operation_id),
            'invalid adoption operation_id')
    base = nc.HISTORY + '/chapter-' + operation_id
    archive = nc.relative_path(root, base)
    journal = read_json(file_path(root, base + '/journal.json'))
    require(journal.get('operation_id') == operation_id and journal.get('kind') == 'chapter_adopt',
            'adoption journal identity mismatch')
    require(journal.get('phase') in ('prepared', 'adopted', 'completed'), 'invalid adoption journal phase')
    require(journal.get('backup') == base and journal.get('state_backup') == base + '/state.json'
            and journal.get('draft_backup') == base + '/draft.md', 'adoption backup paths mismatch')
    number, target, sha = journal.get('number'), journal.get('target'), journal.get('draft_sha256')
    require(type(number) is int and number > 0, 'invalid archived chapter number')
    destination = nc.relative_path(root, target)
    require(destination.parent == root and destination.suffix.lower() == '.md'
            and destination.name != '索引.md', 'invalid archived chapter target')
    require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'invalid archived draft hash')
    req = obj(journal.get('request'))
    require(req.get('number') == number and req.get('target') == target,
            'archived request chapter mismatch')
    require(journal.get('task_stage') == req.get('task_stage'), 'archived request stage mismatch')
    require(req.get('expected_sha256') == journal.get('original_sha256'),
            'archived expected chapter hash mismatch')
    hashes = obj(journal.get('review_hashes'))
    require(isinstance(req.get('reviews'), list) and bool(hashes)
            and set(hashes) == set(req['reviews'])
            and all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                    for value in hashes.values()), 'invalid archived review hashes')
    require(nc.digest(file_path(root, base + '/draft.md')) == sha, 'archived draft hash mismatch')
    prior = nc.validate_state(root, read_json(file_path(root, base + '/state.json')))
    require(not prior['pending_operations'], 'archived prior state already has pending operations')
    old = next((chapter for chapter in prior['chapters'] if chapter['number'] == number), None)
    expected_files = {'state.json', 'draft.md'}
    if journal.get('original_sha256') is None:
        require(old is None and not (archive / 'original.md').exists(),
                'new chapter archive contains an original chapter')
    else:
        require(old is not None and old['path'] == target
                and old['sha256'] == journal['original_sha256'], 'archived original state mismatch')
        require(nc.digest(file_path(root, base + '/original.md')) == journal['original_sha256'],
                'archived original hash mismatch')
        expected_files.add('original.md')
    manifest_path = archive / 'archive.json'
    if journal.get('archive_version') is not None or manifest_path.exists():
        require(journal.get('archive_version') == 1, 'invalid adoption archive_version')
        manifest = read_json(file_path(root, base + '/archive.json'))
        require(manifest.get('schema_version') == 1 and manifest.get('kind') == 'chapter_adoption_archive'
                and manifest.get('operation_id') == operation_id, 'invalid adoption archive manifest')
        files = obj(manifest.get('files'))
        require(set(files) == expected_files, 'adoption archive manifest file set mismatch')
        for name, expected in files.items():
            require(nc.digest(file_path(root, base + '/' + name)) == expected,
                    'adoption archive file hash mismatch: ' + name)
        require(manifest.get('journal_sha256') == journal_digest(journal),
                'adoption journal does not match fixed manifest')
    if pending is not None:
        require(journal_digest(journal) == journal_digest(obj(pending)),
                'adoption journal does not match pending operation')
        require(pending.get('phase') in ('prepared', 'adopted'), 'invalid pending adoption phase')
        require(not (pending['phase'] == 'prepared' and journal['phase'] != 'prepared'),
                'adoption journal is ahead of pending operation')
    return journal


def validate_sync_receipt(root, operation_id):
    """Return immutable finish evidence without consulting taskctl or live materials."""
    journal = validate_adoption_archive(root, operation_id)
    base = journal['backup']
    receipt = read_json(file_path(root, base + '/completion.json'))
    require(receipt.get('schema_version') == 1 and receipt.get('kind') == 'chapter_sync_completed',
            'invalid synchronization completion receipt')
    for key in ('operation_id', 'number', 'target', 'draft_sha256', 'task_stage'):
        require(receipt.get(key) == journal.get(key), 'synchronization receipt mismatch: ' + key)
    require(journal['phase'] in ('adopted', 'completed'), 'completed synchronization requires adopted journal')
    require(receipt.get('journal_sha256') == journal_digest(journal),
            'synchronization receipt journal hash mismatch')
    manifest = nc.relative_path(root, base + '/archive.json')
    require(receipt.get('archive_manifest_sha256') == (nc.digest(manifest) if manifest.exists() else None),
            'synchronization receipt archive hash mismatch')
    record_ref = obj(receipt.get('change_record'))
    require(record_ref.get('path') == base + '/synchronization.json',
            'synchronization receipt requires the fixed change record copy')
    record = read_json(evidence_file(root, record_ref, 'archived synchronization record'))
    require(record.get('draft_sha256') == journal['draft_sha256'],
            'archived synchronization draft mismatch')
    require(receipt.get('source_change_record') == dict(path=journal['request']['change_record'],
                                                      sha256=record_ref['sha256']),
            'synchronization source record mismatch')
    return receipt


def save_fixed(path, data):
    """A retry may reuse identical durable output, but never replace old evidence."""
    encoded = data if isinstance(data, bytes) else data.encode('utf-8')
    if path.exists():
        require(path.read_bytes() == encoded, 'fixed adoption evidence already differs: ' + path.name)
    else:
        nc.atomic_write(path, encoded)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)


def legacy_scope_consistent(review):
    """Only compare explicit version fields, never claim to parse prose."""
    scope = review.get('version_scope')
    if isinstance(scope, dict):
        reviewed, current = scope.get('verdict_version'), scope.get('adopted_version')
        if nonempty(reviewed) and nonempty(current):
            old = re.search(r'\bv\d+\b', reviewed, re.I)
            new = re.search(r'\bv\d+\b', current, re.I)
            require(not (old and new) or old.group().lower() == new.group().lower(),
                    'review version_scope contradicts adopted version; independent current review required')


def draft_diff(root, old, current):
    """Canonical diff binds the change map to the actual old and new bytes."""
    old_text = file_path(root, old['path']).read_bytes().decode('utf-8')
    new_text = file_path(root, current['path']).read_bytes().decode('utf-8')
    return ''.join(difflib.unified_diff(old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
                                       fromfile=old['path'], tofile=current['path'], lineterm='\n'))


def change_map(book, old, new, summary):
    import taskctl
    require(nonempty(summary), 'change map requires a summary')
    with taskctl.reading(book) as root:
        old_path, new_path = file_path(root, old), file_path(root, new)
        baseline = dict(path=old, sha256=nc.digest(old_path))
        current = dict(path=new, sha256=nc.digest(new_path))
        return dict(old_draft=baseline, new_draft=current, summary=summary,
                    diff=draft_diff(root, baseline, current))


def validate_review_scope(root, review, sha, seen, writer):
    reviewed = obj(review.get('reviewed_draft'))
    current = obj(review.get('current_draft'))
    evidence_file(root, reviewed, 'reviewed draft')
    evidence_file(root, current, 'current draft')
    require(reviewed == current and current['sha256'] == sha,
            'reviewed/current draft must both identify the current version')
    report = obj(review.get('report'))
    evidence_file(root, report, 'reading report')
    require(report['path'] not in (reviewed['path'], current['path']), 'reading report cannot be the draft')
    reading = obj(review.get('reading'))
    require(reading.get('mode') in ('full', 'incremental') and nonempty(reading.get('scope')),
            'review requires explicit reading mode and scope')
    if reading['mode'] == 'incremental':
        base_ref = obj(review.get('base_review'))
        base_path = evidence_file(root, base_ref, 'base review')
        require(base_ref['path'] not in seen, 'cyclic review provenance')
        base = read_json(base_path)
        require(base.get('schema_version') == 2 and obj(base.get('reading')).get('mode') == 'full',
                'incremental review needs a schema 2 complete-reading baseline')
        baseline = obj(review.get('base_draft'))
        evidence_file(root, baseline, 'base draft')
        require(baseline.get('sha256') != sha, 'incremental review requires different old and new versions')
        require(base.get('draft_sha256') == baseline.get('sha256') and base.get('current_draft') == baseline,
                'incremental baseline version mismatch')
        require(base.get('writer_task') == review.get('writer_task'), 'incremental baseline writer task mismatch')
        identity = obj(base.get('reviewer'))
        require(nonempty(identity.get('task_id')) and nonempty(identity.get('agent_id')), 'missing baseline reviewer')
        require(base.get('verdict') == 'pass', 'incremental baseline was not passed')
        validate_reviews(root, [base_ref['path']], writer, baseline['sha256'])
        change = obj(review.get('change_map'))
        change_path = evidence_file(root, change, 'change map')
        require(change['path'] not in (report['path'], base_ref['path'], reviewed['path'], baseline['path']),
                'change map must be a separate evidence document')
        mapping = read_json(change_path)
        require(mapping.get('old_draft') == baseline and mapping.get('new_draft') == current,
                'change map does not bind old and new versions')
        require(mapping.get('diff') == draft_diff(root, baseline, current),
                'change map diff does not match actual old and new drafts')
        require(nonempty(mapping.get('summary')) and nonempty(reading.get('related_scope')),
                'incremental review needs change summary and related reading scope')
        require(report['path'] != obj(base.get('report')).get('path') and report['sha256'] != base['report']['sha256'],
                'incremental review requires a new reviewer report')


def validate_reviews(root, reviews, writer, sha):
    obj(writer)
    require(all(nonempty(writer.get(k)) for k in ('task_id', 'agent_id')), 'invalid writer identity')
    require(isinstance(reviews, list) and bool(reviews), 'missing reviews')
    coverage, reviewers = set(), []
    for name in reviews:
        review = read_json(file_path(root, name))
        require(type(review.get('schema_version')) is int and review['schema_version'] in (1, 2), 'review schema_version')
        identity = obj(review.get('reviewer'))
        require(all(nonempty(identity.get(k)) for k in ('task_id', 'agent_id')), 'invalid reviewer identity')
        require(identity['agent_id'] != writer['agent_id'] and identity['task_id'] != writer['task_id'], 'writer cannot review own draft')
        require(review.get('writer_task') == writer['task_id'] and review.get('draft_sha256') == sha, 'stale review')
        require(review.get('verdict') == 'pass', 'review not pass')
        legacy_scope_consistent(review)
        if review['schema_version'] == 2:
            validate_review_scope(root, review, sha, {name}, writer)
        areas = review.get('coverage')
        require(isinstance(areas, list) and all(a in ('literary', 'continuity') for a in areas), 'invalid coverage')
        coverage.update(areas)
        issues = review.get('issues')
        require(isinstance(issues, list), 'missing issues')
        for issue in issues:
            obj(issue)
            require(all(nonempty(issue.get(k)) for k in ('id', 'severity', 'description', 'evidence', 'status')), 'invalid issue')
            require(issue['severity'] in ('hard', 'soft', 'info'), 'unknown issue severity')
            require(issue['status'] in ('open', 'resolved', 'waived'), 'unknown issue status')
            require(issue['severity'] != 'hard' or issue['status'] == 'resolved', 'unresolved hard issue')
            if review['schema_version'] == 2:
                require(issue.get('priority') in ('major', 'optional'), 'schema 2 issues require major/optional priority')
                if issue['priority'] == 'major':
                    require(issue['status'] in ('resolved', 'waived'), 'unresolved major reading issue')
                    if issue['status'] == 'waived':
                        authorization = obj(issue.get('retention_authorization'))
                        require(nonempty(authorization.get('source')) and nonempty(authorization.get('scope')),
                                'major issue retention requires author authorization provenance')
                        require(nonempty(issue.get('retention_reason')),
                                'major issue retention requires a reading-impact reason')
                        evidence_file(root, issue.get('retention_evidence'), 'major issue retention')
        reviewers.append(identity)
    require(coverage == {'literary', 'continuity'}, 'missing literary/continuity coverage')
    return reviewers


def validate_prose_check(root, req, sha, stage_id):
    import taskctl
    if stage_id is None:
        return
    data = taskctl.load_plan(root)
    stage = next(stage for stage in data['stages'] if stage['id'] == stage_id)
    if taskctl.workflow_version(stage) < 2:
        return
    prepared = taskctl.selected_task(stage, 'prepare')['checks']['prose_policy']
    policy = {key: prepared[key] for key in ('path', 'sha256')}
    report_ref = obj(req.get('prose_check'))
    validate_prose_evidence(root, req['draft'], sha, policy, report_ref)
    return policy


def validate_prose_evidence(root, draft, sha, policy, report_ref):
    policy_path = evidence_file(root, policy, 'prepared prose policy')
    report = read_json(evidence_file(root, report_ref, 'prose check report'))
    import prosecheck
    result = prosecheck.check_text(file_path(root, draft).read_bytes().decode('utf-8'), read_json(policy_path))
    require(report == dict(draft_sha256=sha, policy=policy, result=result),
            'prose_check report differs from current draft/policy/recomputed result')
    require(result['ok'], 'current prose violates confirmed mechanical policy')


def adopt(book, request):
    root = nc.root_path(book)
    with locked(root):
        return _adopt(root, obj(request))


def adopted_state(prior, pending, reviewers, prose_policy):
    """Reconstruct exactly the state installed by this immutable request."""
    state, pending = copy.deepcopy(prior), copy.deepcopy(pending)
    req = pending['request']
    old = next((chapter for chapter in state['chapters'] if chapter['number'] == pending['number']), None)
    writer = req['writer']
    entry = dict(old or {})
    entry.update(number=pending['number'], path=pending['target'], sha256=pending['draft_sha256'],
                 adoption_operation_id=pending['operation_id'],
                 writer_task=writer['task_id'], writer_agent=writer['agent_id'],
                 review_path=req['reviews'][0], reviewer_task=reviewers[0]['task_id'],
                 review_paths=req['reviews'], reviewers=reviewers, authorization=req['authorization'],
                 review_hashes=pending['review_hashes'])
    if pending.get('task_stage') is not None:
        entry['task_stage'] = pending['task_stage']
        entry['workflow_version'] = req['workflow_version']
        if req['workflow_version'] >= 2:
            entry['prose_check'] = req['prose_check']
            entry['prose_policy'] = prose_policy
    state['chapters'] = [chapter for chapter in state['chapters'] if chapter['number'] != pending['number']] + [entry]
    state['chapters'].sort(key=lambda chapter: chapter['number'])
    pending['phase'] = 'adopted'
    state['pending_operations'] = [pending]
    return state


def resume(book, request):
    """Complete a prepared installation using its original operation and evidence."""
    root = nc.root_path(book)
    req = obj(request)
    with locked(root):
        before = nc.inventory(root)
        state = nc.load_state(root)
        operations = state['pending_operations']
        require(len(operations) == 1, 'resume requires one pending adoption')
        pending = obj(operations[0])
        operation = req.get('operation_id')
        require(pending.get('kind') == 'chapter_adopt' and pending.get('operation_id') == operation,
                'resume operation mismatch')
        journal = validate_adoption_archive(root, operation, pending=pending)
        require(journal.get('archive_version') == 1,
                'resume requires a checksum adoption archive; recover legacy operations from a verified snapshot')
        require(pending['phase'] in ('prepared', 'adopted') and journal['phase'] != 'completed',
                'installation already completed; continue synchronization with finish')
        original = pending['request']
        prior = read_json(file_path(root, pending['state_backup']))
        target = nc.relative_path(root, pending['target'])
        target_sha = nc.digest(target) if target.is_file() else None
        require(not target.exists() or target.is_file(), 'resume target is not a regular file')
        if pending['phase'] == 'prepared':
            prepared = copy.deepcopy(prior)
            prepared['pending_operations'] = [pending]
            require(state == prepared, 'current state differs from the prepared adoption')
            require(target_sha in (pending['original_sha256'], pending['draft_sha256']),
                    'resume target changed outside the interrupted adoption; preserve manual edits first')
        else:
            require(target_sha == pending['draft_sha256'], 'installed chapter changed after adoption')
        draft = file_path(root, original.get('draft'))
        require(nc.digest(draft) == pending['draft_sha256'], 'resume draft changed after adoption started')
        reviewers = validate_reviews(root, original['reviews'], original['writer'], pending['draft_sha256'])
        require(all(nc.digest(file_path(root, name)) == sha for name, sha in pending['review_hashes'].items()),
                'review changed after adoption started')
        policy = None
        if original.get('workflow_version', 1) >= 2:
            report = read_json(evidence_file(root, original.get('prose_check'), 'prose check report'))
            policy = obj(report.get('policy'))
        installed = adopted_state(prior, pending, reviewers, policy)
        if pending['phase'] == 'adopted':
            require(state == installed, 'current state differs from the installed adoption')
        import taskctl
        task_stage = taskctl.require_adoption(root, original, resume_operation=operation)
        require(task_stage == pending.get('task_stage'), 'resume task stage differs from the original adoption')
        validated_policy = validate_prose_check(root, original, pending['draft_sha256'], task_stage)
        require(validated_policy == policy, 'resume prose policy differs from the original adoption')
        require(nc.inventory(root) == before, 'book changed during resume validation')
        if target_sha != pending['draft_sha256']:
            nc.atomic_write(target, file_path(root, pending['draft_backup']).read_bytes())
        if pending['phase'] == 'prepared':
            save(root / nc.STATE, installed)
        if journal['phase'] == 'prepared':
            save(nc.relative_path(root, pending['backup'] + '/journal.json'), installed['pending_operations'][0])
        return dict(ok=True, operation_id=operation, draft_sha256=pending['draft_sha256'],
                    pending=True, backup=pending['backup'], resumed=True)


def _adopt(root, req):
    req = dict(req)  # Add the selected task stage without mutating the caller.
    state = nc.load_state(root)
    require(not state['pending_operations'], 'pending operation exists; recover/finish first')
    nc.inventory(root)
    number = req.get('number')
    require(type(number) is int and number > 0, 'invalid number')
    target = nc.relative_path(root, req.get('target'))
    require(target.parent == root and target.suffix.lower() == '.md' and target.name != '索引.md', 'invalid target')
    draft = file_path(root, req.get('draft'))
    data = draft.read_bytes()
    require(bool(data.decode('utf-8').strip()), 'empty draft')
    sha = hashlib.sha256(data).hexdigest()
    old = next((c for c in state['chapters'] if c['number'] == number), None)
    require(not any(c['path'] == req['target'] and c['number'] != number for c in state['chapters']), 'target belongs to another chapter')
    if old:
        require(old['path'] == req['target'], 'cannot rename registered chapter')
        require(target.is_file() and nc.digest(target) == old['sha256'] == req.get('expected_sha256'), 'old chapter changed or stale expected_sha256')
    else:
        require(req.get('expected_sha256', 'missing') is None and not target.exists(), 'new chapter requires null expected_sha256 and absent target')
    sequence_issues = nc.chapter_sequence_issues(
        [ch for ch in state['chapters'] if ch['number'] != number] + [dict(number=number)])
    require(not sequence_issues, '; '.join(sequence_issues))
    writer = obj(req.get('writer'))
    require(all(nonempty(writer.get(k)) for k in ('task_id', 'agent_id')), 'invalid writer identity')
    auth = obj(req.get('authorization'))
    mode = auth.get('mode')
    require(mode in ('per_chapter', 'trial', 'continuous', '逐章', '试写', '连续'), 'invalid authorization mode')
    require(nonempty(auth.get('source')), 'missing authorization source')
    if mode in ('per_chapter', 'trial', '逐章', '试写'):
        require(auth.get('approved_sha256') == sha, 'authorization hash mismatch')
    reviews = req.get('reviews')
    require(isinstance(reviews, list) and bool(reviews), 'missing reviews')
    reviewers = validate_reviews(root, reviews, writer, sha)
    record = nc.relative_path(root, req.get('change_record'))
    require(record != target and record != root / nc.STATE and record != draft and req['change_record'] not in reviews, 'change_record conflicts with evidence')
    import taskctl
    task_stage = taskctl.require_adoption(root, req)
    prose_policy = validate_prose_check(root, req, sha, task_stage)
    if task_stage is not None:
        req['task_stage'] = task_stage
        stages = taskctl.load_plan(root)['stages']
        req['workflow_version'] = taskctl.workflow_version(next(stage for stage in stages if stage['id'] == task_stage))
    operation = uuid.uuid4().hex
    backup_rel = '历史版本/chapter-' + operation
    backup = nc.relative_path(root, backup_rel)
    backup.mkdir(parents=True)
    save(backup / 'state.json', state)
    if old:
        nc.atomic_write(backup / 'original.md', target.read_bytes())
    nc.atomic_write(backup / 'draft.md', data)
    pending = dict(operation_id=operation, kind='chapter_adopt', number=number,
                   target=req['target'], draft_sha256=sha, request=req,
                   review_hashes={name: nc.digest(file_path(root, name)) for name in reviews},
                   backup=backup_rel, original_sha256=old['sha256'] if old else None,
                   state_backup=backup_rel + '/state.json',
                   draft_backup=backup_rel + '/draft.md', phase='prepared', archive_version=1)
    if task_stage is not None:
        pending['task_stage'] = task_stage
    save(backup / 'journal.json', pending)
    names = ['state.json', 'draft.md'] + (['original.md'] if old else [])
    save(backup / 'archive.json', dict(schema_version=1, kind='chapter_adoption_archive',
                                     operation_id=operation,
                                     files={name: nc.digest(backup / name) for name in names},
                                     journal_sha256=journal_digest(pending)))
    state['pending_operations'] = [pending]
    save(root / nc.STATE, state)
    # Once pending is durable, failures are deliberately left recoverable.
    nc.atomic_write(target, data)
    state = adopted_state(state, pending, reviewers, prose_policy)
    pending = state['pending_operations'][0]
    save(root / nc.STATE, state)
    save(backup / 'journal.json', pending)
    return dict(ok=True, operation_id=operation, draft_sha256=sha, pending=True, backup=backup_rel)


def finish(book, request):
    root = nc.root_path(book)
    with locked(root):
        state = nc.load_state(root)
        pending = state['pending_operations']
        require(len(pending) == 1, 'expected one pending operation')
        p = obj(pending[0])
        require(p.get('kind') == 'chapter_adopt' and p.get('operation_id') == request.get('operation_id'), 'operation mismatch')
        require(p.get('phase') == 'adopted', 'interrupted adoption requires recovery')
        journal = validate_adoption_archive(root, p['operation_id'], pending=p)
        ch = next((c for c in state['chapters'] if c['number'] == p['number']), None)
        require(ch and ch['path'] == p['target'] and ch['sha256'] == p['draft_sha256'], 'chapter not installed')
        require(nc.digest(file_path(root, p['target'])) == p['draft_sha256'], 'chapter hash mismatch')
        req = p['request']
        validate_reviews(root, req['reviews'], req['writer'], p['draft_sha256'])
        if ch.get('workflow_version', 1) >= 2:
            validate_prose_evidence(root, ch['path'], ch['sha256'], ch.get('prose_policy'), ch.get('prose_check'))
        require(all(nc.digest(file_path(root, name)) == sha for name, sha in p['review_hashes'].items()), 'review changed after adoption')
        record = read_json(file_path(root, req['change_record']))
        require(record.get('draft_sha256') == p['draft_sha256'], 'change record hash mismatch')
        cats = obj(record.get('categories'))
        require(all(cats.get(k) in ('updated', 'unchanged') for k in CATEGORIES), 'missing/pending category')
        require(all(cats[k] == 'updated' for k in REQUIRED_UPDATED),
                'summaries/history/indexes/handoff must be updated')
        sources = record.get('checked_sources')
        require(isinstance(sources, list) and bool(sources), 'empty checked_sources')
        for name in sources:
            file_path(root, name)
        updates = record.get('updated_files')
        require(isinstance(updates, list), 'invalid updated_files')
        seen = set()
        for item in updates:
            obj(item)
            path = file_path(root, item.get('path'))
            require(item['path'] not in seen, 'duplicate updated file')
            seen.add(item['path'])
            require(nc.digest(path) == item.get('sha256'), 'updated file hash mismatch')
        evidence = obj(record.get('category_files', {}))
        for category in CATEGORIES:
            if cats[category] == 'updated':
                names = evidence.get(category)
                require(isinstance(names, list) and bool(names) and all(n in seen for n in names), 'updated category requires category_files evidence: ' + category)
        handoff = obj(record.get('handoff'))
        require(type(handoff.get('chapter')) is int and handoff['chapter'] == p['number'], 'handoff chapter mismatch')
        text = file_path(root, handoff.get('path')).read_text(encoding='utf-8')
        require(re.search(r'第0*' + str(p['number']) + r'章', text) is not None or p['target'] in text,
                'handoff does not name chapter')
        require(handoff['path'] in seen, 'handoff requires updated_files hash')
        before = nc.inventory(root)
        # Only pending itself is allowed; every other novelctl gate remains active.
        issues = nc.check_book(root, allow_pending=True)
        require(not issues, 'novelctl check failed: ' + '; '.join(issues))
        import taskctl
        taskctl.require_sync(root, p)
        require(nc.inventory(root) == before, 'book changed during finish')
        # Install fixed completion evidence before clearing pending. If clearing
        # state fails, the same finish request can reuse these identical files.
        backup = nc.relative_path(root, p['backup'])
        if journal['phase'] == 'prepared':
            save(backup / 'journal.json', p)
        record_path = file_path(root, req['change_record'])
        save_fixed(backup / 'synchronization.json', record_path.read_bytes())
        record_ref = dict(path=p['backup'] + '/synchronization.json',
                          sha256=nc.digest(backup / 'synchronization.json'))
        manifest = backup / 'archive.json'
        receipt = dict(schema_version=1, kind='chapter_sync_completed',
                       **{key: p.get(key) for key in
                          ('operation_id', 'number', 'target', 'draft_sha256', 'task_stage')},
                       journal_sha256=journal_digest(p),
                       archive_manifest_sha256=nc.digest(manifest) if manifest.exists() else None,
                       change_record=record_ref,
                       source_change_record=dict(path=req['change_record'], sha256=record_ref['sha256']))
        save_fixed(backup / 'completion.json', json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
        validate_sync_receipt(root, p['operation_id'])
        # A durable terminal phase lets later checks detect deletion of an old
        # receipt even after another operation replaces this chapter.
        if journal['phase'] != 'completed':
            completed = dict(p, phase='completed')
            save(backup / 'journal.json', completed)
        state['pending_operations'] = []
        save(root / nc.STATE, state)
        return dict(ok=True, operation_id=p['operation_id'], pending=False, draft_sha256=p['draft_sha256'],
                    completion=dict(path=p['backup'] + '/completion.json',
                                    sha256=nc.digest(backup / 'completion.json')))


def check(book, *, verify_snapshots=True):
    issues = nc.check_book(book, verify_snapshots=verify_snapshots)
    try:
        root = nc.root_path(book)
        for ch in nc.load_state(root)['chapters']:
            try:
                reviews = ch.get('review_paths', [ch['review_path']])
                require(isinstance(reviews, list) and bool(reviews), 'missing review_paths')
                validate_reviews(root, reviews,
                                 dict(task_id=ch['writer_task'], agent_id=ch.get('writer_agent')),
                                 ch['sha256'])
                require(nonempty(ch.get('writer_agent')), 'missing writer_agent; host verification required')
                hashes = ch.get('review_hashes')
                require(isinstance(hashes, dict) and set(hashes) == set(reviews), 'missing review hashes')
                require(all(nc.digest(file_path(root, name)) == sha for name, sha in hashes.items()), 'review changed after adoption')
                if ch.get('workflow_version', 1) >= 2:
                    require(all(read_json(file_path(root, name)).get('schema_version') == 2 for name in reviews),
                            'workflow v2 chapter requires review schema_version 2')
                    validate_prose_evidence(root, ch['path'], ch['sha256'], ch.get('prose_policy'), ch.get('prose_check'))
            except (ValueError, OSError, TypeError, KeyError) as exc:
                issues.append('chapter ' + str(ch['number']) + ': ' + str(exc))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        issues.append(str(exc))
    return dict(ok=not issues, issues=issues,
                identity_notice='主agent须对照宿主核验实际agent身份；文件不能认证身份。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('adopt', 'resume', 'finish', 'check', 'unlock', 'quarantine', 'diff'):
        sub = subs.add_parser(command)
        sub.add_argument('--book', required=True)
        if command == 'diff':
            sub.add_argument('--old', required=True, help='book-relative baseline draft')
            sub.add_argument('--new', required=True, help='book-relative current draft')
            sub.add_argument('--summary', required=True)
        elif command != 'check':
            sub.add_argument('--request', required=True, help='inline JSON, JSON-file path, or @JSON-file')
        else:
            sub.add_argument('--skip-snapshots', action='store_true', help='skip immutable snapshot payload verification (read-only quick check)')
    args = parser.parse_args(argv)
    try:
        if args.command == 'diff':
            print(json.dumps(change_map(args.book, args.old, args.new, args.summary), ensure_ascii=False))
            return 0
        if args.command == 'check':
            result = check(args.book, verify_snapshots=not args.skip_snapshots)
        else:
            raw = args.request
            req = (obj(json.loads(raw)) if raw.lstrip().startswith('{') else read_json(Path(raw[1:] if raw.startswith('@') else raw)))
            result = globals()[args.command](args.book, req)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 1
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
