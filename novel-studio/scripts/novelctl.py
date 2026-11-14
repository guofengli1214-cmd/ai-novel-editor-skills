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
with the author's actual title choice or explicit deferral. --work-id separates
the portable directory identifier from a selected title; an unnamed project
requires a matching deferred record and preserves title:""/title_status:"unresolved".
A supplied API title_decision is checked
before creating directories and preserved in the author's agreement record.
Omitting it from the low-level API retains legacy fixture compatibility.
Optional hidden_plot_decision (CLI --hidden-plot-decision) preserves the author's
enabled/disabled choice; only enabled creates the empty hidden-plot management
record. Omission leaves the question pending and never implies consent.
Export does not require finalization.
Public API: load_state(book), check_book(book), index_book(book),
snapshot_book(book, label), restore_book(book, snapshot), export_book(book, output).
All writing commands are for the materials subagent; the main agent uses check.
Snapshots live in 历史版本/<id>/ with manifest.json and payload/; history
is excluded. Restore validates first, stages a complete replacement and preserves
history. It creates a verified pre-restore snapshot when current state is readable;
otherwise its pre-restore backup is the unchanged sibling recovery directory,
not a snapshot. The original directory is retained on successful restore and
reinstated if committing fails. Concurrent writers must be stopped for restore. check validates inline and
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

# These empty management records are created only by init, never by read/check.
MANAGEMENT_FILES = {
    '线索与悬念/钩子管理.md': (
        '# 钩子管理\n\n## 用途与职责\n\n'
        '用途：分别管理读者期待与钩子的设置、承接、兑现及有意留白。\n'
        '专职角色：钩子管理子 agent；本次仅建立空入口，尚未派工分析。\n\n'
        '## 计划\n\n待建立。尚无已定钩子或兑现安排。\n\n'
        '## 实际\n\n未分析。尚无正式正文，不登记已设置或已兑现的钩子。\n\n'
        '## 来源与版本\n\n待登记。后续条目须引用作者约定、计划或正文及其文件版本；'
        '计划与正文实际分别记录，不把提案当作已发生事实。\n'),
    '故事线/故事线管理.md': (
        '# 故事线管理\n\n## 用途与职责\n\n'
        '用途：分别管理主支线及其关联、推进、交汇与收束。\n'
        '专职角色：故事线管理子 agent；本次仅建立空入口，尚未派工分析。\n\n'
        '## 计划\n\n待建立。尚无已定主支线或故事走向。\n\n'
        '## 实际\n\n未分析。尚无正式正文，不登记已推进或已收束的故事线。\n\n'
        '## 来源与版本\n\n待登记。后续条目须引用作者约定、计划或正文及其文件版本；'
        '计划与正文实际分别记录，不把提案当作已发生事实。\n'),
    '曲线与节奏/故事起伏曲线.md': (
        '# 故事起伏曲线\n\n## 用途与职责\n\n'
        '用途：主动主导场景、章节、阶段与全书的起伏规划，管理张弛、压力、情绪及希望与挫败变化。\n'
        '专职角色：起伏曲线管理子 agent；本次仅建立空入口，尚未派工分析。\n\n'
        '## 计划\n\n待建立。尚无已定起伏安排或曲线取值。\n\n'
        '## 实际\n\n未分析。尚无正式正文，不登记实际起伏判断或曲线值。\n\n'
        '## 来源与版本\n\n待登记。后续条目须引用作者约定、计划或正文及其文件版本；'
        '计划与正文实际分别记录，不把提案当作已发生事实。\n'),
}

# Optional: only create this empty record after a recorded author choice.
HIDDEN_PLOT_FILE = '故事线/暗线剧情管理.md'
HIDDEN_PLOT_TEMPLATE = (
    '# 暗线剧情管理\n\n## 用途与职责\n\n'
    '用途：管理幕后真实事件、明暗线因果、知情边界、线索及揭示的计划与实际。\n'
    '专职角色：暗线管理子 agent；本次仅建立空入口，尚未派工分析。\n'
    '作者选择启用管理不代表已选定任何幕后事件、秘密或揭示方案。\n\n'
    '## 计划\n\n待建立。尚无已定暗线、幕后事件或揭示安排。\n\n'
    '## 实际\n\n未分析。尚无正式正文，不登记已发生的幕后事件、知情或揭示。\n\n'
    '## 来源与版本\n\n待登记。后续条目须引用作者决定、故事时间与叙述位置、'
    '角色及读者知情、相关故事线和钩子标识、计划或正文及其文件版本；'
    '计划与正文实际分别记录，不把提案当作已发生事实。\n')


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
            names[:] = [n for n in names if n not in ('payload', 'adoption-evidence')]
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
    return validate_state(root, state)


def validate_state(root, state):
    """Validate a live or archived state without following its chapter pointers."""
    if not isinstance(state, dict):
        raise ValueError('invalid state structure')
    if type(state.get('schema_version')) is not int or state['schema_version'] != 1:
        raise ValueError('unsupported schema_version')
    if not isinstance(state.get('title'), str) or not isinstance(state.get('chapters'), list) or not isinstance(state.get('pending_operations'), list):
        raise ValueError('invalid state structure')
    if 'task_management' in state and type(state['task_management']) is not bool:
        raise ValueError('task_management must be boolean')
    if 'work_id' in state:
        validate_directory_name(state['work_id'])
    if 'title_status' in state:
        if state['title_status'] not in ('unresolved', 'active'):
            raise ValueError('invalid title_status')
        if state['title_status'] == 'unresolved':
            import taskctl
            errors = taskctl.title_decision_errors(
                state['title'], state.get('title_deferral'), work_id=state.get('work_id'))
            if errors:
                raise ValueError('; '.join(errors))
            if not isinstance(state.get('title_deferral'), dict) or state['title_deferral'].get('resolution') != 'deferred':
                raise ValueError('unresolved title requires an explicit title_deferral')
        elif not state['title'].strip():
            raise ValueError('active title must be nonempty')
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


def chapter_sequence_issues(chapters):
    """Registered formal chapters use the complete reading sequence 1..N."""
    numbers = sorted(ch['number'] for ch in chapters)
    if numbers == list(range(1, len(numbers) + 1)):
        return []
    return ['chapter numbers must be continuous from 1: ' +
            ', '.join(str(number) for number in numbers)]


def index_destination(root, child):
    if not child.is_dir():
        return child.name
    if archived(root, child) and not (child / 'manifest.json').is_file():
        return child.name + '/journal.json'
    return child.name + '/索引.md'


def index_directory(root, folder):
    """Refresh one live index; immutable archives are never rewritten."""
    root, folder = Path(root), contained_path(root, folder)
    if archived(root, folder):
        return
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
    atomic_write(target, old)


def index_book(book):
    root = root_path(book)
    _, dirs = inventory(root)
    for directory in dirs:
        index_directory(root, root / directory)


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
        # Adoption archives are distinct from whole-book snapshots. Their fixed
        # files and synchronization receipt remain evidence after pending clears.
        for directory in all_dirs:
            parts = Path(directory).parts
            if (len(parts) == 2 and parts[0] == HISTORY and
                    re.fullmatch(r'chapter-[0-9a-f]{32}', parts[1])):
                try:
                    import chapterctl
                    operation_id = parts[1][len('chapter-'):]
                    journal = chapterctl.validate_adoption_archive(root, operation_id)
                    if journal['phase'] == 'completed' or (root / directory / 'completion.json').exists():
                        chapterctl.validate_sync_receipt(root, operation_id)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    issues.append('invalid adoption archive: ' + directory + ': ' + str(exc))
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
        issues.extend(chapter_sequence_issues(state['chapters']))
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
            operation_id = ch.get('adoption_operation_id')
            if operation_id is not None and not any(
                    isinstance(operation, dict) and operation.get('operation_id') == operation_id
                    for operation in state['pending_operations']):
                try:
                    import chapterctl
                    receipt = chapterctl.validate_sync_receipt(root, operation_id)
                    # Later authorized renaming/reordering retains the original
                    # operation as provenance; its adopted bytes must still match.
                    if receipt['draft_sha256'] != ch['sha256']:
                        raise ValueError('completion receipt does not match current chapter')
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    issues.append('invalid adoption completion: ' + str(operation_id) + ': ' + str(exc))
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


def validate_directory_name(name):
    """The work directory identifier is distinct from a book's formal title."""
    if (not isinstance(name, str) or not name.strip() or name in ('.', '..')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((' ', '.')) or
            re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])',
                         name.split('.')[0].rstrip(' '))):
        raise ValueError('title must be a single portable directory name (no Windows reserved names, characters or trailing space/dot)')


def init_book(parent, title='', *, task_management=True, title_decision=None, work_id=None,
              initial_workflow='planning', hidden_plot_decision=None):
    if type(task_management) is not bool:
        raise ValueError('task_management must be boolean')
    if initial_workflow not in ('planning', 'materials', 'review_only'):
        raise ValueError('invalid initial workflow')
    if not task_management and initial_workflow != 'planning':
        raise ValueError('standalone workflows require task management')
    # All naming/deferral checks precede creation of even the library path.
    directory = title if work_id is None else work_id
    validate_directory_name(directory)
    if not isinstance(title, str) or (title != '' and not title.strip()):
        raise ValueError('title must be nonempty or explicitly deferred')
    if title == '' and title_decision is None:
        raise ValueError('unnamed project requires an explicit deferred title decision')
    title_record = None
    if title_decision is not None:
        # Runtime import avoids the taskctl -> novelctl module dependency cycle.
        # Validate before mkdir, including the otherwise missing library root.
        import taskctl
        errors = taskctl.title_decision_errors(title, title_decision, work_id=work_id)
        if errors:
            raise ValueError('; '.join(errors))
        naming_record = {'title': title, 'decision': title_decision}
        if work_id is not None:
            naming_record['work_id'] = work_id
        title_record = ('# 作者约定与授权\n\n## 初始化书名决定\n\n'
                        '以下保留本次建书的作者决定来源；资料整理可在本文件继续扩充，'
                        '须保留原话、范围及后续取代关系。\n\n```json\n' +
                        json.dumps(naming_record, ensure_ascii=False, indent=2) + '\n```\n\n'
                        '此记录仅证明已申报的选名或明确暂缓定名，不产生正文、设定、剧情或后续创作授权。\n')
    hidden_plot_status = '暗线剧情：待询问作者是否需要，未启用管理。\n'
    if hidden_plot_decision is not None:
        import taskctl
        errors = taskctl.hidden_plot_decision_errors(hidden_plot_decision)
        if errors:
            raise ValueError('; '.join(errors))
        hidden_plot_status = ('暗线剧情：作者已选择启用；具体暗线及揭示仍待已定选择与派工分析。\n'
                              if hidden_plot_decision['value'] == 'enabled' else
                              '暗线剧情：作者已选择不需要，已禁用暗线管理。\n')
        if title_record is None:
            title_record = '# 作者约定与授权\n'
        title_record += ('\n## 初始化暗线决定\n\n```json\n' +
                         json.dumps(hidden_plot_decision, ensure_ascii=False, indent=2) +
                         '\n```\n\n此记录仅保存是否启用暗线管理的选择，不产生具体剧情、秘密或揭示方案的创作授权。\n')
    parent = Path(parent).absolute()
    if any(item.is_symlink() for item in (parent, *parent.parents)):
        raise ValueError('symbolic link in library path')
    parent.mkdir(parents=True, exist_ok=True)
    parent = root_path(parent)
    book = parent / directory
    book.mkdir()  # Never overwrite an existing work.
    try:
        for directory in DIRECTORIES:
            (book / directory).mkdir()
        state = dict(schema_version=1, title=title, chapters=[], pending_operations=[])
        if work_id is not None:
            state.update(work_id=work_id, title_status='active' if title else 'unresolved')
        if not title:
            state['title_deferral'] = title_decision
        task_link = ''
        if task_management:
            state['task_management'] = True
        atomic_write(book / STATE, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        if title_record is not None:
            atomic_write(book / '创作约定/作者约定与授权.md', title_record)
        if task_management:
            import taskctl
            if initial_workflow == 'planning':
                taskctl.initialize(book)
            else:
                taskctl.initialize(book, workflow=initial_workflow)
            task_link = '- [阶段任务与验收](任务计划与验收.md)\n'
        management_files = dict(MANAGEMENT_FILES)
        if hidden_plot_decision is not None and hidden_plot_decision['value'] == 'enabled':
            management_files[HIDDEN_PLOT_FILE] = HIDDEN_PLOT_TEMPLATE
        for name, content in management_files.items():
            target = book / name
            target.parent.mkdir(exist_ok=True)
            atomic_write(target, content)
        management_links = ''.join('- [' + Path(name).stem + '](../' + name + ')\n'
                                   for name in management_files)
        atomic_write(book / '创作约定/写作进度与接续说明.md', '# 写作进度与接续说明\n\n文件约定版本：1\n当前方案：待定；正式章节：无；故事时点：待定。\n作者参与与资料建设模式：待记录真实约定；尚无后续创作授权。\n' + hidden_plot_status + '\n## 交接入口\n\n- [资料索引](索引.md)\n- [全书索引](../索引.md)\n' + task_link + management_links + '\n## 待补充与下一步\n\n创作意图、语言标准与代表片段、阶段目标、故事线、人物成长与关系、近期事件、伏笔、知情与资源状态均待建立。\n独立阅读反馈与构思风险待记录。作者决定、未核实项、冲突与过期资料待核对。\n写前必读：本文件及全书索引；尚未进行正文与文学检查。\n中断更新事项以作品状态.json 的 pending_operations 为准。\n')
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


def adoption_references(data):
    """Conservatively find operation pointers in ordinary JSON/ledger bytes."""
    matches = re.findall(
        rb'"(?:adoption_operation_id|operation_id)"\s*:\s*"([0-9a-f]{32})"|chapter-([0-9a-f]{32})', data)
    return {value.decode('ascii') for pair in matches for value in pair if value}


def adoption_inventory(root):
    """Inventory only direct adoption archives, never recursive book snapshots."""
    root = root_path(root)
    history = relative_path(root, HISTORY)
    files, directories, operations, references = {}, {'.', HISTORY}, set(), set()
    if history.exists():
        for archive in sorted(history.iterdir()):
            contained_path(root, archive)
            if not re.fullmatch(r'chapter-[0-9a-f]{32}', archive.name):
                continue
            if not archive.is_dir():
                raise ValueError('adoption archive is not a directory: ' + archive.name)
            operation = archive.name[len('chapter-'):]
            operations.add(operation)
            local_files, local_dirs = inventory(archive)
            prefix = HISTORY + '/' + archive.name
            files.update({prefix + '/' + name: sha for name, sha in local_files.items()})
            directories.update(prefix if name == '.' else prefix + '/' + name for name in local_dirs)
            for name in ('journal.json', 'state.json'):
                source = archive / name
                if source.is_file():
                    references.update(adoption_references(source.read_bytes()))
            import chapterctl
            journal = chapterctl.validate_adoption_archive(root, operation)
            if journal['phase'] == 'completed' or (archive / 'completion.json').exists():
                chapterctl.validate_sync_receipt(root, operation)
    for name in (STATE, '创作约定/任务计划与验收.md'):
        source = relative_path(root, name)
        if source.is_file():
            references.update(adoption_references(source.read_bytes()))
    if references - operations:
        raise ValueError('missing referenced adoption archives: ' + ', '.join(sorted(references - operations)))
    return files, sorted(directories)


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
    evidence = manifest.get('adoption_evidence')
    if evidence is not None:
        if (not isinstance(evidence, dict) or type(evidence.get('schema_version')) is not int
                or evidence['schema_version'] != 1
                or not isinstance(evidence.get('files'), dict)
                or not isinstance(evidence.get('directories'), list)):
            raise ValueError('invalid snapshot adoption evidence manifest')
        evidence_root = relative_path(snap, 'adoption-evidence')
        expected = (evidence['files'], evidence['directories'])
        if inventory(evidence_root) != expected:
            raise ValueError('snapshot adoption evidence checksum/directory mismatch')
        if adoption_inventory(evidence_root) != expected:
            raise ValueError('snapshot adoption evidence contains unsupported files')
        # The payload ledger/state may reference an archive absent from the
        # evidence even when every supplied file has a valid checksum.
        required = set()
        for name in (STATE, '创作约定/任务计划与验收.md'):
            source = relative_path(payload, name)
            if source.is_file():
                required.update(adoption_references(source.read_bytes()))
        available = {Path(name).parts[1][len('chapter-'):]
                     for name in evidence['files'] if len(Path(name).parts) >= 3}
        if required - available:
            raise ValueError('snapshot lacks referenced adoption evidence: ' + ', '.join(sorted(required - available)))
    elif any(name == 'adoption-evidence' or name.startswith('adoption-evidence/') for name in all_dirs):
        raise ValueError('snapshot adoption evidence is not bound by manifest')
    load_state(payload)
    return payload


def snapshot_book(book, label):
    root = root_path(book)
    load_state(root)
    before = inventory(root, exclude_history=True)
    before_adoptions = adoption_inventory(root)
    history = relative_path(root, HISTORY)
    # Resolve the live history index through the same link/path guard even
    # though the book payload deliberately excludes history.
    relative_path(root, HISTORY + '/索引.md')
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
        evidence_root = snap / 'adoption-evidence'
        evidence_root.mkdir()
        adoption_files, adoption_dirs = before_adoptions
        for directory in adoption_dirs:
            (evidence_root / directory).mkdir(exist_ok=True)
        for name in adoption_files:
            shutil.copy2(root / name, evidence_root / name)
        if (inventory(payload) != before or inventory(root, exclude_history=True) != before
                or inventory(evidence_root) != before_adoptions
                or adoption_inventory(root) != before_adoptions):
            raise ValueError('book changed while snapshotting')
        evidence = dict(schema_version=1, files=adoption_files, directories=adoption_dirs)
        atomic_write(snap / 'manifest.json', json.dumps(dict(schema_version=1, label=label, files=files, directories=dirs,
                                                          adoption_evidence=evidence), ensure_ascii=False, indent=2) + '\n')
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
        shutil.copytree(root / HISTORY, stage / HISTORY)
        manifest = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
        evidence = manifest.get('adoption_evidence')
        if evidence is not None:
            evidence_history = snap / 'adoption-evidence' / HISTORY
            for archive in evidence_history.iterdir():
                destination = stage / HISTORY / archive.name
                if destination.exists():
                    shutil.rmtree(destination)  # Only the uncommitted staging copy.
                shutil.copytree(archive, destination)
        try:
            adoption_inventory(stage)
        except (ValueError, OSError, TypeError, KeyError) as exc:
            detail = ('legacy snapshot has no adoption evidence backup; restore requires intact current archives or a trusted complete book backup'
                      if evidence is None else
                      'snapshot cannot repair other adoption history; quarantine only archives proven unreferenced, then retry')
            raise ValueError(detail + ': ' + str(exc)) from exc
        try:
            load_state(root)
            adoption_inventory(root)
        except (ValueError, OSError, TypeError, KeyError, AttributeError):
            # Invalid live state/history is retained exactly in the sibling
            # recovery directory, never blessed as a new trusted snapshot.
            backup = None
        else:
            backup = snapshot_book(root, 'pre-restore')
            shutil.copytree(backup, stage / HISTORY / backup.name)
            shutil.copy2(root / HISTORY / '索引.md', stage / HISTORY / '索引.md')
        index_directory(stage, stage / HISTORY)
        os.rename(root, recovery)
        try:
            os.rename(stage, root)
        except BaseException:
            os.rename(recovery, root)
            raise
        # Recovery is intentionally kept: no destructive cleanup in commit path.
        return (root / backup.relative_to(root) if backup is not None else recovery), recovery
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('init'); p.add_argument('--parent', required=True)
    p.add_argument('--title', default='', help='author-selected formal title; omit only with explicit deferral')
    p.add_argument('--work-id', help='portable neutral directory identifier, separate from the formal title')
    p.add_argument('--workflow', choices=['planning', 'materials', 'review_only'], default='planning',
                   help='initial authorized task; materials and review_only do not begin novel writing')
    p.add_argument('--title-decision', required=True,
                   help='actual author title decision: JSON object, JSON-file path or @file')
    p.add_argument('--hidden-plot-decision',
                   help='optional actual author choice, value enabled/disabled: JSON object, JSON-file path or @file')
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
            hidden_plot_decision = None
            if args.hidden_plot_decision is not None:
                raw = args.hidden_plot_decision
                hidden_plot_decision = (json.loads(raw) if raw.lstrip().startswith('{') else
                                        json.loads(Path(raw[1:] if raw.startswith('@') else raw).read_text(encoding='utf-8-sig')))
                if not isinstance(hidden_plot_decision, dict):
                    raise ValueError('hidden plot decision must be a JSON object')
            print(init_book(args.parent, args.title, title_decision=decision, work_id=args.work_id,
                            initial_workflow=args.workflow, hidden_plot_decision=hidden_plot_decision))
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
            if backup == recovery:
                print('backup_kind: raw_recovery (original files; not a verified snapshot)')
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('error: ' + str(exc), file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
