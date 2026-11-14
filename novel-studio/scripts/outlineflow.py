"""Bounded outline review; technical declarations never certify literary merit."""
import copy
import hashlib
import json
from pathlib import Path

import novelctl as nc


READINESS = ('actionable', 'timeline', 'causal_chain', 'author_boundaries')
MAX_ROUNDS = 2


def rounds(stage):
    return sum(task['kind'] in ('outline_revision_1', 'outline_revision_2')
               for task in stage['tasks'])


def latest(stage):
    import taskctl as tc
    number = rounds(stage)
    return (tc.selected_task(stage, 'outline_revision_' + str(number) if number else 'outline_draft'),
            tc.selected_task(stage, 'outline_recheck_' + str(number) if number else 'outline_review'))


def cursor(stage):
    # A submitted candidate awaits the final decision, but does not hide the
    # actionable reviewer/revision/acceptance task behind it.
    import taskctl as tc
    return next((task['id'] for task in stage['tasks']
                 if task['status'] not in tc.FINISHED
                 and not (task['kind'] == 'outline_draft' and task['status'] == 'submitted')), '')


def validate_structure(stage):
    import taskctl as tc
    tc.require(tc.workflow_version(stage) == 2, 'outline requires workflow v2')
    kinds = [task['kind'] for task in stage['tasks']]
    previous = tc.selected_task(stage, 'outline_review')
    seen_gap = False
    for number in range(1, MAX_ROUNDS + 1):
        revision, recheck = 'outline_revision_' + str(number), 'outline_recheck_' + str(number)
        present = revision in kinds or recheck in kinds
        if not present:
            seen_gap = True
            continue
        tc.require(not seen_gap and kinds.count(revision) == kinds.count(recheck) == 1,
                   'outline revision rounds must be complete and sequential')
        producer, report = tc.selected_task(stage, revision), tc.selected_task(stage, recheck)
        tc.require(producer['depends_on'] == [previous['id']]
                   and report['depends_on'] == [producer['id']],
                   'outline round must depend on the preceding current report/candidate')
        tc.require(kinds.index(previous['kind']) < kinds.index(revision) < kinds.index(recheck)
                   < kinds.index('outline_acceptance'), 'invalid outline round order')
        previous = report
    allowed = {'outline_draft', 'outline_review', 'outline_acceptance'} | {
        prefix + str(number) for prefix in ('outline_revision_', 'outline_recheck_')
        for number in range(1, MAX_ROUNDS + 1)}
    for task in stage['tasks']:
        if task['kind'].startswith('outline_'):
            tc.require(task['kind'] in allowed and task['id'] == stage['id'] + '.' + task['kind'],
                       'unsupported outline task or revision limit exceeded')
            tc.require(task['status'] != 'not_applicable', 'outline tasks cannot be not_applicable')
        tc.require(task['kind'] not in ('draft', 'voice_trial', 'adopt', 'sync', 'finalize'),
                   'outline workflow cannot create or adopt chapter prose')
        for path, _ in tc.output_artifact_pairs(task):
            parts = Path(path).parts
            tc.require(len(parts) > 1 and parts[0] not in ('草稿与备选', nc.HISTORY),
                       'outline outputs cannot create chapter prose or write generated history')
    acceptance = tc.selected_task(stage, 'outline_acceptance')
    tc.require(acceptance['depends_on'] == [previous['id']],
               'outline acceptance must depend only on the latest report')
    draft = tc.selected_task(stage, 'outline_draft')
    tc.require(draft['status'] != 'done' or acceptance['status'] == 'done',
               'outline_draft remains submitted until final outline acceptance')


def submitted_dependency_errors(root, stage, task, dependency, valid):
    """Return None for ordinary edges, errors for the one candidate-read edge."""
    import taskctl as tc
    if (stage['workflow'] != 'outline' or task['kind'] != 'outline_review'
            or dependency['kind'] != 'outline_draft' or dependency['status'] != 'submitted'):
        return None
    errors = tc.editorial_errors(root, stage, dependency)
    for item in dependency['inputs'] + dependency['outputs']:
        path = nc.relative_path(root, item['path'])
        if not path.is_file() or nc.digest(path) != item['sha256']:
            errors.append('stale submitted outline candidate: ' + item['path'])
    if dependency.get('dependency_restart_required'):
        errors.append('outline candidate requires dependency restart')
    for prerequisite in dependency['depends_on']:
        if valid(prerequisite):
            errors.append('invalid outline candidate prerequisite: ' + prerequisite)
    return errors


def pair(item):
    return (item.get('path'), item.get('sha256')) if isinstance(item, dict) else (None, None)


def editorial_errors(root, stage, task):
    import taskctl as tc
    if stage['workflow'] != 'outline':
        return []
    errors, checks = [], task['checks']
    def need(condition, message):
        if not condition:
            errors.append(message)
    kind = task['kind']
    inputs = {pair(item) for item in task['inputs']}
    outputs = {pair(item) for item in task['outputs']}
    if kind == 'outline_draft' or kind.startswith('outline_revision_'):
        candidate = checks.get('outline_candidate', {})
        need(pair(candidate) in outputs, 'outline_candidate must identify an output version')
        need(isinstance(candidate.get('path'), str) and Path(candidate['path']).parts[:1] == ('大纲',),
             'outline_candidate must be an immutable outline file')
        if kind != 'outline_draft':
            number = int(kind.rsplit('_', 1)[1])
            prior_kind = 'outline_recheck_' + str(number - 1) if number > 1 else 'outline_review'
            prior_report = tc.selected_task(stage, prior_kind)
            prior_check = prior_report['checks'].get('outline_review', {})
            need(pair(prior_check) in inputs and pair(prior_check.get('candidate')) in inputs,
                 'outline revision inputs must bind the preceding report and candidate')
            prior_candidates = [item['checks'].get('outline_candidate', {}) for item in stage['tasks']
                                if item['kind'] == 'outline_draft' or item['kind'].startswith('outline_revision_')
                                and int(item['kind'].rsplit('_', 1)[1]) < number]
            need(all(candidate.get('path') != old.get('path') for old in prior_candidates),
                 'outline revisions must use a new immutable candidate path')
    elif kind == 'outline_review' or kind.startswith('outline_recheck_'):
        producer_kind = ('outline_draft' if kind == 'outline_review' else
                         'outline_revision_' + kind.rsplit('_', 1)[1])
        producer = tc.selected_task(stage, producer_kind)
        candidate = producer['checks'].get('outline_candidate', {})
        report = checks.get('outline_review', {})
        need(pair(candidate) in inputs and pair(report.get('candidate')) == pair(candidate),
             'outline review must bind the exact current candidate input')
        need(pair(report) in outputs, 'outline review check must identify its report output')
        other_versions = [item['checks'].get('outline_candidate', {}).get('path')
                          for item in stage['tasks']]
        other_versions += [item['checks'].get('outline_review', {}).get('path')
                           for item in stage['tasks'] if item['id'] != task['id']]
        need(report.get('path') not in other_versions,
             'outline reports need separate immutable paths from candidates and other reports')
        readiness = report.get('readiness', {})
        need(isinstance(readiness, dict) and all(type(readiness.get(name)) is bool for name in READINESS),
             'outline review requires four explicit readiness checks')
        issues = report.get('issues')
        need(isinstance(issues, list), 'outline review requires classified issues')
        blockers, ids = [], set()
        for issue in issues if isinstance(issues, list) else []:
            if not isinstance(issue, dict):
                errors.append('invalid outline issue')
                continue
            issue_id = issue.get('id')
            need(tc.nonempty(issue_id) and issue_id not in ids, 'outline issues need unique ids')
            if isinstance(issue_id, str):
                ids.add(issue_id)
            need(issue.get('category') in ('blocker', 'risk', 'blank'), 'invalid outline issue category')
            chapters = issue.get('chapters')
            need(isinstance(chapters, list) and bool(chapters)
                 and all(type(chapter) is int and chapter > 0 for chapter in chapters),
                 'outline issues need affected chapter numbers')
            need(all(tc.nonempty(issue.get(name)) for name in ('minimum_fix', 'verification')),
                 'outline issues need minimum_fix and verification')
            if issue.get('category') == 'risk':
                need(tc.nonempty(issue.get('tracking')), 'outline risk needs explicit tracking')
            if issue.get('category') == 'blocker':
                blockers.append(issue)
        verdict = report.get('verdict')
        need(verdict in ('pass', 'revise'), 'outline verdict must be pass or revise')
        if verdict == 'pass':
            need(not blockers and isinstance(readiness, dict)
                 and all(readiness.get(name) is True for name in READINESS),
                 'outline pass requires all hard standards and no blockers')
        elif verdict == 'revise':
            need(bool(blockers), 'outline revise requires a structural blocker; track nonblocking risks instead')
        if kind == 'outline_recheck_2' and verdict == 'revise':
            options = report.get('options')
            need(isinstance(options, list) and 2 <= len(options) <= 3
                 and all(isinstance(option, dict) and all(tc.nonempty(option.get(name))
                         for name in ('id', 'description', 'cost')) for option in options)
                 and len({option['id'] for option in options}) == len(options),
                 'final blocked recheck needs two or three concrete author options and costs')
    elif kind == 'outline_acceptance':
        producer, report_task = latest(stage)
        candidate = producer['checks'].get('outline_candidate', {})
        report = report_task['checks'].get('outline_review', {})
        need(report.get('verdict') == 'pass', 'outline acceptance requires the latest passing recheck')
        need(pair(candidate) in inputs and pair(report) in inputs,
             'outline acceptance inputs must bind the current candidate and latest report')
        need(pair(checks.get('current_version')) == pair(candidate),
             'outline acceptance current_version must identify the current candidate')
        confirmation = checks.get('author_confirmation', {})
        need(pair(confirmation) in inputs and pair(confirmation.get('candidate')) == pair(candidate)
             and all(tc.nonempty(confirmation.get(name)) for name in ('source_quote', 'scope')),
             'outline acceptance requires explicit author confirmation of this candidate')
    return errors


def next_action(stage):
    import taskctl as tc
    _, report_task = latest(stage)
    report = report_task['checks'].get('outline_review', {})
    if report_task['status'] == 'done' and report.get('verdict') == 'revise':
        return dict(next_action='author_decision' if rounds(stage) == MAX_ROUNDS else 'outline_revise',
                    revision_round=rounds(stage), options=copy.deepcopy(report.get('options', [])))
    return dict(next_action=cursor(stage), revision_round=rounds(stage))


def execution_errors(stage, task):
    if stage['workflow'] == 'outline' and task['kind'] == 'outline_acceptance':
        _, report = latest(stage)
        if report['checks'].get('outline_review', {}).get('verdict') != 'pass':
            return ['outline acceptance requires the latest passing recheck; '
                    + next_action(stage)['next_action']]
    return []


def mutation_errors(stage, task, request):
    """Submission freezes a version; administrative acceptance does not rewrite it."""
    if stage['workflow'] != 'outline' or task['kind'] in ('outline_acceptance', 'next_plan'):
        return []
    if not task['kind'].startswith('outline_'):
        return []
    versions = [item for item in task['history'] + [task]
                if item.get('status') in ('submitted', 'done')]
    if not versions:
        return []
    frozen = versions[0]
    if (request.get('status') not in ('submitted', 'done', 'blocked')
            or any(request.get(name, task[name]) != frozen.get(name)
                   for name in ('inputs', 'outputs', 'checks'))):
        return ['submitted outline versions are immutable; schedule the next bounded revision '
                'rather than replacing or restarting this round']
    return []


def tasks_digest(tasks):
    return hashlib.sha256(json.dumps(tasks, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def pristine_plan(stage):
    return (stage.get('status') == 'planned' and all(task['status'] == 'todo'
            and not any(task.get(name) for name in
                        ('inputs', 'outputs', 'checks', 'history', 'verification', 'reason',
                         'operation_id', 'dependency_restart_required'))
            for task in stage['tasks']))


def validate_rescope_basis(stage, choice):
    """Keep automatic repair bounded; a new author request is a separate scope."""
    import taskctl as tc
    _, report_task = latest(stage)
    report = report_task['checks'].get('outline_review', {})
    tc.require(report_task['status'] == 'done'
               and tc.selected_task(stage, 'outline_acceptance')['status'] != 'done',
               'outline rescope requires an accepted report and an unaccepted candidate')
    mode = choice.get('mode', 'option')
    if mode == 'option':
        tc.require(rounds(stage) == MAX_ROUNDS and report.get('verdict') == 'revise',
                   'option rescope requires the final blocked outline after two revisions')
        tc.require(choice.get('option_id') in [option['id'] for option in report.get('options', [])],
                   'outline rescope must bind an actual current author option and scope')
    else:
        tc.require(mode == 'author_revision' and report.get('verdict') == 'pass'
                   and 'option_id' not in choice,
                   'author_revision rescope requires the latest passing report and new author feedback')


def validate_supersession(root, stage, stages):
    """Seal an unaccepted scope without pretending its candidate was accepted."""
    import taskctl as tc
    record = stage.get('outline_supersession')
    tc.require(stage['workflow'] == 'outline' and isinstance(record, dict),
               'only an explicitly superseded outline can use superseded status')
    tc.require(record.get('tasks_sha256') == tasks_digest(stage['tasks']),
               'superseded outline task history has changed')
    producer, report_task = latest(stage)
    report = report_task['checks'].get('outline_review', {})
    successor = stages.get(record.get('successor'))
    tc.require(successor is not None and successor['workflow'] == 'outline'
               and tc.workflow_version(successor) == 2 and stage['next_stage'] == successor['id'],
               'superseded outline requires a new outline successor, never chapter work')
    choice = record.get('choice', {})
    tc.evidence_structure(root, [choice])
    validate_rescope_basis(stage, choice)
    tc.require(all(other is stage or other.get('outline_supersession', {}).get('choice', {}).get('path')
                   != choice['path'] for other in stages.values()),
               'each outline rescope requires a new immutable author choice record')
    if choice.get('mode') == 'author_revision':
        tc.require(all(other is stage or other.get('outline_supersession', {}).get('choice', {}).get('sha256')
                       != choice['sha256'] for other in stages.values()),
                   'author revision requires fresh feedback, not a copied prior rescope choice')
    tc.require(pair(choice.get('candidate')) == pair(producer['checks'].get('outline_candidate'))
               and pair(choice.get('report')) == pair(report)
               and all(tc.nonempty(choice.get(name)) for name in ('source_quote', 'scope')),
               'outline rescope must bind the current candidate, report and explicit author scope')
    verification = record.get('verification')
    tc.require(tc.nonempty(record.get('reason')) and isinstance(verification, dict)
               and all(tc.nonempty(verification.get(name)) for name in ('agent_id', 'source')),
               'outline rescope requires reason and host verification')
    old_plan = record.get('superseded_future_plan')
    if old_plan is not None:
        tc.require(isinstance(old_plan, dict) and pristine_plan(old_plan)
                   and old_plan.get('id') not in stages,
                   'rescope can archive only a completely unstarted future plan')
    artifacts = record.get('artifacts')
    tc.evidence_structure(root, artifacts)
    expected = {pair(item) for task in stage['tasks'] for item in task['inputs'] + task['outputs']}
    tc.require({pair(item) for item in artifacts} == expected,
               'superseded outline must preserve all registered version evidence')
    for evidence in artifacts + [choice]:
        path = nc.relative_path(root, evidence['path'])
        tc.require(path.is_file() and nc.digest(path) == evidence['sha256'],
                   'stale superseded outline evidence: ' + evidence['path'])
    try:
        text = nc.relative_path(root, choice['path']).read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        raise ValueError('author rescope source must be readable UTF-8') from exc
    tc.require(choice['source_quote'] in text, 'author rescope quote is absent from its source')


def rescope(book, request):
    """Continue after an explicit author choice or request, preserving old work."""
    import taskctl as tc
    tc.require(isinstance(request, dict), 'outline rescope request must be an object')
    with tc.writing(book) as root:
        data, (stages, task_map) = tc.load(root)
        stage = stages.get(request.get('stage'))
        tc.require(stage is not None and stage['workflow'] == 'outline'
                   and stage['id'] == data['current_stage'] and stage['status'] not in tc.CLOSED_STAGES,
                   'outline rescope requires the current unclosed outline')
        _, report_task = latest(stage)
        tc.require(not tc.task_validity(root, data, task_map)(report_task['id']),
                   'outline rescope requires the accepted current report')
        tc.require(not nc.load_state(root)['pending_operations'], 'pending_operations prevent outline rescope')
        tc.require(not tc.artifact_blockers(root, data, task_map),
                   'unregistered artifacts must be reconciled before outline rescope')
        future = [item for item in data['stages'] if item is not stage and item['status'] not in tc.CLOSED_STAGES]
        tc.require(len(future) <= 1 and all(pristine_plan(item) for item in future),
                   'outline rescope cannot discard any started future work')
        if future:
            tc.require(all(item is stage or item is future[0] or item['next_stage'] != future[0]['id']
                           for item in data['stages']),
                       'another historical stage references the future plan; preserve its linkage')
        successor = request.get('successor')
        tc.require(isinstance(successor, dict) and tc.nonempty(successor.get('id'))
                   and tc.nonempty(successor.get('title')) and successor['id'] not in stages
                   and successor.get('workflow', 'outline') == 'outline',
                   'outline rescope successor must be a new outline stage')
        choice = request.get('choice')
        tc.require(isinstance(choice, dict), 'outline rescope requires an explicit author choice')
        tc.evidence_structure(root, [choice])
        validate_rescope_basis(stage, choice)
        tc.require(Path(choice['path']).parts[:1] in (('创作约定',), ('审稿与修订',))
                   and choice['path'] not in {item['path'] for task in stage['tasks'] for item in task['outputs']},
                   'outline rescope needs a separate author decision record')
        new_stage = tc.new_stage(successor['id'], successor['title'], 'outline')
        new_stage['status'] = 'active'
        evidence = {pair(item): dict(path=item['path'], sha256=item['sha256'])
                    for task in stage['tasks'] for item in task['inputs'] + task['outputs']}
        stage['outline_supersession'] = dict(successor=successor['id'], reason=request.get('reason'),
            verification=copy.deepcopy(request.get('verification')), choice=copy.deepcopy(choice),
            tasks_sha256=tasks_digest(stage['tasks']), artifacts=list(evidence.values()))
        if future:
            stage['outline_supersession']['superseded_future_plan'] = copy.deepcopy(future[0])
            data['stages'].remove(future[0])
        stage['status'] = 'superseded'
        stage['next_stage'] = successor['id']
        data['stages'].append(new_stage)
        data['current_stage'], data['current_task'] = new_stage['id'], cursor(new_stage)
        tc.save(root, data)
        return dict(ok=True, superseded=stage['id'], current_stage=new_stage['id'],
                    current_task=data['current_task'], authorization_scope=choice['scope'])


def revise(book, request):
    import taskctl as tc
    tc.require(isinstance(request, dict), 'outline revision request must be an object')
    with tc.writing(book) as root:
        data, (stages, task_map) = tc.load(root)
        stage = stages.get(request.get('stage'))
        tc.require(stage is not None and stage['workflow'] == 'outline'
                   and stage['id'] == data['current_stage'] and stage['status'] != 'done',
                   'outline revision requires the current unclosed outline stage')
        verification = request.get('verification')
        tc.require(tc.nonempty(request.get('reason')) and isinstance(verification, dict)
                   and all(tc.nonempty(verification.get(name)) for name in ('agent_id', 'source')),
                   'outline revision scheduling needs reason and host verification')
        _, report_task = latest(stage)
        tc.require(not tc.task_validity(root, data, task_map)(report_task['id']),
                   'outline revision requires the accepted current report')
        report = report_task['checks']['outline_review']
        tc.require(report['verdict'] == 'revise', 'passing outlines proceed to author confirmation, not another revision')
        if rounds(stage) == MAX_ROUNDS:
            return dict(ok=False, stage=stage['id'], error='outline revision limit reached', **next_action(stage))
        tc.require(not nc.load_state(root)['pending_operations'], 'pending_operations prevent outline revision')
        blockers = tc.artifact_blockers(root, data, task_map)
        tc.require(not blockers, '; '.join(blockers))
        number = rounds(stage) + 1
        added = []
        for kind in ('outline_revision_' + str(number), 'outline_recheck_' + str(number)):
            title, role, acceptance = tc.TASK_DEFAULTS[kind]
            added.append(dict(id=stage['id'] + '.' + kind, kind=kind, title=title, role=role,
                              acceptance=acceptance, status='todo', depends_on=[added[-1]['id'] if added else report_task['id']],
                              inputs=[], outputs=[], checks={}, verification=None, reason='', history=[], operation_id=''))
        acceptance = tc.selected_task(stage, 'outline_acceptance')
        tc.require(acceptance['status'] not in tc.FINISHED, 'accepted outline needs a separately authorized new stage')
        for task in (acceptance, tc.selected_task(stage, 'next_plan')):
            tc.archive(task, 'outline revision round scheduled')
            task.update(status='todo', inputs=[], outputs=[], checks={}, verification=None, operation_id='', reason='')
            task.pop('dependency_restart_required', None)
        acceptance['depends_on'] = [added[-1]['id']]
        offset = stage['tasks'].index(acceptance)
        stage['tasks'][offset:offset] = added
        stage.setdefault('outline_round_history', []).append(dict(round=number, reason=request['reason'],
            verification=copy.deepcopy(verification), report=dict(path=report['path'], sha256=report['sha256'])))
        data['current_task'] = cursor(stage)
        tc.save(root, data)
        return dict(ok=True, stage=stage['id'], added=[task['id'] for task in added], **next_action(stage))
