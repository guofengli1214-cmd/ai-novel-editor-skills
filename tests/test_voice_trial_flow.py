"""A/B selection ordering using technical strings, never fiction or real reading."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc


class VoiceTrialFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        title = '对照流程技术夹具'
        self.root = nc.init_book(Path(self.temp.name), title, title_decision=dict(
            resolution='provided', value=title, source_quote='合成作者明确给定技术项目名称。',
            scope='本次技术流程', prior_status='proposed'))
        self.source_path = '创作约定/固定技术决定.md'
        self.write(self.source_path, '合成技术输入：仅比较两种编码标签，选择A，仅用于测试。')
        self.source = self.ev(self.source_path)
        self.policy_path = '创作约定/技术标点规则.json'
        self.write(self.policy_path, json.dumps(dict(quote_style='chinese_double', quote_confirmed=True)))
        self.policy = self.ev(self.policy_path)
        tc.plan(self.root, dict(id='S02', title='同输入双标签机械流程', workflow='trial', chapter=1))
        self.complete('S01', 'direction', ['审稿与修订/技术方向记录.md'])
        self.complete('S01', 'concept', ['审稿与修订/技术构思记录.md'])
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.complete('S01', 'next_plan', ['审稿与修订/技术接续记录.md'])
        tc.close_stage(self.root, 'S01')
        self.add_selection_tasks()

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding='utf-8')

    def ev(self, path):
        return dict(path=path, sha256=nc.digest(self.root / path))

    def task(self, stage_id, name):
        stage = next(s for s in tc.load_plan(self.root)['stages'] if s['id'] == stage_id)
        return tc.selected_task(stage, name)

    def add_selection_tasks(self):
        data = tc.load_plan(self.root)
        stage = next(s for s in data['stages'] if s['id'] == 'S02')
        draft = tc.selected_task(stage, 'draft')
        tasks = []
        for kind, predecessor, role in (
                ('voice_first_read', 'S02.voice_trial', 'reviewer'),
                ('voice_selection', 'S02.voice_first_read', 'materials')):
            tasks.append(dict(
                id='S02.' + kind, kind=kind, title='中性技术节点 ' + kind, role=role,
                status='todo', depends_on=[predecessor], inputs=[], outputs=[], checks={},
                acceptance='仅核对技术文件和真实状态转换，不证明文学阅读或作者真实决定',
                verification=None, reason='', history=[], operation_id=''))
        index = stage['tasks'].index(draft)
        stage['tasks'][index:index] = tasks
        draft['depends_on'].append('S02.voice_selection')
        # Exercise the documented single-ledger/manual-JSON + render route.
        path = self.root / tc.TASK_FILE
        raw = path.read_text(encoding='utf-8')
        start = raw.index(tc.BEGIN) + len(tc.BEGIN)
        end = raw.index(tc.END)
        path.write_text(raw[:start] + '\n```json\n' + json.dumps(data, ensure_ascii=False, indent=2)
                        + '\n```\n' + raw[end:], encoding='utf-8')
        tc.render(self.root)

    def creative_check(self, kind):
        check = dict(self.source, summary='合成技术决定和适用范围', items=[])
        if kind == 'voice_selection':
            check['items'] = [dict(
                id='technical-selection-A', topic='voice_selection', record_id='合成决定A',
                resolution='selected', value='A', question_quote='合成技术提问：标签A还是标签B？',
                source_quote='A', scope='本次单一技术输入，仅供机械测试')]
        else:
            check['none_reason'] = '仅执行已提供技术输入，不产生新的创作选择。'
        return check

    def configure(self, stage_id, name, inputs=()):
        kind = self.task(stage_id, name)['kind']
        registered = [self.source, *inputs]
        checks = {}
        if kind in tc.CREATIVE_DECISION_TASKS:
            checks['creative_decisions'] = self.creative_check(kind)
        tc.update(self.root, dict(stage=stage_id, task=name, status='todo',
                                  inputs=registered, checks=checks))
        return registered, checks

    def submit_task(self, stage_id, name, paths, inputs=()):
        kind = self.task(stage_id, name)['kind']
        registered, preflight = self.configure(stage_id, name, inputs)
        tc.gate(self.root, stage_id, name)
        tc.update(self.root, dict(stage=stage_id, task=name, status='doing'))
        self.assertEqual(self.task(stage_id, name)['status'], 'doing')
        tc.gate(self.root, stage_id, name)  # Executing agent independently rechecks.
        for index, path in enumerate(paths):
            self.write(path, 'TECHNICAL_FIXTURE_ONLY ' + name + ' ' + str(index) + '\n')
        outputs = [self.ev(path) for path in paths]
        checks = {key: dict(outputs[-1], summary='技术证据 ' + key)
                  for key in tc.EDITORIAL_CHECKS.get(kind, ())}
        checks.update(preflight)
        if kind == 'direction':
            choice = dict(resolution='provided', value='合成技术选择',
                          source_quote='合成作者已给定这项技术选择。', scope='本次机械流程')
            for category, topics in tc.DIRECTION_DECISIONS.items():
                checks[category]['decisions'] = {topic: dict(choice) for topic in topics}
            checks['startup_mode']['decision'] = dict(choice)
            checks['author_preferences']['decisions']['hidden_plot'] = dict(
                choice, value='disabled')
            checks['author_preferences']['decisions']['title'] = dict(
                choice, value=nc.load_state(self.root)['title'], prior_status='proposed')
        if kind == 'prepare':
            registered.append(self.policy)
            checks['prose_policy'] = dict(self.policy, summary='技术约定提取的标点政策')
        tc.submit(self.root, dict(stage=stage_id, task=name, inputs=registered,
                                  outputs=outputs, checks=checks))
        self.assertEqual(self.task(stage_id, name)['status'], 'submitted')
        return outputs

    def accept(self, stage_id, name):
        tc.update(self.root, dict(stage=stage_id, task=name, status='done', verification=dict(
            agent_id='fixture-main', source='已核对本次机械文件、依赖及合成输入')))
        self.assertEqual(self.task(stage_id, name)['status'], 'done')

    def complete(self, stage_id, name, paths, inputs=()):
        outputs = self.submit_task(stage_id, name, paths, inputs)
        self.accept(stage_id, name)
        return outputs

    def prepare_candidates(self):
        self.complete('S02', 'prepare', ['审稿与修订/技术准备.md'])
        return self.complete('S02', 'voice_trial', [
            '草稿与备选/第0001章-标签A.md', '草稿与备选/第0001章-标签B.md',
            '审稿与修订/技术候选对照.md'])

    def assert_gate_blocked(self, name):
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', name)

    def test_candidate_reading_then_author_selection_then_final_chapter_reading(self):
        candidates = self.prepare_candidates()
        self.configure('S02', 'voice_selection')
        self.assert_gate_blocked('voice_selection')
        self.assert_gate_blocked('draft')
        feedback = self.submit_task('S02', 'voice_first_read', ['审稿与修订/技术候选首读.md'], candidates[:2])
        self.assert_gate_blocked('voice_selection')  # Submitted feedback is not acceptance.
        self.accept('S02', 'voice_first_read')
        self.assert_gate_blocked('draft')
        selected = self.submit_task('S02', 'voice_selection', ['创作约定/技术选声记录.md'], feedback)
        self.assert_gate_blocked('draft')  # Submitted choice is not acceptance.
        self.accept('S02', 'voice_selection')
        draft = self.submit_task('S02', 'draft', ['草稿与备选/第0001章-技术成稿.md'], [candidates[0], *selected])
        self.assert_gate_blocked('first_read')
        self.accept('S02', 'draft')
        self.complete('S02', 'first_read', ['审稿与修订/技术章稿首读.md'], draft)
        self.assertEqual(self.task('S02', 'voice_first_read')['outputs'], feedback)
        self.assertNotEqual(feedback, self.task('S02', 'first_read')['outputs'])
        self.assertTrue(tc.check(self.root)['ok'])

    def test_candidate_changes_invalidate_selection_and_reopen_its_dependents(self):
        candidates = self.prepare_candidates()
        feedback = self.complete('S02', 'voice_first_read', ['审稿与修订/技术候选首读.md'], candidates[:2])
        self.complete('S02', 'voice_selection', ['创作约定/技术选声记录.md'], feedback)
        self.write(candidates[0]['path'], 'TECHNICAL_FIXTURE_ONLY CHANGED\n')
        self.assert_gate_blocked('draft')
        tc.update(self.root, dict(stage='S02', task='voice_trial', status='todo', reason='技术候选输入变化'))
        for name in ('voice_first_read', 'voice_selection'):
            task = self.task('S02', name)
            self.assertEqual(task['status'], 'todo')
            self.assertTrue(task['history'])
        self.assert_gate_blocked('voice_selection')

    def test_required_trial_nodes_cannot_be_skipped_and_selection_needs_author_evidence(self):
        candidates = self.prepare_candidates()
        for name in ('voice_first_read', 'voice_selection'):
            with self.subTest(task=name):
                with self.assertRaisesRegex(ValueError, 'voice.*not_applicable'):
                    tc.update(self.root, dict(stage='S02', task=name, status='not_applicable',
                                              reason='不能跳过已选定的试写比较流程', verification=dict(
                                                  agent_id='fixture-main', source='技术跳过尝试')))
        feedback = self.complete('S02', 'voice_first_read', ['审稿与修订/技术候选首读.md'], candidates[:2])
        inputs, checks = self.configure('S02', 'voice_selection', feedback)
        bad = copy.deepcopy(checks)
        bad['creative_decisions']['items'][0]['resolution'] = 'unresolved'
        tc.update(self.root, dict(stage='S02', task='voice_selection', status='todo', inputs=inputs, checks=bad))
        self.assert_gate_blocked('voice_selection')
        self.assert_gate_blocked('draft')


if __name__ == '__main__':
    unittest.main()
