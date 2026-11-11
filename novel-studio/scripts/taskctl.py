#!/usr/bin/env python3
"""One-file task planning and acceptance, using only the standard library.

The authoritative JSON and its derived Markdown checkboxes live together in
创作约定/任务计划与验收.md. Notes outside the managed markers are preserved.
init creates S01/planning; plan accepts {id,title,workflow,chapter?,next_stage?,
end_reason?}. update accepts {stage,task,status,inputs?,outputs?,verification?,
reason?,operation_id?}; task may be its full ID or a kind within that stage.
Evidence is [{path,sha256}], verification is {agent_id,source}; identities and
acceptance are host assertions, not authentication. Core tasks cannot be skipped.
next_plan needs verification and an existing detailed next stage or end_reason,
without hashing this ledger. Other completed tasks need current output evidence.

Commands: init, plan, update, render, check [--stage ID], gate --stage ID
--before KIND, close --stage ID. All take --book DIR. plan/update --request
accept inline JSON, @file or a JSON-file path. render refreshes checkboxes after
intentional JSON edits (including optional research tasks). It does not grant
acceptance. Only the current stage can execute; close moves the cursor without
executing its successor. Plan only the current and next stage, not a whole book.
Writes use the chapter writer lock and atomic replacement, and never index.
Checks/gates are read-only with before/after inventory checks. No model calls,
literary scoring or second task-state file. Host agents must verify real work.
"""
import argparse
import copy
from contextlib import contextmanager
import json
from pathlib import Path
import re

import novelctl as nc

TASK_FILE = '创作约定/任务计划与验收.md'
BEGIN = '<!-- taskctl:begin -->'
END = '<!-- taskctl:end -->'
WORKFLOWS = {
    'planning': 'direction concept next_plan'.split(),
    'chapter': 'prepare draft first_read review revise recheck adopt sync next_plan'.split(),
    'trial': 'prepare voice_trial draft first_read review revise recheck adopt sync next_plan'.split(),
    'finalization': 'whole_read structural_revision whole_recheck finalize next_plan'.split(),
}
STATUSES = {'todo', 'doing', 'submitted', 'done', 'blocked', 'not_applicable'}
FINISHED = {'done', 'not_applicable'}
TASK_DEFAULTS = {
    'direction': ('创作方向与授权', 'materials', '记录作者目标、边界和本次授权。'),
    'concept': ('构思验证', 'writer', '以梗概或代表场景检验持续冲突与主要风险。'),
    'next_plan': ('细化下一阶段或记录结束', 'materials', '下一阶段已有具体任务，或明确本次授权结束。'),
    'prepare': ('写前资料与场景准备', 'materials', '核对相关原文、故事时点与本次场景任务。'),
    'voice_trial': ('语言与叙述试写', 'writer', '比较实际试写并记录采用的作品声音。'),
    'draft': ('正文起草', 'writer', '产出当前版本草稿及候选变化。'),
    'first_read': ('独立首次阅读', 'reviewer', '先读正文并保存未受预期答案引导的阅读反馈。'),
    'review': ('文学与连贯性评审', 'reviewer', '提供当前稿的具体证据、问题机制及处理建议。'),
    'revise': ('修订或记录保留理由', 'writer', '逐项处理意见，保留新稿和修订对照。'),
    'recheck': ('修订稿独立复核', 'reviewer', '复核实际新稿、评审及关联影响，记录当前哈希。'),
    'adopt': ('采用正式正文', 'materials', '按授权采用对应稿，记录正式正文与操作编号。'),
    'sync': ('同步作品资料', 'materials', '完成变化资料、摘要、索引和接续并清除待办。'),
    'whole_read': ('全书独立阅读', 'reviewer', '阅读全文并保存整体阅读报告。'),
    'structural_revision': ('全书结构修订', 'writer', '处理整体问题并提供修订对照及取舍理由。'),
    'whole_recheck': ('全书修订复核', 'reviewer', '复核当前全部正式正文与整体修订结果。'),
    'finalize': ('全书定稿验收', 'materials', '核验完整清单、独立阅读和修订证据。'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


@contextmanager
def reading(book):
    root = nc.root_path(book)
    before = nc.inventory(root)
    try:
        yield root
    finally:
        require(nc.inventory(root) == before, 'book changed during task validation')


@contextmanager
def writing(book):
    from chapterctl import locked  # Avoid a module-import cycle with adoption.
    root = nc.root_path(book)
    with locked(root):
        yield root


def managed(data):
    lines = [BEGIN, '当前阶段：' + data['current_stage'],
             '当前任务：' + (data['current_task'] or '本阶段任务已验收'), '', '## 阶段与子任务']
    for stage in data['stages']:
        label = str(stage['title']).replace('\n', ' ')
        lines.append('- [' + ('x' if stage['status'] == 'done' else ' ') + '] ' + stage['id'] + ' ' + label)
        for task in stage['tasks']:
            title = str(task['title']).replace('\n', ' ')
            reason = '：' + task['reason'].replace('\n', ' ') if task['status'] == 'not_applicable' else ''
            lines.append('  - [' + ('x' if task['status'] == 'done' else ' ') + '] ' + task['id'] + ' ' + title + ' (' + task['status'] + reason + ')')
    lines += ['', '## 机器可读任务数据', '```json', json.dumps(data, ensure_ascii=False, indent=2), '```']
    return '\n'.join(lines) + '\n' + END


def extract(root):
    text = nc.relative_path(root, TASK_FILE).read_text(encoding='utf-8')
    require(text.count(BEGIN) == text.count(END) == 1 and text.index(BEGIN) < text.index(END), 'invalid task managed markers')
    start, end_marker = text.index(BEGIN), text.index(END)
    # Only the managed region is authoritative. Author notes may contain JSON
    # examples (including invalid JSON) without becoming task data.
    region = text[start + len(BEGIN):end_marker]
    blocks = re.findall(r'^```json[ \t]*\n(.*?)\n```[ \t]*$', region, re.M | re.S)
    require(len(blocks) == 1, 'expected one authoritative fenced JSON block')
    data = json.loads(blocks[0])
    end = end_marker + len(END)
    return data, text, start, end


def evidence_structure(root, evidence):
    require(isinstance(evidence, list), 'evidence must be a list')
    seen = set()
    for item in evidence:
        require(isinstance(item, dict), 'invalid evidence item')
        path = item.get('path')
        nc.relative_path(root, path)
        require(path not in (TASK_FILE, nc.STATE), 'task evidence cannot hash its ledger or book state')
        require(path not in seen, 'duplicate evidence path')
        seen.add(path)
        require(isinstance(item.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', item['sha256']), 'invalid evidence sha256')


def structure(root, data):
    require(isinstance(data, dict) and type(data.get('schema_version')) is int and data['schema_version'] == 1, 'invalid task schema_version')
    stages = data.get('stages')
    require(isinstance(stages, list) and bool(stages), 'missing stages')
    stage_map, task_map = {}, {}
    for stage in stages:
        require(isinstance(stage, dict) and nonempty(stage.get('id')) and nonempty(stage.get('title')), 'invalid stage identity/title')
        require(stage['id'] not in stage_map, 'duplicate stage id')
        stage_map[stage['id']] = stage
        require(stage.get('workflow') in WORKFLOWS, 'unknown workflow')
        require(stage.get('status') in ('planned', 'active', 'done'), 'invalid stage status')
        chapter = stage.get('chapter')
        require(chapter is None or type(chapter) is int and chapter > 0, 'invalid stage chapter')
        require(stage['workflow'] not in ('chapter', 'trial') or chapter is not None, 'chapter workflow needs chapter number')
        require(stage.get('next_stage') is None or nonempty(stage['next_stage']), 'invalid next_stage')
        require(isinstance(stage.get('end_reason'), str), 'invalid end_reason')
        tasks = stage.get('tasks')
        require(isinstance(tasks, list) and bool(tasks), 'stage needs detailed tasks')
        core = WORKFLOWS[stage['workflow']]
        positions = []
        for kind in core:
            matches = [i for i, task in enumerate(tasks) if isinstance(task, dict) and task.get('kind') == kind]
            require(len(matches) == 1, 'missing or duplicate core task: ' + kind)
            positions += matches
        require(positions == sorted(positions) and tasks[-1].get('kind') == 'next_plan', 'core task order/next_plan must remain fixed')
        for task in tasks:
            require(isinstance(task, dict) and all(nonempty(task.get(k)) for k in ('id', 'kind', 'title', 'role', 'acceptance')), 'invalid task identity/acceptance')
            require(task['id'] not in task_map, 'duplicate task id')
            task_map[task['id']] = (stage, task)
            require(task.get('status') in STATUSES, 'invalid task status')
            require(task['kind'] not in core or task['status'] != 'not_applicable', 'core tasks cannot be not_applicable')
            deps = task.get('depends_on')
            require(isinstance(deps, list) and all(nonempty(dep) for dep in deps) and len(deps) == len(set(deps)), 'invalid task dependencies')
            require(isinstance(task.get('reason'), str) and isinstance(task.get('history'), list) and isinstance(task.get('operation_id'), str), 'invalid task history/reason/operation_id')
            verification = task.get('verification')
            require(verification is None or isinstance(verification, dict), 'invalid verification')
            evidence_structure(root, task.get('inputs'))
            evidence_structure(root, task.get('outputs'))
        for previous, current in zip(positions, positions[1:]):
            require(tasks[previous]['id'] in tasks[current]['depends_on'], 'core task missing required predecessor: ' + tasks[current]['kind'])
    require(data.get('current_stage') in stage_map, 'invalid current_stage')
    require(sum(stage['status'] != 'done' for stage in stages) <= 2, 'detail only the current and next stage')
    require(data.get('current_task') == '' or data.get('current_task') in task_map and task_map[data['current_task']][0]['id'] == data['current_stage'], 'invalid current_task')
    for stage in stages:
        require(stage['next_stage'] is None or stage['next_stage'] in stage_map and stage['next_stage'] != stage['id'], 'next_stage must name another detailed stage')
    for stage in stages:
        chain = set()
        cursor = stage
        while cursor is not None:
            require(cursor['id'] not in chain, 'stage next_stage cycle')
            chain.add(cursor['id'])
            cursor = stage_map[cursor['next_stage']] if cursor['next_stage'] else None
    visited, visiting = set(), set()
    def visit(task_id):
        require(task_id in task_map, 'unknown dependency: ' + task_id)
        require(task_id not in visiting, 'task dependency cycle')
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in task_map[task_id][1]['depends_on']:
            require(dependency in task_map and task_map[dependency][0]['id'] == task_map[task_id][0]['id'],
                    'task dependencies must stay within the same stage')
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)
    for task_id in task_map:
        visit(task_id)
    return stage_map, task_map


def load(root, rendered=True):
    data, text, start, end = extract(root)
    maps = structure(root, data)
    if rendered:
        require(text[start:end] == managed(data), 'task Markdown/checkboxes differ from authoritative JSON; render intentionally edited JSON')
    return data, maps


def load_plan(book):
    with reading(book) as root:
        return load(root)[0]


def save(root, data):
    structure(root, data)
    path = nc.relative_path(root, TASK_FILE)
    if path.exists():
        _, text, start, end = extract(root)
        text = text[:start] + managed(data) + text[end:]
    else:
        text = '# 任务计划与验收\n\n' + managed(data) + '\n\n## 补充记录\n\n'
    nc.atomic_write(path, text)


def new_stage(stage_id, title, workflow, chapter=None, next_stage=None, end_reason=''):
    require(workflow in WORKFLOWS, 'unknown workflow')
    tasks = []
    for kind in WORKFLOWS[workflow]:
        title_text, role, acceptance = TASK_DEFAULTS[kind]
        tasks.append(dict(id=stage_id + '.' + kind, kind=kind, title=title_text,
                          role=role,
                          status='todo', depends_on=[tasks[-1]['id']] if tasks else [],
                          inputs=[], outputs=[], acceptance=acceptance,
                          verification=None, reason='', history=[], operation_id=''))
    return dict(id=stage_id, title=title, workflow=workflow, chapter=chapter,
                status='planned', next_stage=next_stage, end_reason=end_reason, tasks=tasks)


def initialize(book):
    with writing(book) as root:
        require(not nc.relative_path(root, TASK_FILE).exists(), 'task ledger already exists')
        stage = new_stage('S01', '确定方向与验证构思', 'planning')
        stage['status'] = 'active'
        data = dict(schema_version=1, current_stage='S01', current_task=stage['tasks'][0]['id'], stages=[stage])
        save(root, data)
        return dict(ok=True, path=TASK_FILE, current_stage='S01')


def plan(book, request):
    require(isinstance(request, dict), 'plan request must be an object')
    with writing(book) as root:
        data, (stages, _) = load(root)
        stage_id = request.get('id')
        require(nonempty(stage_id), 'missing stage id')
        if stage_id in stages:
            stage = stages[stage_id]
            require(stage['status'] != 'done', 'closed stages are historical; plan a new revision stage')
            for field in ('workflow', 'chapter'):
                require(field not in request or request[field] == stage[field], 'cannot change existing ' + field)
            if any(field in request and request[field] != stage[field] for field in ('next_stage', 'end_reason')):
                next_plan = selected_task(stage, 'next_plan')
                if next_plan['status'] in FINISHED:
                    archive(next_plan, 'stage continuation changed')
                    next_plan['status'] = 'todo'
                    next_plan['verification'] = None
                    if stage_id == data['current_stage']:
                        data['current_task'] = next_plan['id']
            for field in ('title', 'next_stage', 'end_reason'):
                if field in request:
                    stage[field] = request[field]
        else:
            require(sum(stage['status'] != 'done' for stage in data['stages']) < 2, 'detail only the current and next stage')
            stage = new_stage(stage_id, request.get('title'), request.get('workflow'), request.get('chapter'), request.get('next_stage'), request.get('end_reason', ''))
            data['stages'].append(stage)
        if stage['next_stage']:
            successor = next((item for item in data['stages'] if item['id'] == stage['next_stage']), None)
            require(successor is not None and successor['status'] != 'done', 'next_stage must be an unfinished future stage')
        if stages[data['current_stage']]['status'] == 'done' and stage['status'] == 'planned':
            data['current_stage'] = stage_id
            data['current_task'] = stage['tasks'][0]['id']
        save(root, data)
        return dict(ok=True, stage=stage_id)


def task_validity(root, data, task_map):
    memo = {}
    def valid(task_id):
        if task_id in memo:
            return memo[task_id]
        stage, task = task_map[task_id]
        errors = []
        if task['status'] not in FINISHED:
            errors.append('not accepted')
        else:
            verification = task['verification']
            if not isinstance(verification, dict) or not all(nonempty(verification.get(k)) for k in ('agent_id', 'source')):
                errors.append('missing main-agent verification')
            if task['status'] == 'not_applicable' and not nonempty(task['reason']):
                errors.append('not_applicable needs reason')
            if task['status'] == 'done' and task['kind'] != 'next_plan' and not task['outputs']:
                errors.append('missing output evidence')
            if task['status'] == 'done' and task['kind'] == 'next_plan' and not (stage['next_stage'] or nonempty(stage['end_reason'])):
                errors.append('next_plan needs a detailed next_stage or end_reason')
            if task['status'] == 'done' and task['kind'] == 'next_plan' and stage['next_stage'] and stage['status'] != 'done':
                successor = next(item for item in data['stages'] if item['id'] == stage['next_stage'])
                if successor['status'] == 'done':
                    errors.append('next_stage must be an unfinished future stage')
            for item in task['inputs'] + task['outputs']:
                path = nc.relative_path(root, item['path'])
                if not path.is_file() or nc.digest(path) != item['sha256']:
                    errors.append('stale evidence: ' + item['path'])
            for dependency in task['depends_on']:
                if valid(dependency):
                    errors.append('invalid dependency: ' + dependency)
        memo[task_id] = errors
        return errors
    return valid


def selected_task(stage, name):
    matches = [task for task in stage['tasks'] if name in (task['id'], task['kind'])]
    require(len(matches) == 1, 'task must identify one task in the stage')
    return matches[0]


def archive(task, action):
    task['history'].append(dict(action=action, **{key: copy.deepcopy(value) for key, value in task.items() if key != 'history'}))


def update(book, request):
    require(isinstance(request, dict), 'update request must be an object')
    with writing(book) as root:
        data, (stages, task_map) = load(root)
        stage = stages.get(request.get('stage'))
        require(stage is not None and stage['id'] == data['current_stage'] and stage['status'] != 'done', 'only the current unclosed stage can execute')
        task = selected_task(stage, request.get('task'))
        status = request.get('status')
        require(status in STATUSES, 'invalid task status')
        if task['kind'] == 'sync' and status == 'done':
            require(not nc.load_state(root)['pending_operations'], 'pending_operations must be cleared before accepting sync')
        if status in ('doing', 'submitted', 'done', 'not_applicable'):
            valid = task_validity(root, data, task_map)
            require(all(not valid(dep) for dep in task['depends_on']), 'task prerequisites are incomplete or stale')
        changed_acceptance = task['status'] in FINISHED and status in FINISHED and any(
            field in request and request[field] != task[field]
            for field in ('inputs', 'outputs', 'reason', 'operation_id'))
        if changed_acceptance:
            verification = request.get('verification')
            require(isinstance(verification, dict) and all(nonempty(verification.get(key)) for key in ('agent_id', 'source')),
                    'changed accepted evidence requires new main-agent verification')
        archive(task, 'update')
        reopened = task['status'] in FINISHED and status not in FINISHED
        for field in ('inputs', 'outputs', 'verification', 'reason', 'operation_id'):
            if field in request:
                task[field] = copy.deepcopy(request[field])
        task['status'] = status
        if reopened or changed_acceptance:
            if reopened:
                task['verification'] = None
            affected = {task['id']}
            changed = True
            while changed:
                changed = False
                for downstream in stage['tasks']:
                    if downstream['id'] not in affected and affected.intersection(downstream['depends_on']):
                        affected.add(downstream['id'])
                        changed = True
                        if downstream['status'] in FINISHED:
                            archive(downstream, 'reopened dependency')
                            downstream['status'] = 'todo'
                            downstream['verification'] = None
        structure(root, data)
        if status in FINISHED:
            errors = task_validity(root, data, task_map)(task['id'])
            require(not errors, 'task cannot be accepted: ' + '; '.join(errors))
        stage['status'] = 'active'
        data['current_task'] = next((item['id'] for item in stage['tasks'] if item['status'] not in FINISHED), '')
        save(root, data)
        return dict(ok=True, stage=stage['id'], task=task['id'], status=status)


def render(book):
    with writing(book) as root:
        data, _ = load(root, rendered=False)
        save(root, data)
        return dict(ok=True, path=TASK_FILE)


def enabled(root):
    return nc.relative_path(root, TASK_FILE).exists() or nc.load_state(root).get('task_management') is True


def check(book, stage=None):
    issues, stale = [], []
    try:
        with reading(book) as root:
            if not enabled(root):
                return dict(ok=True, enabled=False, issues=[], stale_tasks=[])
            data, (stages, task_map) = load(root)
            selected = stages.get(stage if stage is not None else data['current_stage'])
            require(selected is not None, 'unknown stage')
            valid = task_validity(root, data, task_map)
            for task in selected['tasks']:
                errors = valid(task['id']) if task['status'] in FINISHED else [dep + ': invalid dependency' for dep in task['depends_on'] if valid(dep)] if task['status'] in ('doing', 'submitted') else []
                if errors:
                    stale.append(task['id'])
                    issues.append(task['id'] + ': ' + '; '.join(errors))
            if selected['status'] == 'done' and any(task['status'] not in FINISHED for task in selected['tasks']):
                issues.append('closed stage has unaccepted tasks')
            result = dict(ok=not issues, enabled=True, stage=selected['id'], issues=issues, stale_tasks=stale)
        return result
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        return dict(ok=False, enabled=True, issues=[str(exc)], stale_tasks=stale)


def gate(book, stage, before):
    with reading(book) as root:
        data, (stages, task_map) = load(root)
        selected = stages.get(stage)
        require(selected is not None, 'unknown stage')
        require(selected['id'] == data['current_stage'] or selected['status'] == 'done', 'future stage cannot execute before current stage closes')
        task = selected_task(selected, before)
        valid = task_validity(root, data, task_map)
        for dependency in task['depends_on']:
            errors = valid(dependency)
            require(not errors, dependency + ': ' + '; '.join(errors))
        return dict(ok=True, stage=stage, before=task['kind'])


def close_stage(book, stage):
    with writing(book) as root:
        data, (stages, task_map) = load(root)
        selected = stages.get(stage)
        require(selected is not None and stage == data['current_stage'] and selected['status'] != 'done', 'only the current unclosed stage can close')
        valid = task_validity(root, data, task_map)
        for task in selected['tasks']:
            errors = valid(task['id'])
            require(not errors, task['id'] + ': ' + '; '.join(errors))
        require(not nc.load_state(root)['pending_operations'], 'pending_operations prevent closing a stage')
        selected['status'] = 'done'
        if selected['next_stage']:
            data['current_stage'] = selected['next_stage']
            data['current_task'] = next((task['id'] for task in stages[selected['next_stage']]['tasks'] if task['status'] not in FINISHED), '')
        else:
            data['current_task'] = ''
        save(root, data)
        return dict(ok=True, stage=stage, current_stage=data['current_stage'])


def choose_stage(data, explicit, workflows, chapter=None, allow_done=False):
    matches = [stage for stage in data['stages'] if stage['workflow'] in workflows and
               (chapter is None or stage['chapter'] == chapter) and
               (allow_done or stage['status'] != 'done')]
    if explicit is not None:
        matches = [stage for stage in matches if stage['id'] == explicit]
    elif allow_done:
        active = [stage for stage in matches if stage['status'] == 'active']
        matches = active or [stage for stage in matches if stage['status'] == 'done'][-1:]
    require(len(matches) == 1, 'task stage must uniquely match the requested workflow/chapter')
    return matches[0]


def evidence_covers(evidence, expected):
    indexed = {item['path']: item['sha256'] for item in evidence}
    return all(indexed.get(path) == sha for path, sha in expected.items())


def require_adoption(book, request):
    with reading(book) as root:
        if not enabled(root):
            return None
        data, _ = load(root)
        stage = choose_stage(data, request.get('task_stage'), ('chapter', 'trial'), request.get('number'))
        gate(root, stage['id'], 'adopt')
        recheck = selected_task(stage, 'recheck')
        draft = request.get('draft')
        sha = nc.digest(nc.relative_path(root, draft))
        require(evidence_covers(recheck['inputs'], {draft: sha}), 'recheck inputs do not cover current draft')
        reviews = {path: nc.digest(nc.relative_path(root, path)) for path in request.get('reviews', [])}
        require(bool(reviews) and evidence_covers(recheck['outputs'], reviews), 'recheck outputs do not cover current reviews')
        return stage['id']


def require_sync(book, pending):
    with reading(book) as root:
        if not enabled(root):
            return None
        data, _ = load(root)
        stage = choose_stage(data, pending.get('request', {}).get('task_stage'), ('chapter', 'trial'), pending.get('number'))
        gate(root, stage['id'], 'sync')
        adopted = selected_task(stage, 'adopt')
        require(adopted['operation_id'] == pending.get('operation_id') and nonempty(adopted['operation_id']), 'adopt task operation_id does not match pending adoption')
        require(evidence_covers(adopted['outputs'], {pending['target']: pending['draft_sha256']}), 'adopt outputs do not cover installed formal chapter')
        return stage['id']


def require_finalization(book, record):
    with reading(book) as root:
        if not enabled(root):
            return None
        data, _ = load(root)
        stage = choose_stage(data, record.get('task_stage'), ('finalization',), allow_done=True)
        gate(root, stage['id'], 'finalize')
        recheck = selected_task(stage, 'whole_recheck')
        chapters = {chapter['path']: chapter['sha256'] for chapter in nc.load_state(root)['chapters']}
        require(bool(chapters) and evidence_covers(recheck['inputs'], chapters), 'whole_recheck inputs do not cover the current complete formal book')
        return stage['id']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'plan', 'update', 'render', 'check', 'gate', 'close'):
        sub = subs.add_parser(command)
        sub.add_argument('--book', required=True)
        if command in ('plan', 'update'):
            sub.add_argument('--request', required=True)
        if command in ('check', 'gate', 'close'):
            sub.add_argument('--stage', required=command != 'check')
        if command == 'gate':
            sub.add_argument('--before', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ('plan', 'update'):
            raw = args.request
            request = json.loads(raw) if raw.lstrip().startswith('{') else json.loads(Path(raw[1:] if raw.startswith('@') else raw).read_text(encoding='utf-8'))
            result = globals()[args.command](args.book, request)
        elif args.command == 'check':
            result = check(args.book, args.stage)
        elif args.command == 'gate':
            result = gate(args.book, args.stage, args.before)
        elif args.command == 'close':
            result = close_stage(args.book, args.stage)
        else:
            result = (initialize if args.command == 'init' else render)(args.book)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 1
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
