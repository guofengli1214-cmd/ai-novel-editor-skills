"""Bounded outline iterations with synthetic artifacts, never novel generation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc


class OutlineFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = nc.init_book(Path(self.temp.name), '章目工作流技术夹具', task_management=False)
        tc.initialize(self.root, workflow_version=1)
        self.source_path = '创作约定/显式技术输入.dat'
        self.write(self.source_path, '仅验证技术状态、哈希、依赖与授权记录，不生成小说。')
        self.source = self.ev(self.source_path)
        self.verification = dict(agent_id='fixture-main', source='显式技术夹具主agent验收')
        for kind in ('direction', 'concept'):
            tc.update(self.root, dict(stage='S01', task=kind, status='done',
                outputs=[self.source], verification=self.verification))
        tc.plan(self.root, dict(id='S02', title='全书章目有限轮次', workflow='outline'))
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        tc.update(self.root, dict(stage='S01', task='next_plan', status='done',
            verification=self.verification))
        tc.close_stage(self.root, 'S01')

    def write(self, path, text):
        (self.root / path).write_text(text, encoding='utf-8')

    def ev(self, path):
        return dict(path=path, sha256=nc.digest(self.root / path))

    def stage(self):
        return next(stage for stage in tc.load_plan(self.root)['stages'] if stage['id'] == 'S02')

    def task(self, kind):
        return tc.selected_task(self.stage(), kind)

    def preflight(self):
        return dict(self.source, summary='仅执行已给技术选择', items=[],
                    none_reason='没有新增创作选择，只验证合成流程。')

    def checks(self, kind, evidence):
        result = {name: dict(evidence, summary='合成技术检查：' + name)
                  for name in tc.EDITORIAL_CHECKS.get(kind, ())}
        if (kind in tc.CREATIVE_DECISION_TASKS or kind == 'outline_draft'
                or kind.startswith('outline_revision_') or kind == 'next_plan'):
            result['creative_decisions'] = self.preflight()
        return result

    def start(self, kind, inputs=()):
        evidence = [self.source] + [item for item in inputs if item != self.source]
        checks = {}
        if (kind in tc.CREATIVE_DECISION_TASKS or kind == 'outline_draft'
                or kind.startswith('outline_revision_') or kind == 'next_plan'):
            checks['creative_decisions'] = self.preflight()
        tc.update(self.root, dict(stage='S02', task=kind, status='todo',
            inputs=evidence, checks=checks))
        self.assertTrue(tc.gate(self.root, 'S02', kind)['ok'])
        tc.update(self.root, dict(stage='S02', task=kind, status='doing'))
        return evidence

    def candidate(self, round_number=0):
        kind = 'outline_draft' if round_number == 0 else 'outline_revision_' + str(round_number)
        inputs = []
        if round_number:
            prior = 'outline_review' if round_number == 1 else 'outline_recheck_' + str(round_number - 1)
            previous = self.task(prior)
            inputs = [previous['checks']['outline_review']['candidate'], *previous['outputs']]
        registered_inputs = self.start(kind, inputs)
        path = '大纲/技术候选-v' + str(round_number + 1) + '.md'
        self.write(path, '合成章目版本标记 ' + str(round_number + 1) + '；不包含小说内容。')
        evidence = self.ev(path)
        checks = self.checks(kind, evidence)
        checks['outline_candidate'] = dict(evidence, summary='当前合成章目候选')
        tc.submit(self.root, dict(stage='S02', task=kind, inputs=registered_inputs,
            outputs=[evidence], checks=checks))
        if round_number:
            tc.update(self.root, dict(stage='S02', task=kind, status='done',
                verification=self.verification))
        nc.index_book(self.root)
        return evidence

    @staticmethod
    def issue(category='blocker'):
        result = dict(id='I01', category=category, chapters=[3, 5],
            minimum_fix='补充合成行动条件或明确延后核查。',
            verification='核对所列章节的技术标记与可执行条件。')
        if category == 'risk':
            result['tracking'] = '资料agent在第一卷相关场景写前核对；未核时保持待核。'
        return result

    def review_request(self, candidate, round_number=0, verdict='revise', issues=None):
        kind = 'outline_review' if round_number == 0 else 'outline_recheck_' + str(round_number)
        inputs = self.start(kind, [candidate])
        path = '创作约定/技术审读-' + str(round_number) + '.md'
        self.write(path, '合成独立报告标记 ' + str(round_number) + '，不证明真实阅读效果。')
        report = self.ev(path)
        if issues is None:
            issues = [self.issue()] if verdict == 'revise' else []
        checks = self.checks(kind, report)
        checks['outline_review'] = dict(report, summary='合成独立章目复核',
            candidate=dict(candidate), verdict=verdict,
            readiness=dict(actionable=verdict == 'pass', timeline=True,
                           causal_chain=True, author_boundaries=True), issues=issues)
        if verdict == 'revise':
            checks['outline_review']['options'] = [
                dict(id='A', description='缩小合成目标并补足行动条件', cost='减少本轮范围'),
                dict(id='B', description='保留范围并调整承重因果', cost='修改所列章节')]
        return dict(stage='S02', task=kind, inputs=inputs, outputs=[report], checks=checks)

    def review(self, candidate, round_number=0, verdict='revise', issues=None):
        request = self.review_request(candidate, round_number, verdict, issues)
        tc.submit(self.root, request)
        tc.update(self.root, dict(stage='S02', task=request['task'], status='done',
            verification=self.verification))
        return request['outputs'][0]

    def revise(self):
        return tc.outline_revise(self.root, dict(stage='S02', reason='处理当前合成硬阻断',
                                                verification=self.verification))

    def acceptance_request(self, candidate, report, confirmed=True):
        path = '创作约定/技术章目确认.dat'
        self.write(path, '合成作者：确认本次当前候选，按卷交付，允许进入第一卷准备。')
        confirmation = self.ev(path)
        checks = self.checks('outline_acceptance', confirmation)
        checks['current_version'] = dict(candidate, summary='最终当前章目版本')
        if confirmed:
            checks['author_confirmation'] = dict(confirmation, summary='合成作者明确确认当前候选',
                candidate=dict(candidate), source_quote='确认本次当前候选，按卷交付，允许进入第一卷准备。',
                scope='该合成当前候选和第一卷准备')
        inputs = [self.source, candidate, report, confirmation]
        return dict(stage='S02', task='outline_acceptance', status='done',
                    inputs=inputs, outputs=[confirmation], checks=checks,
                    verification=self.verification)

    def test_submitted_candidate_can_be_reviewed_without_pretending_final_acceptance(self):
        candidate = self.candidate()
        self.assertEqual(self.task('outline_draft')['status'], 'submitted')
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S02.outline_review')
        self.review(candidate)
        self.assertEqual(self.task('outline_draft')['status'], 'submitted')
        self.assertEqual(self.task('outline_review')['status'], 'done')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'next_plan')
        self.revise()
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S02.outline_revision_1')
        self.assertEqual(nc.digest(self.root / candidate['path']), candidate['sha256'])

    def test_two_revision_rounds_rebind_final_gate_and_preserve_versions(self):
        first = self.candidate()
        self.review(first)
        self.revise()
        second = self.candidate(1)
        self.review(second, 1)
        self.revise()
        stage = self.stage()
        acceptance = tc.selected_task(stage, 'outline_acceptance')
        self.assertIn('S02.outline_recheck_2', acceptance['depends_on'])
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S02.outline_revision_2')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'next_plan')
        third = self.candidate(2)
        final_report = self.review(third, 2, 'pass')
        tc.update(self.root, self.acceptance_request(third, final_report))
        self.assertEqual(self.task('outline_acceptance')['status'], 'done')
        self.assertEqual(self.task('outline_draft')['status'], 'done')
        for evidence in (first, second, third):
            self.assertEqual(nc.digest(self.root / evidence['path']), evidence['sha256'])

    def test_second_recheck_blocker_stops_at_limit_and_requires_author_choices(self):
        candidate = self.candidate()
        self.review(candidate)
        for round_number in (1, 2):
            self.revise()
            candidate = self.candidate(round_number)
            self.review(candidate, round_number)
        before = tc.load_plan(self.root)
        try:
            result = self.revise()
        except ValueError:
            result = None
        if result is not None:
            self.assertFalse(result['ok'])
            self.assertEqual(result.get('next_action'), 'author_decision')
        self.assertEqual(before, tc.load_plan(self.root))
        self.assertFalse(any(task['kind'].endswith('_3') for task in self.stage()['tasks']))
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'outline_acceptance')

    def test_final_blocking_report_cannot_omit_concrete_options_and_costs(self):
        candidate = self.candidate()
        self.review(candidate)
        self.revise()
        candidate = self.candidate(1)
        self.review(candidate, 1)
        self.revise()
        candidate = self.candidate(2)
        request = self.review_request(candidate, 2)
        request['checks']['outline_review'].pop('options')
        with self.assertRaises(ValueError):
            tc.submit(self.root, request)

    def test_nonblocking_historical_risk_and_local_blank_can_pass(self):
        candidate = self.candidate()
        report = self.review(candidate, verdict='pass', issues=[self.issue('risk'),
            dict(self.issue('blank'), id='I02', chapters=[7])])
        tc.update(self.root, self.acceptance_request(candidate, report))
        self.assertEqual(self.task('outline_acceptance')['status'], 'done')
        self.assertTrue(tc.check(self.root)['ok'])

    def test_risk_needs_tracking_and_blocker_cannot_be_called_pass(self):
        candidate = self.candidate()
        risk = self.issue('risk')
        del risk['tracking']
        request = self.review_request(candidate, verdict='pass', issues=[risk])
        with self.assertRaises(ValueError):
            tc.submit(self.root, request)
        request['checks']['outline_review']['issues'] = [self.issue('blocker')]
        with self.assertRaises(ValueError):
            tc.submit(self.root, request)

    def test_review_must_bind_current_candidate_not_old_or_unknown_version(self):
        candidate = self.candidate()
        self.review(candidate)
        self.revise()
        latest = self.candidate(1)
        request = self.review_request(latest, 1, 'pass')
        request['checks']['outline_review']['candidate'] = candidate
        with self.assertRaises(ValueError):
            tc.submit(self.root, request)
        request['checks']['outline_review']['candidate'] = dict(latest, sha256='0' * 64)
        with self.assertRaises(ValueError):
            tc.submit(self.root, request)

    def test_missing_author_confirmation_keeps_next_stage_and_prose_protected(self):
        candidate = self.candidate()
        report = self.review(candidate, verdict='pass')
        tc.plan(self.root, dict(id='S03', title='第一卷第一章准备', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S02', next_stage='S03'))
        with self.assertRaises(ValueError):
            tc.update(self.root, self.acceptance_request(candidate, report, confirmed=False))
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S03', 'prepare')
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S03', task='draft', status='doing'))
        self.assertEqual(nc.load_state(self.root)['chapters'], [])

    def test_confirmed_outline_transitions_directly_to_first_chapter_preparation(self):
        candidate = self.candidate()
        report = self.review(candidate, verdict='pass')
        tc.update(self.root, self.acceptance_request(candidate, report))
        tc.plan(self.root, dict(id='S03', title='按卷交付：第一卷第一章', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S02', next_stage='S03'))
        inputs = self.start('next_plan', [candidate, report])
        path = '创作约定/第一卷接续.dat'
        self.write(path, '合成接续：按卷交付；第一章准备、全卷评审与卷末交付里程碑。')
        evidence = self.ev(path)
        tc.submit(self.root, dict(stage='S02', task='next_plan', inputs=inputs,
            outputs=[evidence], checks=self.checks('next_plan', evidence)))
        tc.update(self.root, dict(stage='S02', task='next_plan', status='done',
            verification=self.verification))
        tc.close_stage(self.root, 'S02')
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S03.prepare')
        tc.update(self.root, dict(stage='S03', task='prepare', status='todo',
            inputs=[self.source, candidate], checks=dict(creative_decisions=self.preflight())))
        self.assertTrue(tc.gate(self.root, 'S03', 'prepare')['ok'])
        self.assertEqual(nc.load_state(self.root)['chapters'], [])

    def test_latest_recheck_dependency_cannot_be_removed_or_third_round_forged(self):
        candidate = self.candidate()
        self.review(candidate)
        self.revise()
        original = tc.load_plan(self.root)
        data = copy.deepcopy(original)
        stage = next(stage for stage in data['stages'] if stage['id'] == 'S02')
        tc.selected_task(stage, 'outline_acceptance')['depends_on'] = ['S02.outline_review']
        with self.assertRaises(ValueError):
            tc.save(self.root, data)
        self.assertEqual(tc.load_plan(self.root), original)
        data = copy.deepcopy(original)
        stage = next(stage for stage in data['stages'] if stage['id'] == 'S02')
        extra = copy.deepcopy(tc.selected_task(stage, 'outline_revision_1'))
        extra.update(id='S02.outline_revision_3', kind='outline_revision_3')
        stage['tasks'].insert(-2, extra)
        with self.assertRaises(ValueError):
            tc.save(self.root, data)

    def test_accepted_report_does_not_allow_acceptance_of_older_candidate(self):
        first = self.candidate()
        self.review(first)
        self.revise()
        latest = self.candidate(1)
        report = self.review(latest, 1, 'pass')
        request = self.acceptance_request(first, report)
        with self.assertRaises(ValueError):
            tc.update(self.root, request)

    def test_submitted_candidate_cannot_reopen_to_evade_revision_count(self):
        self.candidate()
        before = tc.load_plan(self.root)
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S02', task='outline_draft', status='doing',
                inputs=[self.source], checks=dict(creative_decisions=self.preflight())))
        self.assertEqual(tc.load_plan(self.root), before)

    def test_accepted_revision_cannot_reopen_as_an_unlimited_same_round(self):
        candidate = self.candidate()
        self.review(candidate)
        self.revise()
        self.candidate(1)
        before = tc.load_plan(self.root)
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S02', task='outline_revision_1', status='doing',
                inputs=[self.source], checks=dict(creative_decisions=self.preflight())))
        self.assertEqual(tc.load_plan(self.root), before)

    def test_independent_report_cannot_be_the_candidate_file_itself(self):
        candidate = self.candidate()
        self.start('outline_review', [candidate])
        report = dict(candidate, summary='错误地拿候选冒充报告', candidate=dict(candidate),
            verdict='pass', readiness=dict(actionable=True, timeline=True,
                causal_chain=True, author_boundaries=True), issues=[])
        with self.assertRaises(ValueError):
            tc.submit(self.root, dict(stage='S02', task='outline_review',
                inputs=[self.source, candidate], outputs=[candidate],
                checks=dict(outline_review=report)))

    def test_nested_candidate_must_be_safe_current_evidence(self):
        candidate = self.candidate()
        request = self.review_request(candidate, verdict='pass')
        for malformed in (None, [], dict(path='../outside.md', sha256=candidate['sha256']),
                          dict(path=candidate['path']),
                          dict(path=str(self.root / candidate['path']), sha256=candidate['sha256'])):
            with self.subTest(candidate=malformed):
                trial = copy.deepcopy(request)
                trial['checks']['outline_review']['candidate'] = malformed
                with self.assertRaises(ValueError):
                    tc.submit(self.root, trial)

    def final_blocked_outline(self):
        candidate = self.candidate()
        report = self.review(candidate)
        for number in (1, 2):
            self.revise()
            candidate = self.candidate(number)
            report = self.review(candidate, number)
        return candidate, report

    def rescope_request(self, candidate, report):
        path = '创作约定/上限后作者结构选择.dat'
        quote = '合成作者明确选择A：缩小合成目标并补足行动条件，授权另起章目规划，不开始正文。'
        self.write(path, quote)
        return dict(stage='S02', successor=dict(id='S03', title='作者另选结构方案章目'),
            reason='作者已选择上限后的新结构方案，保留原未通过章目并重新规划。',
            verification=self.verification,
            choice=dict(self.ev(path), source_quote=quote,
                scope='仅另起当前书的新结构章目阶段，不授权正文。', option_id='A',
                candidate=dict(candidate), report=dict(report)))

    def author_revision_request(self, candidate, report, stage='S02', successor='S03'):
        path = '创作约定/' + stage + '-作者修改反馈.dat'
        quote = '合成作者：不确认本次候选，请按本轮反馈调整章目，再交我确认。'
        self.write(path, stage + ' 当前版本的独立反馈记录：' + quote)
        return dict(stage=stage, successor=dict(id=successor, title='作者修改反馈后的新范围章目'),
            reason='当前章目已通过审读，但作者明确要求修改，保留未接受的旧范围。',
            verification=self.verification,
            choice=dict(self.ev(path), mode='author_revision', source_quote=quote,
                scope='只调整当前章目并重新审读确认，不授权开始正文。',
                candidate=dict(candidate), report=dict(report)))

    def passing_rescoped_outline(self, stage):
        creative = dict(creative_decisions=self.preflight())
        tc.update(self.root, dict(stage=stage, task='outline_draft', status='doing',
            inputs=[self.source], checks=creative))
        path = '大纲/' + stage + '-新范围技术候选.md'
        self.write(path, stage + ' 新范围的纯技术候选标记。')
        candidate = self.ev(path)
        tc.submit(self.root, dict(stage=stage, task='outline_draft', outputs=[candidate],
            checks=dict(creative, outline_candidate=dict(candidate, summary='新范围候选'))))
        with self.assertRaises(ValueError):
            tc.gate(self.root, stage, 'outline_acceptance')
        tc.update(self.root, dict(stage=stage, task='outline_review', status='doing', inputs=[candidate]))
        path = '创作约定/' + stage + '-新范围技术审读.dat'
        self.write(path, stage + ' 新范围的独立技术审读标记。')
        evidence = self.ev(path)
        report = dict(evidence, summary='新范围独立审读', candidate=dict(candidate), verdict='pass',
            readiness=dict(actionable=True, timeline=True, causal_chain=True, author_boundaries=True), issues=[])
        tc.submit(self.root, dict(stage=stage, task='outline_review', outputs=[evidence],
            checks=dict(outline_review=report)))
        tc.update(self.root, dict(stage=stage, task='outline_review', status='done',
            verification=self.verification))
        return candidate, evidence

    def test_author_can_request_a_new_scope_after_initial_passing_report(self):
        candidate = self.candidate()
        report = self.review(candidate, verdict='pass')
        old_tasks = copy.deepcopy(self.stage()['tasks'])
        request = self.author_revision_request(candidate, report)
        result = tc.outline_rescope(self.root, request)
        self.assertTrue(result['ok'])
        old = self.stage()
        self.assertEqual(old['status'], 'superseded')
        self.assertEqual(old['tasks'], old_tasks)
        self.assertEqual(tc.selected_task(old, 'outline_draft')['status'], 'submitted')
        self.assertEqual(tc.selected_task(old, 'outline_acceptance')['status'], 'todo')
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S03.outline_draft')
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        self.assertTrue(tc.check(self.root)['ok'])
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S03', 'outline_acceptance')
        new_candidate, new_report = self.passing_rescoped_outline('S03')
        missing = self.acceptance_request(new_candidate, new_report, confirmed=False)
        missing['stage'] = 'S03'
        with self.assertRaises(ValueError):
            tc.update(self.root, missing)
        acceptance = self.acceptance_request(new_candidate, new_report)
        acceptance['stage'] = 'S03'
        tc.update(self.root, acceptance)
        self.assertEqual(tc.selected_task(tc.load_plan(self.root)['stages'][-1],
                                         'outline_acceptance')['status'], 'done')
        self.assertTrue(tc.check(self.root)['ok'])

    def test_author_can_request_changes_after_second_passing_recheck_without_a_third_round(self):
        candidate = self.candidate()
        self.review(candidate)
        for number in (1, 2):
            self.revise()
            candidate = self.candidate(number)
            report = self.review(candidate, number, 'pass' if number == 2 else 'revise')
        old_tasks = copy.deepcopy(self.stage()['tasks'])
        tc.outline_rescope(self.root, self.author_revision_request(candidate, report))
        self.assertEqual(self.stage()['tasks'], old_tasks)
        self.assertEqual(tc.of.rounds(self.stage()), 2)
        self.assertFalse(any(task['kind'].endswith('_3') for task in self.stage()['tasks']))
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S03.outline_draft')
        self.assertTrue(tc.check(self.root)['ok'])

    def test_author_revision_requires_current_passing_report_and_explicit_fresh_feedback(self):
        candidate = self.candidate()
        report = self.review(candidate, verdict='pass')
        valid = self.author_revision_request(candidate, report)
        variants = []
        for field, value in (('mode', 'unknown'), ('option_id', 'A'), ('source_quote', '来源没有的答复'),
                             ('scope', ''), ('sha256', '0' * 64),
                             ('candidate', dict(candidate, sha256='0' * 64)),
                             ('report', dict(report, sha256='0' * 64))):
            request = copy.deepcopy(valid)
            request['choice'][field] = value
            variants.append(request)
        request = copy.deepcopy(valid)
        request['choice'].pop('mode')
        variants.append(request)
        request = copy.deepcopy(valid)
        request['successor']['workflow'] = 'chapter'
        variants.append(request)
        before = nc.inventory(self.root)
        for request in variants:
            with self.subTest(choice=request['choice']):
                with self.assertRaises(ValueError):
                    tc.outline_rescope(self.root, request)
                self.assertEqual(nc.inventory(self.root), before)
        tc.outline_rescope(self.root, valid)
        current_candidate, current_report = self.passing_rescoped_outline('S03')
        fresh = self.author_revision_request(current_candidate, current_report, 'S03', 'S04')
        reused = copy.deepcopy(fresh)
        reused['choice'].update(path=valid['choice']['path'], sha256=valid['choice']['sha256'])
        copied_path = '创作约定/复制旧反馈技术夹具.dat'
        self.write(copied_path, (self.root / valid['choice']['path']).read_text(encoding='utf-8'))
        copied = copy.deepcopy(fresh)
        copied['choice'].update(self.ev(copied_path))
        before = nc.inventory(self.root)
        for request in (reused, copied):
            with self.assertRaises(ValueError):
                tc.outline_rescope(self.root, request)
            self.assertEqual(nc.inventory(self.root), before)
        tc.outline_rescope(self.root, fresh)
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S04.outline_draft')
        self.assertTrue(tc.check(self.root)['ok'])

    def test_author_revision_cannot_bypass_a_blocking_report_or_replace_an_accepted_candidate(self):
        candidate = self.candidate()
        report = self.review(candidate)
        request = self.author_revision_request(candidate, report)
        before = nc.inventory(self.root)
        with self.assertRaises(ValueError):
            tc.outline_rescope(self.root, request)
        self.assertEqual(nc.inventory(self.root), before)
        self.revise()
        candidate = self.candidate(1)
        report = self.review(candidate, 1, 'pass')
        tc.update(self.root, self.acceptance_request(candidate, report))
        request = self.author_revision_request(candidate, report)
        before = nc.inventory(self.root)
        with self.assertRaises(ValueError):
            tc.outline_rescope(self.root, request)
        self.assertEqual(nc.inventory(self.root), before)

    def test_author_revision_archives_future_plan_and_keeps_old_report_and_feedback_hash_checked(self):
        candidate = self.candidate()
        report = self.review(candidate, verdict='pass')
        tc.plan(self.root, dict(id='FUTURE', title='作者反馈前预建首章', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S02', next_stage='FUTURE'))
        future = copy.deepcopy(tc.load_plan(self.root)['stages'][-1])
        request = self.author_revision_request(candidate, report)
        tc.outline_rescope(self.root, request)
        self.assertEqual(self.stage()['outline_supersession']['superseded_future_plan'], future)
        self.assertNotIn('FUTURE', [stage['id'] for stage in tc.load_plan(self.root)['stages']])
        tampered = tc.load_plan(self.root)
        old = next(stage for stage in tampered['stages'] if stage['id'] == 'S02')
        tc.selected_task(old, 'outline_review')['title'] = '试图重写已封存的任务历史'
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'superseded outline task history has changed'):
            tc.save(self.root, tampered)
        self.assertEqual(nc.inventory(self.root), before)
        for evidence in (report, request['choice']):
            path = self.root / evidence['path']
            original = path.read_bytes()
            path.write_text('已封存证据被改动。', encoding='utf-8')
            self.assertFalse(tc.check(self.root)['ok'])
            path.write_bytes(original)
            self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(nc.load_state(self.root)['chapters'], [])

    def test_author_rescope_preserves_failed_stage_and_starts_only_new_outline(self):
        candidate, report = self.final_blocked_outline()
        old_tasks = copy.deepcopy(self.stage()['tasks'])
        request = self.rescope_request(candidate, report)
        request_file = Path(self.temp.name) / '中文 改范围请求.json'
        request_file.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        process = subprocess.run([sys.executable, str(Path(tc.__file__)), 'outline-rescope',
            '--book', str(self.root), '--request', '@' + str(request_file)], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertTrue(result['ok'])
        data = tc.load_plan(self.root)
        old = next(stage for stage in data['stages'] if stage['id'] == 'S02')
        new = next(stage for stage in data['stages'] if stage['id'] == 'S03')
        self.assertEqual(old['status'], 'superseded')
        self.assertEqual(old['tasks'], old_tasks)
        self.assertEqual(tc.selected_task(old, 'outline_draft')['status'], 'submitted')
        self.assertEqual(tc.selected_task(old, 'outline_acceptance')['status'], 'todo')
        self.assertEqual(new['workflow'], 'outline')
        self.assertEqual(data['current_stage'], 'S03')
        self.assertEqual(data['current_task'], 'S03.outline_draft')
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        self.assertTrue(tc.check(self.root)['ok'])
        tc.update(self.root, dict(stage='S03', task='outline_draft', status='todo',
            inputs=[self.source], checks=dict(creative_decisions=self.preflight())))
        self.assertTrue(tc.gate(self.root, 'S03', 'outline_draft')['ok'])
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S02', task='outline_draft', status='doing'))
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'outline_review')
        with self.assertRaises(ValueError):
            tc.plan(self.root, dict(id='S02', title='试图恢复被取代阶段'))

    def test_rescope_cannot_reset_round_limit_before_final_blocked_recheck(self):
        candidate = self.candidate()
        report = self.review(candidate)
        request = self.rescope_request(candidate, report)
        before = nc.inventory(self.root)
        with self.assertRaises(ValueError):
            tc.outline_rescope(self.root, request)
        self.assertEqual(nc.inventory(self.root), before)

    def test_rescope_requires_exact_author_choice_and_cannot_enter_prose(self):
        candidate, report = self.final_blocked_outline()
        valid = self.rescope_request(candidate, report)
        variants = []
        request = copy.deepcopy(valid)
        request.pop('choice')
        variants.append(request)
        for field, value in (('option_id', 'unknown'), ('sha256', '0' * 64),
                             ('candidate', self.task('outline_draft')['outputs'][0]),
                             ('report', self.task('outline_review')['outputs'][0])):
            request = copy.deepcopy(valid)
            request['choice'][field] = value
            variants.append(request)
        request = copy.deepcopy(valid)
        request['successor'].update(workflow='chapter', chapter=1)
        variants.append(request)
        before = nc.inventory(self.root)
        for request in variants:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    tc.outline_rescope(self.root, request)
                self.assertEqual(nc.inventory(self.root), before)

    def test_superseded_reports_remain_hash_checked_in_the_new_stage(self):
        candidate, report = self.final_blocked_outline()
        tc.outline_rescope(self.root, self.rescope_request(candidate, report))
        self.assertTrue(tc.check(self.root)['ok'])
        path = self.root / report['path']
        original = path.read_bytes()
        path.write_text('篡改已取代阶段的旧复核报告。', encoding='utf-8')
        self.assertFalse(tc.check(self.root)['ok'])
        path.write_bytes(original)
        self.assertTrue(tc.check(self.root)['ok'])

    def test_historical_submitted_candidate_does_not_block_later_finalization_defer(self):
        candidate, report = self.final_blocked_outline()
        tc.outline_rescope(self.root, self.rescope_request(candidate, report))
        old_tasks = copy.deepcopy(self.stage()['tasks'])
        creative = dict(creative_decisions=self.preflight())
        tc.update(self.root, dict(stage='S03', task='outline_draft', status='todo',
            inputs=[self.source], checks=creative))
        tc.gate(self.root, 'S03', 'outline_draft')
        tc.update(self.root, dict(stage='S03', task='outline_draft', status='doing'))
        path = '大纲/新范围技术候选.md'
        self.write(path, '新授权范围的纯技术候选版本标记。')
        new_candidate = self.ev(path)
        tc.submit(self.root, dict(stage='S03', task='outline_draft', outputs=[new_candidate],
            checks=dict(creative, outline_candidate=dict(new_candidate, summary='新范围技术候选'))))
        tc.update(self.root, dict(stage='S03', task='outline_review', status='doing',
            inputs=[new_candidate]))
        path = '创作约定/新范围技术审读.md'
        self.write(path, '新范围纯技术报告，不证明实际阅读效果。')
        new_report = self.ev(path)
        review = dict(new_report, summary='新范围技术独立审读', candidate=new_candidate,
            verdict='pass', readiness=dict(actionable=True, timeline=True,
                causal_chain=True, author_boundaries=True), issues=[])
        tc.submit(self.root, dict(stage='S03', task='outline_review', outputs=[new_report],
            checks=dict(outline_review=review)))
        tc.update(self.root, dict(stage='S03', task='outline_review', status='done',
            verification=self.verification))
        acceptance = self.acceptance_request(new_candidate, new_report)
        acceptance['stage'] = 'S03'
        tc.update(self.root, acceptance)
        tc.plan(self.root, dict(id='F', title='新范围后定稿技术阶段', workflow='finalization'))
        tc.plan(self.root, dict(id='S03', next_stage='F'))
        tc.update(self.root, dict(stage='S03', task='next_plan', status='todo',
            inputs=[self.source], checks=creative))
        tc.update(self.root, dict(stage='S03', task='next_plan', status='done',
            outputs=[self.source], checks=self.checks('next_plan', self.source),
            verification=self.verification))
        tc.close_stage(self.root, 'S03')
        tc.update(self.root, dict(stage='F', task='whole_read', status='doing', inputs=[new_candidate]))
        path = '创作约定/定稿整体阅读技术报告.md'
        self.write(path, '纯任务技术报告，无正式章节，不证明真实全书阅读。')
        evidence = self.ev(path)
        tc.submit(self.root, dict(stage='F', task='whole_read', outputs=[evidence],
            checks=self.checks('whole_read', evidence)))
        tc.update(self.root, dict(stage='F', task='whole_read', status='done',
            verification=self.verification))
        result = tc.defer(self.root, dict(stage='F',
            revision=dict(id='R', title='定稿让位章节修订技术阶段', chapter=1),
            reason='验证旧失败章目保留submitted不妨碍未来真实任务调度。', verification=self.verification))
        self.assertEqual(result['current_stage'], 'R')
        self.assertEqual(self.stage()['status'], 'superseded')
        self.assertEqual(self.stage()['tasks'], old_tasks)
        self.assertEqual(tc.selected_task(self.stage(), 'outline_draft')['status'], 'submitted')
        self.assertTrue(tc.check(self.root)['ok'])

    def test_rescope_archives_a_pristine_preplanned_chapter_without_starting_prose(self):
        candidate, report = self.final_blocked_outline()
        tc.plan(self.root, dict(id='FUTURE', title='预建但未开工的首章', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S02', next_stage='FUTURE'))
        future = copy.deepcopy(next(stage for stage in tc.load_plan(self.root)['stages']
                                    if stage['id'] == 'FUTURE'))
        result = tc.outline_rescope(self.root, self.rescope_request(candidate, report))
        self.assertTrue(result['ok'])
        data = tc.load_plan(self.root)
        self.assertNotIn('FUTURE', [stage['id'] for stage in data['stages']])
        self.assertEqual(data['current_stage'], 'S03')
        self.assertEqual(self.stage()['outline_supersession']['superseded_future_plan'], future)
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        self.assertTrue(tc.check(self.root)['ok'])

    def test_rescope_does_not_discard_preplanned_chapter_with_registered_inputs(self):
        candidate, report = self.final_blocked_outline()
        tc.plan(self.root, dict(id='FUTURE', title='已有登记输入的后续章计划', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S02', next_stage='FUTURE'))
        data = tc.load_plan(self.root)
        future = next(stage for stage in data['stages'] if stage['id'] == 'FUTURE')
        tc.selected_task(future, 'prepare')['inputs'] = [self.source]
        tc.save(self.root, data)
        request = self.rescope_request(candidate, report)
        before = nc.inventory(self.root)
        with self.assertRaises(ValueError):
            tc.outline_rescope(self.root, request)
        self.assertEqual(nc.inventory(self.root), before)
        self.assertIn('FUTURE', [stage['id'] for stage in tc.load_plan(self.root)['stages']])

    def test_full_planning_specialist_can_bind_live_materials_without_authorizing_prose(self):
        data = tc.load_plan(self.root)
        stage = next(stage for stage in data['stages'] if stage['id'] == 'S02')
        task_id = 'S02.storyline_plan'
        specialist = dict(id=task_id, kind='storyline_management', title='完整规划的故事线技术任务',
            role='specialist', status='todo', depends_on=[], inputs=[], outputs=[], checks={},
            acceptance='合成专项方案与活动资料均登记真实版本，不写小说。',
            verification=None, reason='', history=[], operation_id='')
        stage['tasks'].insert(0, specialist)
        tc.selected_task(stage, 'outline_draft')['depends_on'] = [task_id]
        data['current_task'] = task_id
        tc.save(self.root, data)
        for path in ('草稿与备选/第0001章-技术越权.md', '第0001章-技术越权.md'):
            with self.subTest(path=path):
                bad = copy.deepcopy(data)
                bad_stage = next(stage for stage in bad['stages'] if stage['id'] == 'S02')
                tc.selected_task(bad_stage, task_id)['outputs'] = [dict(path=path, sha256='0' * 64)]
                before = nc.inventory(self.root)
                with self.assertRaises(ValueError):
                    tc.save(self.root, bad)
                self.assertEqual(nc.inventory(self.root), before)
        self.start('storyline_management')
        live = '故事线/故事线管理.md'
        snapshot = '创作约定/完整故事线规划技术依据.md'
        self.write(live, '纯技术专项活动资料版本，不包含小说情节。')
        self.write(snapshot, '本轮专项技术方案的不可变版本依据，不包含小说情节。')
        output = dict(self.ev(snapshot), material_files=[self.ev(live)])
        tc.submit(self.root, dict(stage='S02', task=task_id, outputs=[output],
            checks=dict(creative_decisions=self.preflight())))
        self.assertEqual(self.task(task_id)['status'], 'submitted')
        tc.update(self.root, dict(stage='S02', task=task_id, status='done',
            verification=self.verification))
        # The specialist binds the draft through the DAG; deliberately omit
        # its output from draft.inputs so the review gate must recurse through
        # prerequisites rather than accidentally catch only a direct hash.
        self.start('outline_draft')
        self.assertTrue(tc.gate(self.root, 'S02', 'outline_draft')['ok'])
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        candidate_path = '大纲/完整专项技术章目.md'
        self.write(candidate_path, '纯技术章目版本标记，无小说正文。')
        candidate = self.ev(candidate_path)
        tc.submit(self.root, dict(stage='S02', task='outline_draft', outputs=[candidate],
            checks=dict(creative_decisions=self.preflight(),
                        outline_candidate=dict(candidate, summary='完整专项后技术章目'))))
        target = self.root / snapshot
        original = target.read_bytes()
        target.write_text('专项上游不可变证据被改动，候选字节仍未变化。', encoding='utf-8')
        self.assertEqual(nc.digest(self.root / candidate_path), candidate['sha256'])
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'outline_review')
        target.write_bytes(original)
        self.assertTrue(tc.gate(self.root, 'S02', 'outline_review')['ok'])
        report = self.review(candidate)
        for number in (1, 2):
            self.revise()
            candidate = self.candidate(number)
            report = self.review(candidate, number)
        tc.outline_rescope(self.root, self.rescope_request(candidate, report))
        self.assertEqual(self.stage()['status'], 'superseded')
        self.assertEqual(nc.digest(self.root / live), output['material_files'][0]['sha256'])
        self.assertTrue(tc.check(self.root)['ok'])


if __name__ == '__main__':
    unittest.main()
