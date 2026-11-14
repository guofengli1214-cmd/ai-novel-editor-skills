"""Candidate-only trial closure and scope guards using technical strings only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc


class TrialOnlyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        title = '候选交付技术夹具'
        self.root = nc.init_book(Path(self.temp.name), title, title_decision=dict(
            resolution='provided', value=title, source_quote='合成技术输入：项目名已明确。',
            scope='本轮机械测试', prior_status='proposed'))
        self.source = '创作约定/固定技术范围.dat'
        self.write(self.source, 'TECHNICAL_FIXTURE_ONLY: compare two labels and read feedback; '
                   'no voice selection, formal draft, adoption, or subsequent creation.')
        self.policy = '创作约定/固定技术规则.json'
        self.write(self.policy, json.dumps(dict(quote_style='unspecified')))
        self.verification = dict(agent_id='fixture-main', source='技术范围、输入、产物及版本核对')

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8')

    def ev(self, path):
        return dict(path=path, sha256=nc.digest(self.root / path))

    def stage(self, stage_id):
        return next(stage for stage in tc.load_plan(self.root)['stages'] if stage['id'] == stage_id)

    def task(self, stage_id, kind):
        return tc.selected_task(self.stage(stage_id), kind)

    def configure(self, stage_id, kind, inputs=()):
        registered = [self.ev(self.source), *inputs]
        checks = {}
        if kind in tc.CREATIVE_DECISION_TASKS:
            checks['creative_decisions'] = dict(
                self.ev(self.source), summary='仅执行已有技术范围。', items=[],
                none_reason='本步骤不作新的创作选择，仅交付技术夹具或登记终点。')
        tc.update(self.root, dict(stage=stage_id, task=kind, status='todo',
                                  inputs=registered, checks=checks))
        return registered, checks

    def submit(self, stage_id, kind, paths=None, inputs=()):
        registered, checks = self.configure(stage_id, kind, inputs)
        tc.gate(self.root, stage_id, kind)
        tc.update(self.root, dict(stage=stage_id, task=kind, status='doing'))
        tc.gate(self.root, stage_id, kind)  # Independent executing-agent recheck.
        paths = paths or ['审稿与修订/' + stage_id + '-' + kind + '-技术报告.md']
        for index, path in enumerate(paths):
            self.write(path, 'TECHNICAL_FIXTURE_ONLY ' + kind + ' ' + str(index) + '\n')
        outputs = [self.ev(path) for path in paths]
        checks.update({key: dict(outputs[-1], summary='技术交付、读取范围和限制均已记录。')
                       for key in tc.EDITORIAL_CHECKS.get(kind, ())})
        if kind == 'direction':
            choice = dict(resolution='provided', value='合成技术方向',
                          source_quote='合成输入：本轮已给定这个技术范围。', scope='本轮机械测试')
            for category, topics in tc.DIRECTION_DECISIONS.items():
                checks[category]['decisions'] = {topic: dict(choice) for topic in topics}
            checks['startup_mode']['decision'] = dict(choice)
            checks['author_preferences']['decisions']['hidden_plot'] = dict(choice, value='disabled')
            checks['author_preferences']['decisions']['title'] = dict(
                choice, value=nc.load_state(self.root)['title'], prior_status='proposed')
        if kind == 'prepare':
            registered.append(self.ev(self.policy))
            checks['prose_policy'] = dict(self.ev(self.policy), summary='仅供技术检查的固定规则。')
        tc.submit(self.root, dict(stage=stage_id, task=kind, inputs=registered,
                                  outputs=outputs, checks=checks))
        return outputs

    def complete(self, stage_id, kind, paths=None, inputs=()):
        outputs = self.submit(stage_id, kind, paths, inputs)
        tc.update(self.root, dict(stage=stage_id, task=kind, status='done',
                                  verification=self.verification))
        return outputs

    def begin_trial_only(self):
        tc.plan(self.root, dict(id='S02', title='仅候选与反馈', workflow='trial_only', chapter=1))
        self.complete('S01', 'direction')
        self.complete('S01', 'concept')
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.complete('S01', 'next_plan')
        tc.close_stage(self.root, 'S01')

    def candidates(self):
        self.complete('S02', 'prepare')
        return self.complete('S02', 'voice_trial', [
            '草稿与备选/第0001章-技术标签A.md',
            '草稿与备选/第0001章-技术标签B.md',
            '审稿与修订/候选技术对照.md'])

    def feedback(self, candidates):
        return self.complete('S02', 'voice_first_read', inputs=candidates[:2])

    def finish_trial_only(self):
        candidates = self.candidates()
        self.feedback(candidates)
        tc.plan(self.root, dict(id='S02', end_reason='合成限定范围已交付；不选声、不采用。'))
        self.complete('S02', 'next_plan')
        tc.close_stage(self.root, 'S02')
        return candidates

    def extra_task(self, data, kind='research'):
        stage = next(item for item in data['stages'] if item['id'] == 'S02')
        task = copy.deepcopy(stage['tasks'][0])
        task.update(id='S02.extra', kind=kind, title='额外技术核查',
                    depends_on=['S02.prepare'])
        stage['tasks'].insert(-1, task)
        return task

    def test_limited_candidates_and_feedback_close_without_selection_or_adoption(self):
        self.begin_trial_only()
        self.assertEqual([task['kind'] for task in self.stage('S02')['tasks']],
                         ['prepare', 'voice_trial', 'voice_first_read', 'next_plan'])
        candidates = self.finish_trial_only()
        self.assertEqual(self.stage('S02')['status'], 'done')
        self.assertEqual(tc.load_plan(self.root)['current_task'], '')
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        self.assertEqual(nc.load_state(self.root)['pending_operations'], [])
        self.assertTrue(all((self.root / item['path']).is_file() for item in candidates))

    def test_closed_trial_can_accept_new_separate_authorized_work(self):
        self.begin_trial_only()
        candidates = self.finish_trial_only()
        before = [(item['path'], nc.digest(self.root / item['path'])) for item in candidates]
        tc.plan(self.root, dict(id='S03', title='后续单独授权的材料核查', workflow='materials'))
        self.assertEqual(tc.load_plan(self.root)['current_stage'], 'S03')
        self.complete('S03', 'scope')
        self.complete('S03', 'organize')
        tc.plan(self.root, dict(id='S03', end_reason='合成材料核查已交付。'))
        self.complete('S03', 'next_plan')
        tc.close_stage(self.root, 'S03')
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(before, [(item['path'], nc.digest(self.root / item['path']))
                                  for item in candidates])

    def test_trial_only_cannot_supply_formal_adoption_or_skip_independent_feedback(self):
        self.begin_trial_only()
        candidates = self.candidates()
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'next_plan')
        with self.assertRaises(ValueError):
            tc.require_adoption(self.root, dict(number=1, task_stage='S02', draft=candidates[0]['path']))
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S02', task='voice_first_read',
                                      status='not_applicable', reason='只交候选不能免除独立反馈。'))
        self.feedback(candidates)
        with self.assertRaises(ValueError):
            tc.close_stage(self.root, 'S02')
        self.assertEqual(nc.load_state(self.root)['chapters'], [])

    def test_trial_only_cannot_add_selection_drafting_or_adoption_steps(self):
        self.begin_trial_only()
        original = tc.load_plan(self.root)
        for kind in ('voice_selection', 'draft', 'first_read', 'review', 'revise', 'recheck',
                     'adopt', 'sync', 'whole_read', 'structural_revision', 'whole_recheck', 'finalize'):
            with self.subTest(kind=kind):
                data = copy.deepcopy(original)
                self.extra_task(data, kind)
                with self.assertRaises(ValueError):
                    tc.save(self.root, data)
                self.assertEqual(tc.load_plan(self.root), original)

    def test_other_tasks_cannot_launder_drafts_or_formal_outputs(self):
        self.begin_trial_only()
        original = tc.load_plan(self.root)
        for path in ('第0001章-技术正式稿.md', '草稿与备选/技术新稿.md'):
            for nested in (False, True):
                with self.subTest(path=path, nested=nested):
                    data = copy.deepcopy(original)
                    extra = self.extra_task(data)
                    reference = dict(path=path, sha256='0' * 64)
                    extra['outputs'] = ([dict(path='历史版本/技术固定凭据.md', sha256='0' * 64,
                                               material_files=[reference])] if nested else [reference])
                    with self.assertRaises(ValueError):
                        tc.save(self.root, data)
                    self.assertEqual(tc.load_plan(self.root), original)

    def test_candidate_change_invalidates_feedback_and_terminal_acceptance(self):
        self.begin_trial_only()
        candidates = self.candidates()
        self.feedback(candidates)
        tc.plan(self.root, dict(id='S02', end_reason='技术交付终点。'))
        self.complete('S02', 'next_plan')
        self.write(candidates[0]['path'], 'TECHNICAL_FIXTURE_ONLY CHANGED\n')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'next_plan')
        with self.assertRaises(ValueError):
            tc.close_stage(self.root, 'S02')
        self.assertFalse(tc.check(self.root)['ok'])
        tc.update(self.root, dict(stage='S02', task='voice_trial', status='todo',
                                  reason='技术候选版本变化，重新读取与验收。'))
        for kind in ('voice_first_read', 'next_plan'):
            self.assertEqual(self.task('S02', kind)['status'], 'todo')
            self.assertTrue(self.task('S02', kind)['history'])

    def test_numbered_candidates_must_match_the_finite_trial_scope(self):
        self.begin_trial_only()
        self.complete('S02', 'prepare')
        with self.assertRaises(ValueError):
            self.submit('S02', 'voice_trial', ['草稿与备选/第0002章-越界技术标签.md'])

    def test_new_finite_mode_keeps_old_complete_trial_contracts(self):
        old_core = ['prepare', 'voice_trial', 'draft', 'first_read', 'review', 'revise',
                    'recheck', 'adopt', 'sync', 'next_plan']
        for version in (1, 2):
            with self.subTest(version=version):
                stage = tc.new_stage('OLD', '完整试写技术流程', 'trial', chapter=1,
                                     workflow_version=version)
                self.assertEqual([task['kind'] for task in stage['tasks']], old_core)
                self.assertEqual(tc.workflow_version(stage), version)
        with self.assertRaises(ValueError):
            tc.new_stage('LIMITED', '有限模式不得降级', 'trial_only', chapter=1, workflow_version=1)
        original = tc.load_plan(self.root)
        with self.assertRaises(ValueError):
            tc.plan(self.root, dict(id='NO_CHAPTER', title='缺少本轮章号', workflow='trial_only'))
        self.assertEqual(tc.load_plan(self.root), original)


if __name__ == '__main__':
    unittest.main()
