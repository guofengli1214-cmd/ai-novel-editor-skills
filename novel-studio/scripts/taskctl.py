#!/usr/bin/env python3
"""One-file task planning and acceptance, using only the standard library.

The authoritative JSON and its derived Markdown checkboxes live together in
创作约定/任务计划与验收.md. Notes outside the managed markers are preserved.
init creates S01/planning; plan accepts {id,title,workflow,chapter?,next_stage?,
end_reason?,direction_gate_version?}. update accepts {stage,task,status,inputs?,outputs?,verification?,
reason?,operation_id?,checks?}; submit uses the same request without status and
registers actual outputs as submitted, never accepted. New stages default to
workflow_version 2; a missing version means historical version 1.
New v2 planning stages also use direction_gate_version 1: direction checks
record resolved author decisions before submission or acceptance. This checks
declared fields and evidence, not whether quoted words prove real authorization.
New v2 stages use decision_gate_version 1 for decisive choices: author-supplied
choices or actual questions/answers; delegated choices require the explicit
all-recommendations/no-further-questions exception. Old stages are not upgraded.
New stages also use execution_gate_version 1; an existing current unclosed stage
may explicitly enable it with plan. Execution stops for unregistered or unordered
artifacts. recover records current provenance/disposition in this same ledger;
it changes neither artifacts nor task status. update recovery_only:true permits
only verified administrative repair of existing evidence, never starting work.
Evidence is [{path,sha256}], verification is {agent_id,source}; identities and
acceptance are host assertions, not authentication. Core tasks cannot be skipped.
next_plan needs verification and an existing detailed next stage or end_reason,
without hashing this ledger. Other completed tasks need current output evidence.

Commands: init, plan, update, submit, recover, render, check [--stage ID], gate --stage ID
--before KIND, close --stage ID. All take --book DIR. plan/update --request
accept inline JSON, @file or a JSON-file path. render refreshes checkboxes after
intentional JSON edits (including optional research tasks). It does not grant
acceptance. Only the current stage can execute; close moves the cursor without
executing its successor. Plan only the current and next stage, not a whole book.
Writes use the chapter writer lock and atomic replacement, and never index.
Checks/gates are read-only with before/after inventory checks. No model calls,
literary scoring or second task-state file. Host agents must verify real work.
gate --neutral returns only mechanical IDs and gate_not_ready on any validation
failure. Use it for independent first readers; ordinary gate/check diagnostics
may contain private source paths and belong to the orchestrator.
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
    'direction': ('创作方向与授权', 'materials', '依据真实答复或明确委托记录偏好、资料模式和本轮授权；关键未决项先问并等待。'),
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
EDITORIAL_CHECKS = {
    'direction': ('author_preferences', 'startup_mode', 'authorization'),
    'concept': ('story_engine',),
    'prepare': ('author_preferences', 'chapter_design', 'global_direction', 'source_status', 'prose_policy'),
    'voice_trial': ('style_scope',),
    'first_read': ('reader_experience',),
    'review': ('editorial_findings', 'global_direction'),
    'revise': ('issue_disposition',),
    'recheck': ('current_version', 'issue_disposition'),
    'sync': ('source_status', 'handoff'),
    'next_plan': ('global_direction', 'handoff'),
    'whole_read': ('reader_experience', 'global_direction'),
    'structural_revision': ('issue_disposition',),
    'whole_recheck': ('current_version', 'issue_disposition'),
}
DIRECTION_DECISIONS = {
    'author_preferences': ('experience', 'style', 'length'),
    'authorization': ('collaboration', 'work_scope'),
}
DECISION_RESOLUTIONS = {'provided', 'selected', 'delegated', 'unresolved'}
CREATIVE_DECISION_TASKS = {'concept', 'prepare', 'revise', 'next_plan', 'structural_revision'}
TITLE_PRIOR_STATUSES = {'proposed', 'active', 'retired', 'rejected', 'unknown'}
RECOVERY_CLASSES = {'author_input', 'held_output', 'excluded'}


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


def workflow_version(stage):
    value = stage.get('workflow_version', 1)
    require(type(value) is int and value in (1, 2), 'unsupported workflow_version')
    return value


def check_structure(root, task):
    checks = task.get('checks', {})
    require(isinstance(checks, dict), 'checks must be an object')
    for name, item in checks.items():
        require(nonempty(name) and isinstance(item, dict) and nonempty(item.get('summary')), 'invalid editorial check')
        evidence_structure(root, [item])


def decision_errors(decision, topic, strict=False, expected_title=None):
    if not isinstance(decision, dict):
        return ['missing author decision: ' + topic]
    resolution = decision.get('resolution')
    if not isinstance(resolution, str) or resolution not in DECISION_RESOLUTIONS:
        return ['invalid author decision resolution: ' + topic]
    if resolution == 'unresolved':
        return ['author decision unresolved: ' + topic]
    errors = []
    if not all(nonempty(decision.get(key)) for key in ('value', 'source_quote', 'scope')):
        errors.append('resolved author decision needs value/source_quote/scope: ' + topic)
    if not strict:
        return errors
    if resolution == 'selected' and not nonempty(decision.get('question_quote')):
        errors.append('selected decision requires actual question_quote: ' + topic)
    if resolution == 'delegated':
        opt_out = decision.get('opt_out')
        if (not isinstance(opt_out, dict)
                or not all(nonempty(opt_out.get(key)) for key in ('question_quote', 'answer_quote', 'scope'))
                or opt_out.get('all_recommendations') is not True
                or opt_out.get('no_further_questions') is not True):
            errors.append('delegated decision requires explicit all-recommendations/no-further-questions answer: ' + topic)
    if expected_title is not None:
        title = decision.get('value')
        if title != expected_title:
            errors.append('selected title differs from current book state: ' + topic)
        prior = decision.get('prior_status')
        if not isinstance(prior, str) or prior not in TITLE_PRIOR_STATUSES:
            errors.append('title requires valid prior_status: ' + topic)
        if prior in ('retired', 'rejected'):
            if not nonempty(decision.get('history_source')):
                errors.append('retired/rejected title requires history_source: ' + topic)
            reuse = decision.get('reuse_confirmation')
            if (not isinstance(reuse, dict) or reuse.get('acknowledges_retired') is not True
                    or not nonempty(reuse.get('answer_quote')) or not nonempty(title)):
                errors.append('retired/rejected title requires explicit named reuse confirmation: ' + topic)
            elif resolution == 'provided':
                if not nonempty(decision.get('source_quote')) or title not in decision['source_quote']:
                    errors.append('proactively restored title source must explicitly name the title: ' + topic)
                if title not in reuse['answer_quote']:
                    errors.append('title reuse answer must explicitly name the title: ' + topic)
            elif not nonempty(reuse.get('question_quote')) or title not in reuse['question_quote']:
                errors.append('retired/rejected title requires explicit named reuse confirmation: ' + topic)
            elif resolution == 'delegated':
                opt_out = decision.get('opt_out')
                if not isinstance(opt_out, dict) or reuse['answer_quote'] != opt_out.get('answer_quote'):
                    errors.append('title reuse answer must match the actual opt-out answer: ' + topic)
                if (not isinstance(opt_out, dict) or not nonempty(opt_out.get('question_quote'))
                        or title not in opt_out['question_quote']):
                    errors.append('title reuse opt-out question must explicitly name the title: ' + topic)
            elif title not in reuse['answer_quote']:
                if (resolution != 'selected' or reuse.get('selected_title') != title
                        or decision.get('source_quote') != reuse['answer_quote']):
                    errors.append('title reuse answer must name the title or preserve an explicit selected option mapping: ' + topic)
    return errors


def title_decision_errors(title, decision):
    """Pure pre-init/title-change gate; quotes are assertions, not authentication."""
    errors = decision_errors(decision, 'title', strict=True, expected_title=title)
    if not nonempty(title):
        errors.append('title must be a nonempty string')
    return errors


def direction_errors(stage, task, root=None):
    """Verify declared readiness, never interpret author prose or invent consent."""
    if stage.get('direction_gate_version') != 1 or task['kind'] != 'direction':
        return []
    checks, errors = task.get('checks', {}), []
    decisions = []
    for category, topics in DIRECTION_DECISIONS.items():
        group = checks.get(category, {}).get('decisions', {})
        for topic in topics:
            decisions.append((category + '.' + topic, group.get(topic) if isinstance(group, dict) else None))
    decisions.append(('startup_mode', checks.get('startup_mode', {}).get('decision')))
    strict = stage.get('decision_gate_version') == 1
    if strict:
        preferences = checks.get('author_preferences', {}).get('decisions', {})
        decisions.append(('author_preferences.title', preferences.get('title') if isinstance(preferences, dict) else None))
    for topic, decision in decisions:
        if topic == 'author_preferences.title' and root is not None:
            errors.extend(title_decision_errors(nc.load_state(root)['title'], decision))
        else:
            errors.extend(decision_errors(decision, topic, strict=strict))
    return errors


def creative_decision_errors(root, stage, task):
    if stage.get('decision_gate_version') != 1 or task['kind'] not in CREATIVE_DECISION_TASKS:
        return []
    check = task.get('checks', {}).get('creative_decisions')
    if not isinstance(check, dict):
        return ['missing editorial check: creative_decisions']
    items = check.get('items')
    if not isinstance(items, list):
        return ['creative_decisions requires an items list']
    if not items:
        return [] if nonempty(check.get('none_reason')) else ['no new decisions requires none_reason']
    seen, errors = set(), []
    for item in items:
        if not isinstance(item, dict) or not all(nonempty(item.get(key)) for key in ('id', 'topic', 'record_id')):
            errors.append('creative decision requires id/topic/record_id')
            continue
        if item['id'] in seen:
            errors.append('duplicate creative decision id: ' + item['id'])
        seen.add(item['id'])
        expected_title = nc.load_state(root)['title'] if item['topic'] == 'title' else None
        errors.extend(decision_errors(item, item['topic'], strict=True, expected_title=expected_title))
    return errors


def decision_preflight_errors(root, stage, task):
    errors = creative_decision_errors(root, stage, task)
    if stage.get('decision_gate_version') != 1 or task['kind'] not in CREATIVE_DECISION_TASKS:
        return errors
    check = task.get('checks', {}).get('creative_decisions')
    if not isinstance(check, dict):
        return errors
    evidence = {item['path']: item['sha256'] for item in task['inputs'] + task['outputs']}
    if evidence.get(check.get('path')) != check.get('sha256'):
        errors.append('creative decision preflight must reference registered task evidence')
    else:
        path = nc.relative_path(root, check['path'])
        if not path.is_file() or nc.digest(path) != check['sha256']:
            errors.append('stale creative decision preflight evidence')
    return errors


def editorial_errors(root, stage, task):
    strict_direction = stage.get('direction_gate_version') == 1 and task['kind'] == 'direction'
    if (workflow_version(stage) < 2 and not strict_direction) or task['status'] == 'not_applicable':
        return []
    checks = task.get('checks', {})
    errors = direction_errors(stage, task, root) + creative_decision_errors(root, stage, task)
    references = {item['path']: item['sha256'] for item in task['inputs'] + task['outputs']}
    required = list(EDITORIAL_CHECKS.get(task['kind'], ()))
    if stage.get('decision_gate_version') == 1 and task['kind'] in CREATIVE_DECISION_TASKS:
        required.append('creative_decisions')
    for name in required:
        item = checks.get(name)
        if not item:
            errors.append('missing editorial check: ' + name)
        elif references.get(item['path']) != item['sha256']:
            errors.append('editorial check must reference task evidence: ' + name)
    return errors


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
        workflow_version(stage)
        if 'direction_gate_version' in stage:
            require(stage['workflow'] == 'planning' and type(stage['direction_gate_version']) is int
                    and stage['direction_gate_version'] == 1, 'invalid direction_gate_version (planning only, version 1)')
        if 'decision_gate_version' in stage:
            require(workflow_version(stage) == 2 and type(stage['decision_gate_version']) is int
                    and stage['decision_gate_version'] == 1, 'invalid decision_gate_version (workflow v2 only, version 1)')
            require(stage['workflow'] != 'planning' or stage.get('direction_gate_version') == 1,
                    'decision gate planning requires the author direction gate')
        if 'execution_gate_version' in stage:
            require(type(stage['execution_gate_version']) is int and stage['execution_gate_version'] == 1,
                    'invalid execution_gate_version (version 1)')
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
            check_structure(root, task)
        for previous, current in zip(positions, positions[1:]):
            require(tasks[previous]['id'] in tasks[current]['depends_on'], 'core task missing required predecessor: ' + tasks[current]['kind'])
    require(data.get('current_stage') in stage_map, 'invalid current_stage')
    if any(stage.get('execution_gate_version') == 1 for stage in stages):
        current_position = next(i for i, stage in enumerate(stages) if stage['id'] == data['current_stage'])
        require(all(stage['status'] == 'done' for stage in stages[:current_position]),
                'earlier stages must close before advancing the stage cursor')
        require(all(stage['status'] != 'active' for stage in stages[current_position + 1:]),
                'future stages cannot be active')
        require(all(all(task['status'] in FINISHED for task in stage['tasks'])
                    for stage in stages if stage['status'] == 'done'),
                'closed stages cannot contain unaccepted tasks')
        current = stage_map[data['current_stage']]
        expected_cursor = next((task['id'] for task in current['tasks'] if task['status'] not in FINISHED), '')
        require(data.get('current_task') == expected_cursor, 'task cursor must match the first unaccepted task')
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
    recoveries = data.get('artifact_recoveries', [])
    require(isinstance(recoveries, list), 'artifact_recoveries must be a list')
    for record in recoveries:
        require(isinstance(record, dict) and record.get('classification') in RECOVERY_CLASSES,
                'invalid artifact recovery classification')
        evidence_structure(root, [record])
        evidence_structure(root, [record.get('source')])
        require(record['source']['path'] != record['path'], 'artifact recovery needs a separate provenance source')
        require(nonempty(record.get('source_quote')) and nonempty(record.get('scope')), 'artifact recovery needs source_quote/scope')
        verification = record.get('verification')
        require(isinstance(verification, dict) and all(nonempty(verification.get(key)) for key in ('agent_id', 'source')),
                'artifact recovery needs host verification')
        if record['classification'] == 'held_output':
            require(record.get('task_id') in task_map, 'held_output needs an existing task_id')
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


def new_stage(stage_id, title, workflow, chapter=None, next_stage=None, end_reason='', workflow_version=2):
    require(workflow in WORKFLOWS, 'unknown workflow')
    require(type(workflow_version) is int and workflow_version in (1, 2), 'unsupported workflow_version')
    tasks = []
    for kind in WORKFLOWS[workflow]:
        title_text, role, acceptance = TASK_DEFAULTS[kind]
        tasks.append(dict(id=stage_id + '.' + kind, kind=kind, title=title_text,
                          role=role,
                          status='todo', depends_on=[tasks[-1]['id']] if tasks else [],
                          inputs=[], outputs=[], checks={}, acceptance=acceptance,
                          verification=None, reason='', history=[], operation_id=''))
    stage = dict(id=stage_id, title=title, workflow=workflow, workflow_version=workflow_version, chapter=chapter,
                 status='planned', next_stage=next_stage, end_reason=end_reason, tasks=tasks)
    if workflow == 'planning' and workflow_version == 2:
        stage['direction_gate_version'] = 1
    if workflow_version == 2:
        stage['decision_gate_version'] = 1
        stage['execution_gate_version'] = 1
    return stage


def initialize(book, workflow_version=2):
    with writing(book) as root:
        require(not nc.relative_path(root, TASK_FILE).exists(), 'task ledger already exists')
        stage = new_stage('S01', '确定方向与验证构思', 'planning', workflow_version=workflow_version)
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
            require('workflow_version' not in request or request['workflow_version'] == workflow_version(stage),
                    'cannot rewrite historical workflow_version; plan a new stage')
            if 'decision_gate_version' in request:
                require(type(request['decision_gate_version']) is int and request['decision_gate_version'] == 1
                        and stage.get('decision_gate_version') == 1,
                        'decision gate cannot be downgraded or added to historical stages; plan a new stage')
            if 'execution_gate_version' in request:
                require(type(request['execution_gate_version']) is int and request['execution_gate_version'] == 1,
                        'execution gate cannot be disabled')
                require(stage_id == data['current_stage'] or stage.get('execution_gate_version') == 1,
                        'upgrade only the current unclosed stage execution gate')
                stage['execution_gate_version'] = 1
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
            if request.get('workflow_version', 2) == 1:
                require(nonempty(request.get('compatibility_reason')),
                        'new legacy stage requires compatibility_reason; new work uses workflow v2')
                require(all(workflow_version(item) == 1 for item in data['stages']),
                        'workflow v2 books cannot create legacy stages to bypass editorial gates')
            stage = new_stage(stage_id, request.get('title'), request.get('workflow'), request.get('chapter'), request.get('next_stage'), request.get('end_reason', ''), request.get('workflow_version', 2))
            if 'decision_gate_version' in request:
                require(type(request['decision_gate_version']) is int and request['decision_gate_version'] == 1
                        and stage.get('decision_gate_version') == 1, 'new decision gate cannot be disabled')
            if 'execution_gate_version' in request:
                require(type(request['execution_gate_version']) is int and request['execution_gate_version'] == 1,
                        'execution gate cannot be disabled')
                stage['execution_gate_version'] = 1
            if 'compatibility_reason' in request:
                stage['compatibility_reason'] = request['compatibility_reason']
            data['stages'].append(stage)
        if 'direction_gate_version' in request:
            require(stage['workflow'] == 'planning' and type(request['direction_gate_version']) is int
                    and request['direction_gate_version'] == 1, 'direction gate cannot be downgraded or used outside planning')
            if stage.get('direction_gate_version') != 1:
                direction = selected_task(stage, 'direction')
                require(direction['status'] not in FINISHED,
                        'accepted direction is historical; use a new planning stage for author decisions')
                archive(direction, 'direction gate upgraded before acceptance')
                stage['direction_gate_version'] = 1
                if stage_id == data['current_stage']:
                    data['current_task'] = direction['id']
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
            errors.extend(editorial_errors(root, stage, task))
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
        enforced = stage.get('execution_gate_version') == 1
        recovery_only = request.get('recovery_only', False)
        require(type(recovery_only) is bool, 'recovery_only must be a boolean')
        require(not recovery_only or enforced, 'recovery_only requires an explicit execution gate upgrade')
        before_status = task['status']
        before_outputs = {(item['path'], item['sha256']) for item in task['outputs']}
        requested_outputs = request.get('outputs', task['outputs'])
        if enforced:
            evidence_structure(root, requested_outputs)
            new_outputs = {(item['path'], item['sha256']) for item in requested_outputs} - before_outputs
            require(not new_outputs or status not in ('todo', 'blocked', 'doing'),
                    'new output evidence requires ordered submission; todo/blocked/doing cannot preregister outputs')
            if status == 'doing' and before_status != 'doing':
                candidates = set(artifact_candidates(root))
                known = known_artifact_pairs(root, data)
                require(not (before_outputs & candidates) - known,
                        'preexisting output needs provenance recovery before starting the task')
            if recovery_only:
                require(status != 'doing', 'recovery_only never authorizes creative execution')
                verification = request.get('verification')
                require(nonempty(request.get('reason')) and isinstance(verification, dict)
                        and all(nonempty(verification.get(key)) for key in ('agent_id', 'source')),
                        'recovery_only needs reason and fresh host verification')
                known = known_artifact_pairs(root, data)
                require(all((item['path'], item['sha256']) in known for item in requested_outputs),
                        'recovery_only outputs need existing registered provenance; use recover first')
            elif status in ('submitted', 'done', 'not_applicable') and before_status != 'doing':
                candidates = set(artifact_candidates(root))
                known = known_artifact_pairs(root, data)
                require(not ({(item['path'], item['sha256']) for item in requested_outputs} & candidates) - known,
                        'existing output needs provenance recovery or a task started before production')
        if task['kind'] == 'sync' and status == 'done':
            require(not nc.load_state(root)['pending_operations'], 'pending_operations must be cleared before accepting sync')
        if status in ('doing', 'submitted', 'done', 'not_applicable'):
            valid = task_validity(root, data, task_map)
            require(all(not valid(dep) for dep in task['depends_on']), 'task prerequisites are incomplete or stale')
        changed_acceptance = task['status'] in FINISHED and status in FINISHED and any(
            field in request and request[field] != task.get(field, {})
            for field in ('inputs', 'outputs', 'checks', 'reason', 'operation_id'))
        if changed_acceptance:
            verification = request.get('verification')
            require(isinstance(verification, dict) and all(nonempty(verification.get(key)) for key in ('agent_id', 'source')),
                    'changed accepted evidence requires new main-agent verification')
        archive(task, 'update')
        reopened = task['status'] in FINISHED and status not in FINISHED
        for field in ('inputs', 'outputs', 'checks', 'verification', 'reason', 'operation_id'):
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
        data['current_task'] = next((item['id'] for item in stage['tasks'] if item['status'] not in FINISHED), '')
        structure(root, data)
        if enforced and status in ('doing', 'submitted', 'done', 'not_applicable'):
            if status == 'doing':
                errors = decision_preflight_errors(root, stage, task)
                require(not errors, '; '.join(errors))
                for item in task['inputs'] + task['outputs']:
                    path = nc.relative_path(root, item['path'])
                    require(path.is_file() and nc.digest(path) == item['sha256'],
                            'stale execution evidence: ' + item['path'])
            blockers = artifact_blockers(root, data, task_map)
            require(recovery_only or not blockers, '; '.join(blockers))
        if status == 'submitted':
            require(bool(task['outputs']), 'submitted task needs output evidence')
            for item in task['inputs'] + task['outputs']:
                path = nc.relative_path(root, item['path'])
                require(path.is_file() and nc.digest(path) == item['sha256'], 'stale submitted evidence: ' + item['path'])
            require(not editorial_errors(root, stage, task), '; '.join(editorial_errors(root, stage, task)))
        if status in FINISHED:
            errors = task_validity(root, data, task_map)(task['id'])
            require(not errors, 'task cannot be accepted: ' + '; '.join(errors))
        stage['status'] = 'active'
        data['current_task'] = next((item['id'] for item in stage['tasks'] if item['status'] not in FINISHED), '')
        save(root, data)
        result = dict(ok=True, stage=stage['id'], task=task['id'], status=status)
        if recovery_only:
            result['recovery_only'] = True
        return result


def submit(book, request):
    require(isinstance(request, dict), 'submit request must be an object')
    require('status' not in request or request['status'] == 'submitted', 'submit never accepts tasks')
    return update(book, dict(request, status='submitted'))


def artifact_candidates(root):
    """Conservative filename inventory; provenance comes from explicit records."""
    result = []
    for folder in ('草稿与备选', '审稿与修订', '大纲'):
        directory = root / folder
        if not directory.exists():
            continue
        for path in sorted(directory.rglob('*')):
            substantive = (path.name != '索引.md'
                           and path.suffix.lower() in ('.md', '.txt', '.json'))
            if path.is_file() and (substantive or re.search(r'第\d+章.*(?:草稿|首读|评审|复核|修订)', path.name)):
                result.append((path.relative_to(root).as_posix(), nc.digest(path)))
    return result


def recovery_source_errors(root, record):
    source = record['source']
    path = nc.relative_path(root, source['path'])
    if not path.is_file() or nc.digest(path) != source['sha256']:
        return ['stale recovery provenance: ' + source['path']]
    if record['source_quote'] not in path.read_text(encoding='utf-8-sig'):
        return ['recovery source_quote is absent from provenance: ' + source['path']]
    return []


def recovery_index(data):
    return {record['path']: record for record in data.get('artifact_recoveries', [])}


def artifact_owner_matches(path, stage, task):
    chapter = re.search(r'第(\d+|[零〇一二三四五六七八九十百千两]+)章', Path(path).name)
    if path.startswith('草稿与备选/') and chapter:
        if task['kind'] == 'structural_revision' and stage['workflow'] == 'finalization':
            return True
        return (stage['workflow'] in ('chapter', 'trial')
                and task['kind'] in ('voice_trial', 'draft', 'revise')
                and (not chapter.group(1).isdigit() or int(chapter.group(1)) == stage['chapter']))
    return True


def artifact_blockers(root, data, task_map=None):
    """Find current unaccounted output; inputs alone never establish provenance."""
    if task_map is None:
        _, task_map = structure(root, data)
    valid = task_validity(root, data, task_map)
    recoveries = recovery_index(data)
    current, historical = {}, set()
    for stage in data['stages']:
        for task in stage['tasks']:
            for item in task['outputs']:
                current.setdefault((item['path'], item['sha256']), []).append((stage, task))
            for record in task['history']:
                if isinstance(record, dict) and record.get('status') in FINISHED and isinstance(record.get('verification'), dict):
                    historical.update((item['path'], item['sha256']) for item in record.get('outputs', []))
    blockers = []
    for path, sha in artifact_candidates(root):
        pair = path, sha
        record = recoveries.get(path)
        if record and record['sha256'] == sha:
            source_errors = recovery_source_errors(root, record)
            if source_errors:
                blockers.extend(source_errors)
                continue
            if record['classification'] == 'excluded':
                if any(pair in {(item['path'], item['sha256']) for item in task['inputs'] + task['outputs']}
                       for stage, task in task_map.values() if stage['status'] != 'done'):
                    blockers.append('excluded artifact cannot be active task evidence: ' + path)
                continue
        owners = current.get(pair, [])
        ready = [(stage, task) for stage, task in owners
                 if task['status'] in ('doing', 'submitted', 'done', 'not_applicable')
                 and artifact_owner_matches(path, stage, task)
                 and (stage['id'] == data['current_stage'] or stage['status'] == 'done')
                 and all(not valid(dep) for dep in task['depends_on'])]
        if record and record['sha256'] == sha and record['classification'] == 'held_output':
            if not any(task['id'] == record['task_id'] and task['status'] in FINISHED | {'submitted'} for _, task in ready):
                blockers.append('held output needs ordered recovery submission/acceptance: ' + path)
            continue
        if owners and not ready:
            blockers.append('out-of-order output is not accepted task progress: ' + path)
        elif ready or pair in historical:
            continue
        elif record and record['sha256'] == sha and record['classification'] == 'author_input':
            continue
        else:
            blockers.append('unregistered artifact requires provenance recovery: ' + path)
    return blockers


def known_artifact_pairs(root, data):
    pairs = set()
    candidates = set(artifact_candidates(root))
    for stage in data['stages']:
        for task in stage['tasks']:
            pairs.update((item['path'], item['sha256']) for item in task['inputs']
                         if (item['path'], item['sha256']) not in candidates)
            if task['status'] in ('doing', 'submitted', 'done', 'not_applicable'):
                pairs.update((item['path'], item['sha256']) for item in task['outputs'])
            for record in task['history']:
                if isinstance(record, dict) and record.get('status') in FINISHED and isinstance(record.get('verification'), dict):
                    pairs.update((item['path'], item['sha256']) for item in record.get('outputs', []))
    for record in recovery_index(data).values():
        if record['classification'] != 'excluded' and not recovery_source_errors(root, record):
            pairs.add((record['path'], record['sha256']))
    return pairs


def recover(book, request):
    """Record an existing artifact's provenance/disposition, never approve work."""
    require(isinstance(request, dict), 'recovery request must be an object')
    artifacts = request.get('artifacts')
    require(isinstance(artifacts, list) and bool(artifacts), 'recovery needs artifacts')
    verification = request.get('verification')
    require(isinstance(verification, dict) and all(nonempty(verification.get(key)) for key in ('agent_id', 'source')),
            'recovery needs host verification')
    with writing(book) as root:
        data, _ = load(root)
        seen = set()
        records = []
        for item in artifacts:
            require(isinstance(item, dict) and item.get('path') not in seen, 'invalid/duplicate recovery artifact')
            seen.add(item.get('path'))
            record = dict(copy.deepcopy(item), verification=copy.deepcopy(verification))
            records.append(record)
        data.setdefault('artifact_recoveries', []).extend(records)
        structure(root, data)
        for record in records:
            path = nc.relative_path(root, record['path'])
            require(path.is_file() and nc.digest(path) == record['sha256'], 'recovery artifact is missing or changed: ' + record['path'])
            errors = recovery_source_errors(root, record)
            require(not errors, '; '.join(errors))
        save(root, data)
        return dict(ok=True, recovered=[record['path'] for record in records],
                    note='Provenance recorded only; task status and creative authorization are unchanged.')


def artifact_warnings(root, data):
    """Historical advisory inventory; existence never proves completion."""
    registered = set()
    for stage in data['stages']:
        for task in stage['tasks']:
            for record in [task] + task.get('history', []):
                for item in record.get('inputs', []) + record.get('outputs', []):
                    registered.add((item['path'], item['sha256']))
    for record in recovery_index(data).values():
        if not recovery_source_errors(root, record):
            registered.add((record['path'], record['sha256']))
    warnings = []
    for relative, sha in artifact_candidates(root):
        if (relative, sha) not in registered:
            warnings.append('unregistered artifact (inspect before resuming): ' + relative)
    return warnings


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
                if selected.get('execution_gate_version') == 1:
                    if task['status'] == 'doing':
                        errors.extend(decision_preflight_errors(root, selected, task))
                        for item in task['inputs'] + task['outputs']:
                            path = nc.relative_path(root, item['path'])
                            if not path.is_file() or nc.digest(path) != item['sha256']:
                                errors.append('stale execution evidence: ' + item['path'])
                    if task['status'] in ('todo', 'blocked') and task['outputs'] and not task['history']:
                        errors.append('unstarted task cannot claim output evidence')
                if task['status'] == 'submitted':
                    errors.extend(editorial_errors(root, selected, task))
                    if not task['outputs']:
                        errors.append('submitted task needs output evidence')
                    for item in task['inputs'] + task['outputs']:
                        path = nc.relative_path(root, item['path'])
                        if not path.is_file() or nc.digest(path) != item['sha256']:
                            errors.append('stale submitted evidence: ' + item['path'])
                if errors:
                    stale.append(task['id'])
                    issues.append(task['id'] + ': ' + '; '.join(errors))
            if selected['status'] == 'done' and any(task['status'] not in FINISHED for task in selected['tasks']):
                issues.append('closed stage has unaccepted tasks')
            current = stages[data['current_stage']]
            expected_cursor = next((task['id'] for task in current['tasks'] if task['status'] not in FINISHED), '')
            if data['current_task'] != expected_cursor:
                issues.append('task cursor differs from actual accepted/submitted tasks')
            blockers = artifact_blockers(root, data, task_map) if current.get('execution_gate_version') == 1 else []
            issues.extend(blockers)
            result = dict(ok=not issues, enabled=True, stage=selected['id'], issues=issues, stale_tasks=stale,
                          warnings=artifact_warnings(root, data),
                          artifact_blockers=blockers,
                          resume=dict(stage=data['current_stage'], task=data['current_task'],
                                      note='Use this ledger cursor; inspect unregistered artifacts before repeating work.'))
        return result
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        return dict(ok=False, enabled=True, issues=[str(exc)], stale_tasks=stale)


def _gate_details(book, stage, before):
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
        if selected.get('execution_gate_version') == 1:
            blockers = artifact_blockers(root, data, task_map)
            require(not blockers, '; '.join(blockers))
        decision_errors = decision_preflight_errors(root, selected, task)
        require(not decision_errors, '; '.join(decision_errors))
        return dict(ok=True, stage=stage, before=task['kind'])


def gate(book, stage, before, neutral=False):
    if not neutral:
        return _gate_details(book, stage, before)
    # Echo only caller-provided mechanical identifiers. Hidden filenames,
    # titles, author choices and even inventory/load errors stay with the host.
    result = {}
    for key, value in (('stage', stage), ('before', before)):
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}', value):
            result[key] = value
    try:
        _gate_details(book, stage, before)
    except Exception:
        return dict(result, ok=False, error='gate_not_ready')
    return dict(result, ok=True)


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
        if workflow_version(stage) >= 2:
            for path in reviews:
                review = json.loads(nc.relative_path(root, path).read_text(encoding='utf-8-sig'))
                require(review.get('schema_version') == 2, 'workflow v2 requires review schema_version 2')
            prose = request.get('prose_check')
            require(isinstance(prose, dict), 'workflow v2 requires prose_check report evidence')
            evidence_structure(root, [prose])
            require(evidence_covers(recheck['outputs'], {prose['path']: prose['sha256']}),
                    'recheck outputs do not cover prose_check report')
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
    for command in ('init', 'plan', 'update', 'submit', 'recover', 'render', 'check', 'gate', 'close'):
        sub = subs.add_parser(command)
        sub.add_argument('--book', required=True)
        if command in ('plan', 'update', 'submit', 'recover'):
            sub.add_argument('--request', required=True)
        if command in ('check', 'gate', 'close'):
            sub.add_argument('--stage', required=command != 'check')
        if command == 'gate':
            sub.add_argument('--before', required=True)
            sub.add_argument('--neutral', action='store_true', help='hide private validation details from independent readers')
    args = parser.parse_args(argv)
    try:
        if args.command in ('plan', 'update', 'submit', 'recover'):
            raw = args.request
            request = json.loads(raw) if raw.lstrip().startswith('{') else json.loads(Path(raw[1:] if raw.startswith('@') else raw).read_text(encoding='utf-8'))
            result = globals()[args.command](args.book, request)
        elif args.command == 'check':
            result = check(args.book, args.stage)
        elif args.command == 'gate':
            result = gate(args.book, args.stage, args.before, neutral=args.neutral)
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
