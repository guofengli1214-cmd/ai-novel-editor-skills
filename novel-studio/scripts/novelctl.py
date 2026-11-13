#!/usr/bin/env python3
"""Standard-library novel file management (no database).

State: 创作约定/作品状态.json = {schema_version:1, title:str,
chapters:[{number:int, path:str, sha256:str, writer_task:str,
review_path:str, reviewer_task:str}], pending_operations:[]}.
Chapter paths are relative, top-level .md files; review_path is relative.
Optional fields are preserved. Only registered chapters are exported; pending
operations block export. check flags unregistered top-level Markdown files.
New books enable task_management and initialize the single task ledger.
init_book(..., task_management=False) retains legacy fixtures without a ledger;
the init CLI always enables task management and requires --title-decision JSON
with the author's actual title choice. A supplied API title_decision is checked
before creating directories and preserved in the author's agreement record.
Omitting it from the low-level API retains legacy fixture compatibility.
Export does not require finalization.
Public API: load_state(book), check_book(book), index_book(book),
snapshot_book(book, label), restore_book(book, snapshot), export_book(book, output).
All writing commands are for the materials subagent; the main agent uses check.
Snapshots live in 历史版本/<id>/ with manifest.json and payload/; history
is excluded. Restore validates first, stages a complete replacement, preserves
history and creates a verified pre-restore snapshot. A sibling recovery copy
is retained if committing fails. Concurrent writers must be stopped for restore. check validates inline and
reference Markdown local file links (including JSON); heading anchors and
complex Markdown syntax are not validated. TXT preserves Markdown text verbatim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import tempfile
import uuid
from urllib.parse import unquote, urlsplit, quote

STATE = '创作约定/作品状态.json'
HISTORY = '历史版本'
BEGIN = '<!-- novelctl:auto:start -->'
END = '<!-- novelctl:auto:end -->'
DIRECTORIES = ('创作约定', '大纲', '人物', '世界与地点', '连贯性记录', '摘要',
               '审稿与修订', '草稿与备选', HISTORY)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def root_path(book):
    p = Path(book).absolute()
    if any(x.is_symlink() for x in (p, *p.parents)):
        raise ValueError('symbolic link in book path')
    if not p.is_dir():
        raise ValueError('book directory missing')
    return p.resolve()


def is_link_or_reparse(path):
    """Reject Windows junctions as well as symbolic links inside a work."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode) or
            bool(getattr(info, 'st_file_attributes', 0) &
                 getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


def contained_path(root, target):
    """Check existing components before any read/write, including new targets."""
    root, target = Path(root), Path(target)
    rel = target.relative_to(root)
    current = root
    for part in rel.parts:
        if part == '..':
            raise ValueError('path escapes book: ' + str(target))
        current = current / part
        if is_link_or_reparse(current):
            raise ValueError('symbolic link or reparse point: ' + str(current))
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('path escapes book: ' + str(target))
    return target


def relative_path(root, value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('invalid relative path')
    p = Path(value)
    if p.is_absolute() or PureWindowsPath(value).drive or '..' in p.parts or value != p.as_posix():
        raise ValueError('path escapes or is not canonical: ' + value)
    return contained_path(root, root / p)


def inventory(root, exclude_history=False, *, skip_snapshots=False):
    root = root_path(root)
    files, dirs = {}, []
    def walk_error(exc):
        raise exc
    for base, names, entries in os.walk(root, followlinks=False, onerror=walk_error):
        parent = Path(base)
        contained_path(root, parent)
        # Check directory entries before pruning: even the history root must
        # never redirect a caller outside the work.
        for name in names + entries:
            if is_link_or_reparse(parent / name):
                raise ValueError('symbolic link or reparse point: ' + str(parent / name))
        if exclude_history and parent == root:
            names[:] = [n for n in names if n != HISTORY]
        rel = parent.relative_to(root)
        if (skip_snapshots and len(rel.parts) == 2 and rel.parts[0] == HISTORY
                and (parent / 'manifest.json').is_file()):
            names[:] = [n for n in names if n != 'payload']
        dirs.append(parent.relative_to(root).as_posix())
        for name in entries:
            p = contained_path(root, parent / name)
            if not p.is_file():
                raise ValueError('not a regular file: ' + str(p))
            files[p.relative_to(root).as_posix()] = digest(p)
    return files, sorted(dirs)


def atomic_write(path, data):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.novelctl-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data if isinstance(data, bytes) else data.encode('utf-8'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_state(book):
    root = root_path(book)
    state = json.loads(relative_path(root, STATE).read_text(encoding='utf-8'))
    if type(state.get('schema_version')) is not int or state['schema_version'] != 1:
        raise ValueError('unsupported schema_version')
    if not isinstance(state.get('title'), str) or not isinstance(state.get('chapters'), list) or not isinstance(state.get('pending_operations'), list):
        raise ValueError('invalid state structure')
    if 'task_management' in state and type(state['task_management']) is not bool:
        raise ValueError('task_management must be boolean')
    numbers, paths = set(), set()
    for ch in state['chapters']:
        if not isinstance(ch, dict) or type(ch.get('number')) is not int or ch['number'] < 1:
            raise ValueError('invalid chapter number')
        p = relative_path(root, ch.get('path'))
        if p.parent != root or p.suffix.lower() != '.md' or p.name == '索引.md':
            raise ValueError('formal chapter must be a top-level markdown file')
        relative_path(root, ch.get('review_path'))
        if not re.fullmatch('[0-9a-f]{64}', ch.get('sha256', '')):
            raise ValueError('invalid sha256')
        for field in ('writer_task', 'reviewer_task'):
            if not isinstance(ch.get(field), str) or not ch[field].strip():
                raise ValueError('missing ' + field)
        if ch['number'] in numbers or ch['path'] in paths:
            raise ValueError('duplicate chapter number/path')
        numbers.add(ch['number'])
        paths.add(ch['path'])
    return state


def archived(root, path):
    """Snapshot payloads and chapter adoption journals are immutable archives."""
    rel = path.relative_to(root)
    if len(rel.parts) < 2 or rel.parts[0] != HISTORY:
        return False
    folder = root / HISTORY / rel.parts[1]
    return ((folder / 'manifest.json').is_file() or
            (re.fullmatch(r'chapter-[0-9a-f]{32}', folder.name) is not None and
             (folder / 'journal.json').is_file()))


def index_destination(root, child):
    if not child.is_dir():
        return child.name
    if archived(root, child) and not (child / 'manifest.json').is_file():
        return child.name + '/journal.json'
    return child.name + '/索引.md'


def index_book(book):
    root = root_path(book)
    _, dirs = inventory(root)
    for directory in dirs:
        folder = contained_path(root, root / directory)
        if archived(root, folder):
            continue
        target = contained_path(root, folder / '索引.md')
        old = target.read_text(encoding='utf-8') if target.exists() else '# ' + folder.name + '\n\n本目录的文件与资料入口。\n'
        if old.count(BEGIN) != old.count(END) or old.count(BEGIN) > 1 or (BEGIN in old and old.index(BEGIN) > old.index(END)):
            raise ValueError('malformed automatic index block: ' + str(target))
        lines = []
        for child in sorted(folder.iterdir()):
            contained_path(root, child)
            if child.name == '索引.md':
                continue
            dest = index_destination(root, child)
            label = child.name.replace('[', '\\[').replace(']', '\\]')
            lines.append('- [' + label + '](' + quote(dest, safe='/') + ')')
        block = BEGIN + '\n' + '\n'.join(lines) + '\n' + END
        if BEGIN in old:
            old = old[:old.index(BEGIN)] + block + old[old.index(END) + len(END):]
        else:
            old = old.rstrip() + '\n\n' + block + '\n'
        atomic_write(contained_path(root, target), old)


def markdown_prose(text):
    """Mask fenced and inline code so example links are not file references."""
    lines, fence = [], None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})([^\r\n]*)', line)
        if fence is not None:
            if (marker and marker[1][0] == fence[0] and
                    len(marker[1]) >= fence[1] and not marker[2].strip()):
                fence = None
            lines.append('\n')
        elif marker and (marker[1][0] != '`' or '`' not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            lines.append('\n')
        else:
            lines.append(line)
    text, prose, i = ''.join(lines), [], 0
    while i < len(text):
        # Outside a code span, an escaped backtick is ordinary prose. Within
        # a span, backslashes have no escape semantics for the closing run.
        if text[i] == '\\' and i + 1 < len(text):
            prose.append(text[i:i + 2])
            i += 2
        elif text[i] == '`':
            end = i + 1
            while end < len(text) and text[end] == '`':
                end += 1
            search, closing = end, None
            while search < len(text):
                start = text.find('`', search)
                if start < 0:
                    break
                stop = start + 1
                while stop < len(text) and text[stop] == '`':
                    stop += 1
                if stop - start == end - i:
                    closing = stop
                    break
                search = stop
            if closing is None:
                prose.append(text[i:end])
                i = end
            else:
                prose.append(''.join('\n' if char == '\n' else ' '
                                     for char in text[i:closing]))
                i = closing
        else:
            prose.append(text[i])
            i += 1
    return ''.join(prose)


def check_book(book, *, allow_pending=False, verify_snapshots=True):
    root = root_path(book)
    issues = []
    try:
        # Visit each live file once. Snapshot payloads are hashed by their own
        # verifier, or deliberately skipped by the explicit quick-check option.
        all_files, all_dirs = inventory(root, skip_snapshots=True)
        files = {name: sha for name, sha in all_files.items()
                 if Path(name).parts[0] != HISTORY}
        for name in all_files:
            if verify_snapshots and Path(name).name == 'manifest.json' and len(Path(name).parts) == 3 and Path(name).parts[0] == HISTORY:
                try:
                    verify_snapshot((root / name).parent)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    issues.append('invalid snapshot: ' + name + ': ' + str(exc))
        for d in all_dirs:
            folder = root / d
            if archived(root, folder):
                continue
            if not (root / d / '索引.md').is_file():
                issues.append('missing index: ' + d)
            else:
                index_text = markdown_prose((folder / '索引.md').read_text(encoding='utf-8'))
                destinations = re.findall(r'\]\(<?([^\s)>]+)>?\)', index_text)
                destinations = {unquote(urlsplit(x).path) for x in destinations}
                for child in folder.iterdir():
                    if child.name == '索引.md':
                        continue
                    expected = index_destination(root, child)
                    if expected not in destinations:
                        issues.append('index missing entry: ' + d + ': ' + child.name)
        for name in all_files:
            if archived(root, root / name):
                continue
            if not name.lower().endswith('.md'):
                continue
            p = root / name
            text = markdown_prose(p.read_text(encoding='utf-8'))
            links = re.findall(r'!?\[[^\]]*\]\(\s*(<[^>]*>|[^\s)]+)(?:\s+[^)]*)?\)', text)
            links += re.findall(r'^\s*\[[^\]]+\]:\s*(<[^>]*>|\S+)', text, re.M)
            for link in links:
                url = urlsplit(link.strip('<>'))
                if url.scheme or url.netloc or not url.path:
                    continue
                decoded = unquote(url.path)
                target = p.parent / decoded
                try:
                    # Normalize parent components without resolving a link
                    # before relative_path has had a chance to reject it.
                    rel = Path(os.path.abspath(target)).relative_to(root).as_posix()
                    relative_path(root, rel)
                    if not target.is_file():
                        raise ValueError('missing')
                except ValueError:
                    issues.append('invalid local md link/file link: ' + name + ': ' + link)
        state = load_state(root)
        registered = {ch['path'] for ch in state['chapters']}
        for name in sorted(files):
            if '/' not in name and name.lower().endswith('.md') and name != '索引.md' and name not in registered:
                issues.append('unregistered top-level markdown chapter: ' + name)
        for ch in state['chapters']:
            p = relative_path(root, ch['path'])
            if not p.is_file() or digest(p) != ch['sha256']:
                issues.append('chapter sha256 mismatch: ' + ch['path'])
            if not relative_path(root, ch['review_path']).is_file():
                issues.append('missing review: ' + ch['review_path'])
        if state['pending_operations'] and not allow_pending:
            summaries = []
            for operation in state['pending_operations']:
                if not isinstance(operation, dict):
                    summaries.append({'error': 'invalid pending operation'})
                    continue
                summary = {key: operation[key] for key in
                           ('operation_id', 'number', 'target', 'phase') if key in operation}
                if isinstance(operation.get('backup'), str):
                    summary['journal'] = operation['backup'] + '/journal.json'
                summaries.append(summary)
            issues.append('pending_operations: ' + json.dumps(summaries, ensure_ascii=False))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        issues.append(str(exc))
    return issues


def init_book(parent, title, *, task_management=True, title_decision=None):
    if type(task_management) is not bool:
        raise ValueError('task_management must be boolean')
    # Apply the portable filename rules before creating even the library path.
    if (not isinstance(title, str) or not title.strip() or title in ('.', '..')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', title)
            or title.endswith((' ', '.')) or
            re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',
                         title.split('.')[0].rstrip(' '))):
        raise ValueError('title must be a single portable directory name (no Windows reserved names, characters or trailing space/dot)')
    title_record = None
    if title_decision is not None:
        # Runtime import avoids the taskctl -> novelctl module dependency cycle.
        # Validate before mkdir, including the otherwise missing library root.
        import taskctl
        errors = taskctl.title_decision_errors(title, title_decision)
        if errors:
            raise ValueError('; '.join(errors))
        title_record = ('# 作者约定与授权\n\n## 初始化书名决定\n\n'
                        '以下保留本次建书的作者决定来源；资料整理可在本文件继续扩充，'
                        '须保留原话、范围及后续取代关系。\n\n```json\n' +
                        json.dumps({'title': title, 'decision': title_decision},
                                   ensure_ascii=False, indent=2) + '\n```\n\n'
                        '此记录仅证明已申报的书名决定，不产生正文、设定、剧情或后续创作授权。\n')
    parent = Path(parent).absolute()
    if any(item.is_symlink() for item in (parent, *parent.parents)):
        raise ValueError('symbolic link in library path')
    parent.mkdir(parents=True, exist_ok=True)
    parent = root_path(parent)
    book = parent / title
    book.mkdir()  # Never overwrite an existing work.
    try:
        for directory in DIRECTORIES:
            (book / directory).mkdir()
        state = dict(schema_version=1, title=title, chapters=[], pending_operations=[])
        task_link = ''
        if task_management:
            state['task_management'] = True
        atomic_write(book / STATE, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        if title_record is not None:
            atomic_write(book / '创作约定/作者约定与授权.md', title_record)
        if task_management:
            import taskctl
            taskctl.initialize(book)
            task_link = '- [阶段任务与验收](任务计划与验收.md)\n'
        atomic_write(book / '创作约定/写作进度与接续说明.md', '# 写作进度与接续说明\n\n文件约定版本：1\n当前方案：待定；正式章节：无；故事时点：待定。\n作者参与与资料建设模式：待记录真实约定；尚无后续创作授权。\n\n## 交接入口\n\n- [资料索引](索引.md)\n- [全书索引](../索引.md)\n' + task_link + '\n## 待补充与下一步\n\n创作意图、语言标准与代表片段、阶段目标、故事线、人物成长与关系、近期事件、伏笔、知情与资源状态均待建立。\n独立阅读反馈与构思风险待记录。作者决定、未核实项、冲突与过期资料待核对。\n写前必读：本文件及全书索引；尚未进行正文与文学检查。\n中断更新事项以作品状态.json 的 pending_operations 为准。\n')
        index_book(book)
    except Exception:
        # Only remove the directory this invocation created. An existing work
        # fails at mkdir above and never reaches this cleanup.
        shutil.rmtree(book)
        raise
    return book


def export_book(book, output):
    root = root_path(book)
    state = load_state(root)
    if state['pending_operations']:
        raise ValueError('pending_operations: export blocked until synchronization completes')
    output = Path(output).absolute()
    if output.suffix.lower() not in ('.txt', '.md'):
        raise ValueError('output must be .txt or .md')
    if any(is_link_or_reparse(p) for p in (output, *output.parents)):
        raise ValueError('export path contains symlinks/reparse points')
    output = output.resolve()
    if output.is_relative_to(root):
        raise ValueError('export must be outside book and contain no symlinks')
    texts = []
    for ch in sorted(state['chapters'], key=lambda c: c['number']):
        p = relative_path(root, ch['path'])
        if digest(p) != ch['sha256']:
            raise ValueError('chapter sha256 mismatch: ' + ch['path'])
        texts.append(p.read_text(encoding='utf-8').rstrip())
    atomic_write(output, '\n\n'.join(texts) + ('\n' if texts else ''))


def verify_snapshot(snapshot):
    snap = root_path(snapshot)
    manifest_path = relative_path(snap, 'manifest.json')
    if not manifest_path.is_file():
        raise ValueError('not a snapshot directory: missing manifest.json: ' + str(snap))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or
            type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1):
        raise ValueError('unsupported snapshot schema_version: ' + str(snap))
    if not isinstance(manifest.get('files'), dict) or not isinstance(manifest.get('directories'), list):
        raise ValueError('invalid snapshot manifest structure: ' + str(snap))
    payload = relative_path(snap, 'payload')
    if not payload.is_dir():
        raise ValueError('not a snapshot directory: missing payload: ' + str(snap))
    # One traversal checks every snapshot entry and hashes each payload file
    # once. Derive the payload inventory instead of hashing it a second time.
    all_files, all_dirs = inventory(snap)
    files = {name[len('payload/'):]: sha for name, sha in all_files.items()
             if name.startswith('payload/')}
    dirs = sorted('.' if name == 'payload' else name[len('payload/'):]
                  for name in all_dirs if name == 'payload' or name.startswith('payload/'))
    if files != manifest['files'] or dirs != manifest['directories']:
        raise ValueError('snapshot manifest checksum/directory mismatch')
    if any(Path(p).parts[0] == HISTORY for p in files) or HISTORY in dirs:
        raise ValueError('snapshot contains history')
    load_state(payload)
    return payload


def snapshot_book(book, label):
    root = root_path(book)
    load_state(root)
    before = inventory(root, exclude_history=True)
    history = relative_path(root, HISTORY)
    # The payload inventory deliberately excludes history. Check its fixed
    # live index separately before creating even a partial new snapshot.
    history_index = relative_path(root, HISTORY + '/索引.md')
    history.mkdir(exist_ok=True)
    snap = history / ('snapshot-' + uuid.uuid4().hex)
    snap.mkdir()
    try:
        payload = snap / 'payload'
        payload.mkdir()
        files, dirs = before
        for d in dirs:
            (payload / d).mkdir(exist_ok=True)
        for name in files:
            shutil.copy2(root / name, payload / name)
        if inventory(payload) != before or inventory(root, exclude_history=True) != before:
            raise ValueError('book changed while snapshotting')
        atomic_write(snap / 'manifest.json', json.dumps(dict(schema_version=1, label=label, files=files, directories=dirs), ensure_ascii=False, indent=2) + '\n')
        atomic_write(snap / '索引.md', '# 历史快照\n\n- [校验清单](manifest.json)：文件哈希、目录与快照标签。\n- [快照内容](payload/索引.md)：恢复用的原始文件，不在此处编辑。\n')
        verify_snapshot(snap)
        # Maintain only the live history directory index; never rewrite payloads.
        history_index = relative_path(root, HISTORY + '/索引.md')
        old = history_index.read_text(encoding='utf-8') if history_index.exists() else '# 历史版本\n'
        link = '- [' + snap.name + '](' + snap.name + '/索引.md)\n'
        atomic_write(history_index, old.rstrip() + '\n' + link)
        return snap
    except Exception:
        # Keep partial data visible rather than silently treating it as valid.
        raise


def restore_book(book, snapshot):
    root = root_path(book)
    # Check ownership before reading a manifest or hashing any payload. An
    # arbitrary valid snapshot must not import or inspect another work.
    snap = Path(snapshot).absolute()
    requested_root = Path(book).absolute()
    if requested_root != root:
        # Keep the supported Windows book-root junction usable without
        # resolving away links inside the book before contained_path sees them.
        try:
            snap = root / snap.relative_to(requested_root)
        except ValueError:
            pass
    history = relative_path(root, HISTORY)
    if snap.parent != history:
        raise ValueError('restore snapshot must be a direct child of current book history')
    snap = contained_path(root, snap)
    if not snap.is_dir():
        raise ValueError('restore snapshot directory missing')
    payload = verify_snapshot(snap)
    inventory(root)
    stage = Path(tempfile.mkdtemp(prefix='.' + root.name + '-restore-', dir=root.parent))
    recovery = root.parent / ('.' + root.name + '-recovery-' + uuid.uuid4().hex)
    try:
        shutil.copytree(payload, stage, dirs_exist_ok=True)
        if inventory(stage) != inventory(payload):
            raise ValueError('staging verification failed')
        backup = snapshot_book(root, 'pre-restore')
        shutil.copytree(root / HISTORY, stage / HISTORY)
        os.rename(root, recovery)
        try:
            os.rename(stage, root)
        except BaseException:
            os.rename(recovery, root)
            raise
        # Recovery is intentionally kept: no destructive cleanup in commit path.
        return root / backup.relative_to(root), recovery
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init'); p.add_argument('--parent', required=True); p.add_argument('--title', required=True)
    p.add_argument('--title-decision', required=True,
                   help='actual author title decision: JSON object, JSON-file path or @file')
    for command in ('index', 'check', 'export', 'snapshot', 'restore'):
        p = sub.add_parser(command); p.add_argument('--book', required=True)
        if command == 'check': p.add_argument('--skip-snapshots', action='store_true', help='skip immutable snapshot payload verification (read-only quick check)')
        if command == 'export': p.add_argument('--output', required=True)
        if command == 'snapshot': p.add_argument('--label', required=True)
        if command == 'restore': p.add_argument('--snapshot', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            raw = args.title_decision
            decision = (json.loads(raw) if raw.lstrip().startswith('{') else
                        json.loads(Path(raw[1:] if raw.startswith('@') else raw).read_text(encoding='utf-8-sig')))
            if not isinstance(decision, dict):
                raise ValueError('title decision must be a JSON object')
            print(init_book(args.parent, args.title, title_decision=decision))
        elif args.command == 'index': index_book(args.book)
        elif args.command == 'check':
            issues = check_book(args.book, verify_snapshots=not args.skip_snapshots)
            print('\n'.join(issues) if issues else 'OK')
            return 1 if issues else 0
        elif args.command == 'export': export_book(args.book, args.output)
        elif args.command == 'snapshot': print(snapshot_book(args.book, args.label))
        elif args.command == 'restore':
            backup, recovery = restore_book(args.book, args.snapshot)
            print('backup: ' + str(backup) + '\nrecovery: ' + str(recovery))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('error: ' + str(exc), file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
