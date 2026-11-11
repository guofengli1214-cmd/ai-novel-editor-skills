#!/usr/bin/env python3
"""Mechanical chapter adoption gates; standard library only.

Subagents: adopt --book DIR --request JSON; finish --book DIR --request JSON.
Main agent: check --book DIR (read only). JSON is an inline object or @file.
Host MUST independently compare actual writer/reviewer agent identities: JSON
identities and authorizations are assertions, not authenticated credentials.

adopt: {number,target,draft,expected_sha256,writer:{task_id,agent_id},reviews:[],
authorization:{mode,source,approved_sha256?},change_record,task_stage?}. Modes: per_chapter,
trial (approved_sha256 required), continuous; Chinese aliases 逐章/试写/连续.
source is a nonempty authorization provenance string. Paths are book-relative.
Managed books require taskctl adoption/synchronization gates. task_stage selects
the current chapter/trial stage; otherwise taskctl matches it by chapter number.
Review: {schema_version:1,writer_task,reviewer:{task_id,agent_id},draft_sha256,
coverage:[literary,continuity],verdict:pass|revise|blocked,
issues:[{id,severity,description,evidence,status}]}. severity=hard|soft|info; status=open|resolved|waived; hard requires resolved.
finish: {operation_id}. The adopt change_record JSON must contain draft_sha256,
categories (all CATEGORIES: updated|unchanged; pending is rejected;
summaries/history/indexes/handoff MUST be updated after every adoption),
checked_sources:[relative paths], updated_files:[{path,sha256}],
category_files:{updated_category:[paths in updated_files]},
handoff:{path,chapter:int}. Handoff text includes 第N章, 第000N章 or formal filename.

Backups and durable journal live under 历史版本/chapter-<operation_id>.
Pending contains the full request, hashes and backup paths. Any pending blocks
adopt, including retries. Interrupted adoption needs explicit manual recovery
from journal/backup; finish only accepts an installed chapter. Never manufacture
subagent completion records. Concurrent cooperating writers are serialized;
Stale locks require unlock with confirm_no_writer:true and expected_pending:[IDs].
Unlock preserves pending operations and their journals; recovery remains explicit.
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
from contextlib import contextmanager
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
    return obj(json.loads(path.read_text(encoding='utf-8')))


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


def validate_reviews(root, reviews, writer, sha):
    obj(writer)
    require(all(nonempty(writer.get(k)) for k in ('task_id', 'agent_id')), 'invalid writer identity')
    require(isinstance(reviews, list) and bool(reviews), 'missing reviews')
    coverage, reviewers = set(), []
    for name in reviews:
        review = read_json(file_path(root, name))
        require(type(review.get('schema_version')) is int and review['schema_version'] == 1, 'review schema_version')
        identity = obj(review.get('reviewer'))
        require(all(nonempty(identity.get(k)) for k in ('task_id', 'agent_id')), 'invalid reviewer identity')
        require(identity['agent_id'] != writer['agent_id'] and identity['task_id'] != writer['task_id'], 'writer cannot review own draft')
        require(review.get('writer_task') == writer['task_id'] and review.get('draft_sha256') == sha, 'stale review')
        require(review.get('verdict') == 'pass', 'review not pass')
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
        reviewers.append(identity)
    require(coverage == {'literary', 'continuity'}, 'missing literary/continuity coverage')
    return reviewers


def adopt(book, request):
    root = nc.root_path(book)
    with locked(root):
        return _adopt(root, obj(request))


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
    if task_stage is not None:
        req['task_stage'] = task_stage
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
                   draft_backup=backup_rel + '/draft.md', phase='prepared')
    if task_stage is not None:
        pending['task_stage'] = task_stage
    save(backup / 'journal.json', pending)
    state['pending_operations'] = [pending]
    save(root / nc.STATE, state)
    # Once pending is durable, failures are deliberately left recoverable.
    nc.atomic_write(target, data)
    entry = dict(old or {})
    entry.update(number=number, path=req['target'], sha256=sha,
                 writer_task=writer['task_id'], writer_agent=writer['agent_id'],
                 review_path=reviews[0], reviewer_task=reviewers[0]['task_id'],
                 review_paths=reviews, reviewers=reviewers, authorization=auth,
                 review_hashes=pending['review_hashes'])
    if task_stage is not None:
        entry['task_stage'] = task_stage
    state['chapters'] = [c for c in state['chapters'] if c['number'] != number] + [entry]
    state['chapters'].sort(key=lambda c: c['number'])
    pending['phase'] = 'adopted'
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
        ch = next((c for c in state['chapters'] if c['number'] == p['number']), None)
        require(ch and ch['path'] == p['target'] and ch['sha256'] == p['draft_sha256'], 'chapter not installed')
        require(nc.digest(file_path(root, p['target'])) == p['draft_sha256'], 'chapter hash mismatch')
        req = p['request']
        validate_reviews(root, req['reviews'], req['writer'], p['draft_sha256'])
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
        state['pending_operations'] = []
        save(root / nc.STATE, state)
        return dict(ok=True, operation_id=p['operation_id'], pending=False, draft_sha256=p['draft_sha256'])


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
            except (ValueError, OSError, TypeError, KeyError) as exc:
                issues.append('chapter ' + str(ch['number']) + ': ' + str(exc))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        issues.append(str(exc))
    return dict(ok=not issues, issues=issues,
                identity_notice='主agent须对照宿主核验实际agent身份；文件不能认证身份。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('adopt', 'finish', 'check', 'unlock'):
        sub = subs.add_parser(command)
        sub.add_argument('--book', required=True)
        if command != 'check':
            sub.add_argument('--request', required=True, help='inline JSON, JSON-file path, or @JSON-file')
        else:
            sub.add_argument('--skip-snapshots', action='store_true', help='skip immutable snapshot payload verification (read-only quick check)')
    args = parser.parse_args(argv)
    try:
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
