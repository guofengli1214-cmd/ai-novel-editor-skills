"""Finalization/revision scheduling with technical fixtures, never novel generation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_editorial_gates as fixtures


tc, nc, cc = fixtures.tc, fixtures.nc, fixtures.cc
KINDS = ('storyline_management', 'hook_management', 'curve_direction')


class FinalizationDeferTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EditorialGateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.source = self.f.ev('审稿与修订/技术依据.md')
        self.f.chapter_ready()
        operation = cc.adopt(self.root, self.f.req)
        self.f.synchronize(operation)
        tc.plan(self.root, dict(id='F', title='定稿机械夹具', workflow='finalization'))
        tc.plan(self.root, dict(id='S02', next_stage='F'))
        self.f.done('S02', 'next_plan')
        tc.close_stage(self.root, 'S02')
        self.formal = self.f.ev(self.f.req['target'])
        self.read = self.run_task('F', 'whole_read', inputs=[self.formal])

    def request(self):
        return dict(stage='F', revision=dict(id='R', title='逐章修订机械夹具', chapter=1),
                    reason='仅检验定稿让位逐章修订和重新阅读的机械流程。',
                    verification=dict(agent_id='fixture-main', source='显式技术核验，不证明真实作者授权或实际阅读'))

    def stage(self, stage_id='F'):
        return next(s for s in tc.load_plan(self.root)['stages'] if s['id'] == stage_id)

    def preflight(self, stage, task, inputs=()):
        current = tc.selected_task(self.stage(stage), task)
        combined = list(inputs)
        if self.source not in combined:
            combined.append(self.source)
        checks = {}
        if current['kind'] in tc.CREATIVE_DECISION_TASKS:
            checks['creative_decisions'] = dict(self.source, summary='仅核对既有技术决定', items=[],
                                                none_reason='技术夹具不新增任何创作选择。')
        if current['kind'] == 'prepare':
            combined.append(self.f.ev(self.f.policy_path))
        tc.update(self.root, dict(stage=stage, task=current['id'], status='todo', inputs=combined, checks=checks))
        tc.gate(self.root, stage, current['id'])
        tc.update(self.root, dict(stage=stage, task=current['id'], status='doing'))
        return current['kind'], combined, checks

    def accept_started(self, stage, task, outputs, inputs, checks, operation_id=None):
        request = dict(stage=stage, task=task, outputs=outputs, inputs=inputs, checks=checks)
        if operation_id is not None:
            request['operation_id'] = operation_id
        tc.submit(self.root, request)
        tc.update(self.root, dict(stage=stage, task=task, status='done',
                                  verification=dict(agent_id='fixture-main', source='显式机械夹具证据核验')))
        nc.index_book(self.root)

    def run_task(self, stage, task, inputs=(), path=None):
        kind, inputs, preflight = self.preflight(stage, task, inputs)
        path = path or '审稿与修订/' + stage + '-' + task + '-技术报告.md'
        self.f.write(path, '仅用于机械流程测试的报告或文本，不是小说、真实角色阅读或文学结论。')
        report = self.f.ev(path)
        checks = self.f.checks(kind, [report])
        checks.update(preflight)
        if kind == 'prepare':
            checks['prose_policy'] = dict(self.f.ev(self.f.policy_path), summary='已有技术标点约定')
        outputs = [report]
        if kind == 'finalize':
            # Finalize is a real version gate, not an arbitrary report checkbox.
            nc.index_book(self.root)
            record_path = '审稿与修订/' + stage + '-全书定稿记录.json'
            self.f.save(record_path, dict(
                schema_version=1, chapters=fixtures.bc.manifest(self.root)['chapters'],
                reviewer=dict(task_id='fixture-whole-finalize', agent_id='fixture-whole-reader'),
                reading_scope='full', verdict='ready', task_stage=stage,
                reading_report=report, revision_report=report))
            outputs.append(self.f.ev(record_path))
            nc.index_book(self.root)
        self.accept_started(stage, task, outputs, inputs, checks)
        return report

    def specialists(self, before='structural_revision', predecessor='F.whole_read'):
        data = tc.load_plan(self.root)
        stage = next(s for s in data['stages'] if s['id'] == 'F')
        core = tc.selected_task(stage, before)
        ids = ['F.' + kind for kind in KINDS]
        tasks = []
        for kind, task_id in zip(KINDS, ids):
            tasks.append(dict(id=task_id, kind=kind, title='专项机械夹具', role='specialist',
                              status='todo', depends_on=[predecessor], inputs=[], outputs=[], checks={},
                              acceptance='只检验已登记报告的调度', verification=None, reason='', history=[], operation_id=''))
        tasks[-1]['depends_on'] += ids[:2]
        stage['tasks'][stage['tasks'].index(core):stage['tasks'].index(core)] = tasks
        core['depends_on'] += ids
        data['current_task'] = next(t['id'] for t in stage['tasks'] if t['status'] not in tc.FINISHED)
        tc.save(self.root, data)
        return [self.run_task('F', task_id, inputs=[self.formal]) for task_id in ids]

    def assert_rejected_unchanged(self, request=None, pattern=None):
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, pattern or '.'):
            tc.defer(self.root, self.request() if request is None else request)
        self.assertEqual(nc.inventory(self.root), before)

    def test_cli_defer_archives_all_finalization_evidence_and_unblocks_revision(self):
        self.specialists()
        self.run_task('F', 'structural_revision', inputs=[self.formal])
        self.run_task('F', 'whole_recheck', inputs=[self.formal])
        original = copy.deepcopy(self.stage())
        ledger = self.root / tc.TASK_FILE
        ledger.write_text(ledger.read_text(encoding='utf-8') + '\n技术夹具的非受管交接说明保留。\n', encoding='utf-8')
        request = self.request()
        request_file = Path(self.f.temp.name) / 'defer-request.json'
        request_file.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        result = subprocess.run([sys.executable, str(Path(tc.__file__)), 'defer', '--book', str(self.root),
                                 '--request', str(request_file)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(answer['current_stage'], 'R')
        self.assertFalse(answer['reused'])
        data = tc.load_plan(self.root)
        self.assertEqual([s['id'] for s in data['stages']], ['S01', 'S02', 'R', 'F'])
        self.assertEqual(data['current_task'], 'R.prepare')
        deferred, revision = self.stage(), self.stage('R')
        self.assertEqual(deferred['status'], 'planned')
        self.assertIsNone(deferred['next_stage'])
        self.assertEqual(revision['workflow_version'], 2)
        self.assertEqual(revision['decision_gate_version'], 1)
        self.assertEqual(revision['execution_gate_version'], 1)
        self.assertEqual(revision['next_stage'], 'F')
        for old, current in zip(original['tasks'], deferred['tasks']):
            self.assertEqual(current['status'], 'todo')
            for key, empty in (('inputs', []), ('outputs', []), ('checks', {}), ('verification', None),
                               ('reason', ''), ('operation_id', '')):
                self.assertEqual(current[key], empty)
            archived = current['history'][-1]
            for key in old:
                if key != 'history':
                    self.assertEqual(archived[key], old[key])
            self.assertEqual(archived['deferral']['reason'], request['reason'])
            self.assertEqual(archived['deferral']['verification'], request['verification'])
            self.assertEqual(archived['deferral']['revision']['id'], 'R')
        self.assertIn('非受管交接说明保留', ledger.read_text(encoding='utf-8'))
        self.preflight('R', 'prepare')
        self.assertTrue(tc.gate(self.root, 'R', 'prepare')['ok'])
        with self.assertRaisesRegex(ValueError, 'future stage'):
            tc.gate(self.root, 'F', 'whole_read')

    def test_matching_unstarted_v2_future_revision_is_reused_without_losing_its_plan(self):
        tc.plan(self.root, dict(id='R', title='已规划修订技术夹具', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='F', next_stage='R'))
        planned = copy.deepcopy(self.stage('R'))
        result = tc.defer(self.root, self.request())
        self.assertTrue(result['reused'])
        current = self.stage('R')
        self.assertEqual(current['title'], planned['title'])
        self.assertEqual(current['tasks'], planned['tasks'])
        self.assertEqual(current['next_stage'], 'F')
        self.assertEqual(sum(s['id'] == 'R' for s in tc.load_plan(self.root)['stages']), 1)

    def test_defer_rejects_pending_unaccepted_read_busy_and_unregistered_work_without_writes(self):
        state_path, ledger = self.root / nc.STATE, self.root / tc.TASK_FILE
        state_bytes, ledger_bytes = state_path.read_bytes(), ledger.read_bytes()
        state = nc.load_state(self.root)
        state['pending_operations'] = [dict(operation_id='technical-pending')]
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        self.assert_rejected_unchanged(pattern='pending_operations')
        state_path.write_bytes(state_bytes)
        for status in ('todo', 'doing', 'submitted'):
            data = tc.load_plan(self.root)
            stage = data['stages'][-1]
            task = tc.selected_task(stage, 'whole_read' if status == 'todo' else 'structural_revision')
            task['status'] = status
            data['current_task'] = next(t['id'] for t in stage['tasks'] if t['status'] not in tc.FINISHED)
            tc.save(self.root, data)
            self.assert_rejected_unchanged(pattern='accepted whole_read' if status == 'todo' else 'settle')
            ledger.write_bytes(ledger_bytes)
        path = self.root / '审稿与修订/未提交技术报告.md'
        path.write_text('未提交的机械夹具产物，不能借defer洗掉。', encoding='utf-8')
        self.assert_rejected_unchanged(pattern='unregistered artifact')

    def test_defer_rejects_stale_accepted_read_and_finalized_or_nonfinalization_stage(self):
        read = self.root / self.read['path']
        original = read.read_bytes()
        read.write_bytes(original + b'\nchanged fixture\n')
        self.assert_rejected_unchanged(pattern='valid accepted evidence')
        read.write_bytes(original)
        self.run_task('F', 'structural_revision', inputs=[self.formal])
        self.run_task('F', 'whole_recheck', inputs=[self.formal])
        self.run_task('F', 'finalize', inputs=[self.formal])
        self.assert_rejected_unchanged(pattern='accepted finalize')
        request = self.request()
        request['stage'] = 'S02'
        self.assert_rejected_unchanged(request, 'current unclosed finalization')
        other = nc.init_book(Path(self.f.temp.name), '非定稿阶段技术夹具')
        before = nc.inventory(other)
        request['stage'] = 'S01'
        with self.assertRaisesRegex(ValueError, 'current unclosed finalization'):
            tc.defer(other, request)
        self.assertEqual(nc.inventory(other), before)

    def test_invalid_requests_or_different_future_plan_are_rejected_unchanged(self):
        for change in (dict(reason=''), dict(verification={}), dict(revision={}),
                       dict(revision=dict(id='R', title='技术修订', chapter=True)),
                       dict(revision=dict(id='F', title='技术修订', chapter=1)),
                       dict(revision=dict(id='S02', title='技术修订', chapter=1))):
            request = self.request()
            request.update(change)
            self.assert_rejected_unchanged(request)
        tc.plan(self.root, dict(id='OTHER', title='必须保留的技术后续', workflow='chapter', chapter=2))
        tc.plan(self.root, dict(id='F', next_stage='OTHER'))
        self.assert_rejected_unchanged(pattern='another planned stage')

    def test_reuse_rejects_legacy_gate_downgrades_and_any_prior_execution(self):
        tc.plan(self.root, dict(id='R', title='待修订技术阶段', workflow='chapter', chapter=1))
        original = copy.deepcopy(tc.load_plan(self.root))
        for mutation in ('v1', 'decision_gate', 'execution_gate', 'history', 'verification', 'operation', 'chapter'):
            data = copy.deepcopy(original)
            stage = next(s for s in data['stages'] if s['id'] == 'R')
            if mutation == 'v1':
                stage['workflow_version'] = 1
                stage.pop('decision_gate_version')
                stage.pop('execution_gate_version')
                stage.pop('result_gate_version', None)
            elif mutation in ('decision_gate', 'execution_gate'):
                stage.pop(mutation + '_version')
            elif mutation == 'history':
                stage['tasks'][0]['history'] = [dict(action='synthetic prior execution', status='doing')]
            elif mutation == 'verification':
                stage['tasks'][0]['verification'] = dict(agent_id='fixture-main', source='旧技术核验')
            elif mutation == 'operation':
                stage['tasks'][0]['operation_id'] = 'technical-old-operation'
            else:
                stage['chapter'] = 2
            tc.save(self.root, data)
            self.assert_rejected_unchanged(pattern='matching planned v2|never have executed')
            tc.save(self.root, original)

    def test_reopening_revision_keeps_verified_accepted_reports_as_historical_provenance(self):
        self.run_task('F', 'structural_revision', inputs=[self.formal])
        reports = self.specialists(before='whole_recheck', predecessor='F.structural_revision')
        self.run_task('F', 'whole_recheck', inputs=[self.formal])
        tc.update(self.root, dict(stage='F', task='structural_revision', status='todo', reason='机械夹具真实返工'))
        self.assertTrue(tc.gate(self.root, 'F', 'structural_revision')['ok'])
        for task in self.stage()['tasks']:
            if task['kind'] in KINDS or task['kind'] == 'whole_recheck':
                self.assertEqual(task['status'], 'todo')
                self.assertEqual(task['history'][-1]['status'], 'done')
                self.assertEqual(task['outputs'], [])
                self.assertTrue(task['history'][-1]['outputs'])
                self.assertTrue(task['dependency_restart_required'])
        path = self.root / reports[0]['path']
        original = path.read_bytes()
        path.write_bytes(original + b'\nchanged fixture\n')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'F', 'structural_revision')
        path.write_bytes(original)
        pending = self.root / '审稿与修订/从未验收的技术产物.md'
        pending.write_text('没有实际验收，只用于证明submitted/doing历史不能放行。', encoding='utf-8')
        evidence = self.f.ev(pending.relative_to(self.root).as_posix())
        baseline = tc.load_plan(self.root)
        for status in ('doing', 'submitted'):
            data = copy.deepcopy(baseline)
            stage = data['stages'][-1]
            target = next(t for t in stage['tasks'] if t['kind'] == 'whole_recheck')
            target['outputs'] = [evidence]
            target['history'].append(dict(status=status, outputs=[evidence],
                                          verification=dict(agent_id='fixture-main', source='未验收技术记录')))
            tc.save(self.root, data)
            with self.assertRaisesRegex(ValueError, 'out-of-order output'):
                tc.gate(self.root, 'F', 'structural_revision')
            tc.save(self.root, baseline)

    def test_accepted_recovered_held_report_can_be_archived_but_unaccepted_held_output_cannot(self):
        def recover(path):
            tc.recover(self.root, dict(artifacts=[dict(self.f.ev(path), classification='held_output',
                                                       task_id='F.whole_read', source=self.f.ev(self.f.recovery_source),
                                                       source_quote=self.f.recovery_quote, scope='显式机械恢复夹具')],
                                       verification=dict(agent_id='fixture-main', source='技术来源核对')))
        recover(self.read['path'])
        self.assertTrue(tc.defer(self.root, self.request())['ok'])
        self.preflight('R', 'prepare')
        self.assertTrue(tc.gate(self.root, 'R', 'prepare')['ok'])
        path = '审稿与修订/未验收的恢复产物.md'
        self.f.write(path, '仅有来源恢复，未被任何任务提交或验收。')
        recover(path)
        with self.assertRaisesRegex(ValueError, 'held output needs ordered recovery'):
            tc.gate(self.root, 'R', 'prepare')

    def test_explained_verified_optional_report_is_archived_without_turning_exemption_into_completion(self):
        data = tc.load_plan(self.root)
        stage = data['stages'][-1]
        task = dict(id='F.optional_reason', kind='conditional_research', title='条件任务技术理由', role='materials',
                    status='todo', depends_on=['F.whole_read'], inputs=[], outputs=[], checks={},
                    acceptance='核对本轮条件适用性，不代表角色阅读', verification=None, reason='', history=[], operation_id='')
        stage['tasks'].insert(1, task)
        tc.selected_task(stage, 'structural_revision')['depends_on'].append(task['id'])
        data['current_task'] = task['id']
        tc.save(self.root, data)
        self.preflight('F', task['id'])
        path = '审稿与修订/F-条件任务理由.md'
        self.f.write(path, '纯技术夹具：本轮条件不适用的独立理由报告。')
        tc.submit(self.root, dict(stage='F', task=task['id'], outputs=[self.f.ev(path)]))
        tc.update(self.root, dict(stage='F', task=task['id'], status='not_applicable',
                                  reason='仅说明技术条件在本轮不适用，不冒称任务已执行。',
                                  verification=dict(agent_id='fixture-main', source='显式核对行政理由夹具')))
        nc.index_book(self.root)
        tc.defer(self.root, self.request())
        archived_task = tc.selected_task(self.stage(), task['id'])
        self.assertEqual(archived_task['status'], 'todo')
        self.assertEqual(archived_task['outputs'], [])
        self.assertEqual(archived_task['history'][-1]['status'], 'not_applicable')
        self.preflight('R', 'prepare')
        self.assertTrue(tc.gate(self.root, 'R', 'prepare')['ok'])

    def test_unexplained_or_unverified_exemption_history_does_not_legalize_a_report(self):
        path = '审稿与修订/F-无有效理由或验收的历史报告.md'
        self.f.write(path, '纯技术夹具：不完整的历史申报不能证明已验收来源。')
        evidence = self.f.ev(path)
        baseline = tc.load_plan(self.root)
        cases = [dict(reason='', verification=dict(agent_id='fixture-main', source='技术核对')),
                 dict(reason='技术理由', verification=None), dict(reason='技术理由', verification={})]
        for case in cases:
            data = copy.deepcopy(baseline)
            stage = data['stages'][-1]
            task = dict(id='F.invalid_reason_history', kind='conditional_research', title='无效历史技术夹具', role='materials',
                        status='todo', depends_on=[], inputs=[], outputs=[], checks={}, acceptance='仅检验历史来源限制',
                        verification=None, reason='', operation_id='',
                        history=[dict(status='not_applicable', outputs=[evidence], **case)])
            stage['tasks'].insert(-1, task)
            tc.save(self.root, data)
            with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
                tc.gate(self.root, 'F', 'structural_revision')
            tc.save(self.root, baseline)

    def test_real_revision_adopt_finish_and_close_returns_to_fresh_whole_read(self):
        self.specialists()
        tc.defer(self.root, self.request())
        self.run_task('R', 'prepare', inputs=[self.f.draft])  # Immutable prior draft, not the replaceable formal path.
        draft = self.run_task('R', 'draft', path='草稿与备选/第01章-机械修订-v2.md')
        self.run_task('R', 'first_read', inputs=[draft])
        self.run_task('R', 'review', inputs=[draft])
        self.run_task('R', 'revise', inputs=[draft])
        kind, inputs, preflight = self.preflight('R', 'recheck', [draft])
        report_path = '审稿与修订/R-当前稿复核.md'
        review_path = '审稿与修订/R-当前稿评审.json'
        prose_path = '审稿与修订/R-机械规范检查.json'
        self.f.write(report_path, '显式技术夹具报告，不证明真实阅读或文学水平。')
        review = dict(self.f.review, writer_task='R.draft', draft_sha256=draft['sha256'],
                      reviewer=dict(task_id='R.recheck', agent_id='fixture-revision-reader'),
                      current_draft=draft, reviewed_draft=draft, report=self.f.ev(report_path))
        self.f.save(review_path, review)
        policy = self.f.ev(self.f.policy_path)
        self.f.save(prose_path, dict(draft_sha256=draft['sha256'], policy=policy,
                                    result=fixtures.prosecheck.check_text((self.root / draft['path']).read_text(encoding='utf-8'),
                                                                         json.loads((self.root / policy['path']).read_text(encoding='utf-8')))))
        outputs = [self.f.ev(path) for path in (report_path, review_path, prose_path)]
        self.accept_started('R', 'recheck', outputs, inputs, self.f.checks(kind, outputs))
        request = dict(self.f.req, draft=draft['path'], expected_sha256=self.formal['sha256'],
                       writer=dict(task_id='R.draft', agent_id='fixture-revision-writer'),
                       reviews=[review_path], prose_check=self.f.ev(prose_path), task_stage='R',
                       authorization=dict(mode='trial', source='显式机械夹具版本许可', approved_sha256=draft['sha256']),
                       change_record='连贯性记录/R-技术变化.json')
        self.preflight('R', 'adopt')
        operation = cc.adopt(self.root, request)
        self.accept_started('R', 'adopt', [self.f.ev(request['target'])], [], {}, operation['operation_id'])
        self.preflight('R', 'sync')
        files = dict(summaries=['摘要/R-技术摘要.md'], history=['历史版本/R-技术采用.md'],
                     indexes=['索引.md'], handoff=['创作约定/R-技术交接.md'])
        for category in ('summaries', 'history', 'handoff'):
            self.f.write(files[category][0], '机械夹具：第1章已采用，正在完成资料同步。')
        nc.index_book(self.root)
        self.f.save(request['change_record'], dict(
            draft_sha256=draft['sha256'], categories={name: 'updated' if name in files else 'unchanged' for name in cc.CATEGORIES},
            checked_sources=[request['target']], updated_files=[self.f.ev(names[0]) for names in files.values()],
            category_files=files, handoff=dict(path=files['handoff'][0], chapter=1)))
        nc.index_book(self.root)
        cc.finish(self.root, operation)
        sync_evidence = self.f.ev(request['change_record'])
        self.accept_started('R', 'sync', [sync_evidence], [], self.f.checks('sync', [sync_evidence]))
        self.run_task('R', 'next_plan')
        self.assertEqual(tc.close_stage(self.root, 'R')['current_stage'], 'F')
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'F.whole_read')
        self.assertFalse(nc.load_state(self.root)['pending_operations'])
        self.assertEqual(nc.digest(self.root / request['target']), draft['sha256'])
        self.assertTrue(tc.gate(self.root, 'F', 'whole_read')['ok'])
        with self.assertRaisesRegex(ValueError, 'not accepted'):
            tc.gate(self.root, 'F', 'structural_revision')
        with self.assertRaisesRegex(ValueError, 'not accepted'):
            tc.require_finalization(self.root, dict(task_stage='F'))
        self.assertEqual(tc.selected_task(self.stage(), 'whole_read')['status'], 'todo')
        self.assertTrue(tc.selected_task(self.stage(), 'whole_read')['history'])


if __name__ == '__main__':
    unittest.main()
