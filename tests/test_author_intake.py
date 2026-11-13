"""Author-decision gate mechanics with synthetic decisions, never fiction."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc


class AuthorIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = nc.init_book(Path(self.temp.name), '作者了解技术夹具')
        self.evidence = '创作约定/技术作者决定.md'
        (self.root / self.evidence).write_text('合成作者决定，仅验证字段与流程。', encoding='utf-8')

    def checks(self, resolution='provided'):
        evidence = dict(path=self.evidence, sha256=nc.digest(self.root / self.evidence))
        checks = {name: dict(evidence, summary='技术决定的来源及适用范围')
                  for name in tc.EDITORIAL_CHECKS['direction']}
        for category, topics in tc.DIRECTION_DECISIONS.items():
            checks[category]['decisions'] = {topic: self.decision(resolution) for topic in topics}
        checks['startup_mode']['decision'] = self.decision(resolution)
        checks['author_preferences']['decisions']['title'] = dict(
            self.decision(resolution), value=nc.load_state(self.root)['title'], prior_status='proposed')
        return checks

    def decision(self, resolution='provided'):
        decision = dict(resolution=resolution, value='技术选择', question_quote='合成提问：本轮技术约定如何选择？',
                        source_quote='合成作者原话：本项照此处理。', scope='本次技术流程')
        if resolution == 'delegated':
            decision['opt_out'] = dict(question_quote='合成提问：是否所有本轮推荐均采用且不再逐项问？',
                                       answer_quote='合成答复：全部照推荐走，以后不必再问。', scope='本次技术流程',
                                       all_recommendations=True, no_further_questions=True)
        return decision

    def request(self, checks=None):
        return dict(stage='S01', task='direction',
                    outputs=[dict(path=self.evidence, sha256=nc.digest(self.root / self.evidence))],
                    checks=self.checks() if checks is None else checks)

    def accept(self, checks=None):
        result = tc.update(self.root, dict(self.request(checks), status='done',
                                          verification=dict(agent_id='fixture-main', source='合成来源已核对')))
        if tc.load_plan(self.root)['stages'][0].get('decision_gate_version') == 1:
            evidence = dict(path=self.evidence, sha256=nc.digest(self.root / self.evidence))
            tc.update(self.root, dict(stage='S01', task='concept', status='todo', inputs=[evidence],
                                     checks=dict(creative_decisions=dict(evidence, summary='合成构思前选择核对',
                                                                         items=[], none_reason='本轮只核查已选技术方向，无新增选择'))))
        return result

    def legacy_stage(self, version=None):
        data = tc.load_plan(self.root)
        stage = data['stages'][0]
        stage.pop('direction_gate_version', None)
        stage.pop('decision_gate_version', None)
        stage.pop('execution_gate_version', None)
        stage.pop('result_gate_version', None)
        if version is None:
            stage.pop('workflow_version', None)
        else:
            stage['workflow_version'] = version
        tc.save(self.root, data)

    def test_only_new_v2_planning_enables_direction_gate(self):
        self.assertEqual(tc.load_plan(self.root)['stages'][0]['direction_gate_version'], 1)
        self.assertNotIn('direction_gate_version', tc.new_stage('S2', '旧阶段', 'planning', workflow_version=1))
        for workflow in ('chapter', 'trial', 'finalization'):
            self.assertNotIn('direction_gate_version', tc.new_stage('S2', '其他阶段', workflow, chapter=1))

    def test_summary_only_does_not_prove_author_readiness(self):
        checks = self.checks()
        for item in checks.values():
            item.pop('decisions', None)
            item.pop('decision', None)
            item['summary'] = '作者没选，代理先决定，等作者改。'
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'missing author decision'):
            tc.submit(self.root, self.request(checks))
        with self.assertRaisesRegex(ValueError, 'missing author decision'):
            self.accept(checks)
        self.assertEqual(before, nc.inventory(self.root))

    def test_partial_preferences_preserved_but_unresolved_startup_blocks(self):
        checks = self.checks()
        checks['author_preferences']['decisions']['style']['value'] = '技术语言密度已提供'
        checks['author_preferences']['decisions']['length']['value'] = '技术长度已提供'
        checks['startup_mode']['decision'] = self.decision('unresolved')
        with self.assertRaisesRegex(ValueError, 'author decision unresolved: startup_mode'):
            tc.submit(self.root, self.request(checks))
        checks['startup_mode']['decision'] = self.decision('selected')
        self.accept(checks)
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])
        actual = tc.load_plan(self.root)['stages'][0]['tasks'][0]['checks']
        self.assertEqual(actual['author_preferences']['decisions']['style']['value'], '技术语言密度已提供')

    def test_provided_and_selected_decisions_need_no_separate_question_flag(self):
        checks = self.checks()
        checks['author_preferences']['decisions']['style']['resolution'] = 'selected'
        tc.submit(self.root, self.request(checks))
        self.accept(checks)
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])

    def test_explicit_delegation_can_resolve_all_topics_without_specific_style(self):
        checks = self.checks('delegated')
        tc.submit(self.root, self.request(checks))
        self.accept(checks)
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])

    def test_resolved_decision_requires_value_quote_and_scope(self):
        for key in ('value', 'source_quote', 'scope'):
            for resolution in ('provided', 'selected', 'delegated'):
                with self.subTest(key=key, resolution=resolution):
                    checks = self.checks(resolution)
                    checks['authorization']['decisions']['work_scope'][key] = ''
                    with self.assertRaisesRegex(ValueError, 'needs value/source_quote/scope'):
                        tc.submit(self.root, self.request(checks))

    def test_unresolved_cannot_hide_behind_nonempty_values_or_main_verification(self):
        checks = self.checks()
        checks['authorization']['decisions']['collaboration'] = self.decision('unresolved')
        with self.assertRaisesRegex(ValueError, 'unresolved: authorization.collaboration'):
            self.accept(checks)

    def test_legacy_stage_without_marker_keeps_previous_contract(self):
        for version in (None, 2):
            with self.subTest(version=version):
                self.legacy_stage(version)
                checks = self.checks()
                for item in checks.values():
                    item.pop('decisions', None)
                    item.pop('decision', None)
                self.accept(checks)
                self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])
                tc.update(self.root, dict(stage='S01', task='direction', status='todo'))

    def test_legacy_unaccepted_stage_can_explicitly_upgrade_without_changing_workflow(self):
        self.legacy_stage()
        request = self.request()
        for item in request['checks'].values():
            item.pop('decisions', None)
            item.pop('decision', None)
        tc.submit(self.root, request)
        tc.plan(self.root, dict(id='S01', direction_gate_version=1))
        stage = tc.load_plan(self.root)['stages'][0]
        self.assertNotIn('workflow_version', stage)
        self.assertEqual(stage['direction_gate_version'], 1)
        self.assertTrue(stage['tasks'][0]['history'])
        self.assertFalse(tc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'missing author decision'):
            self.accept(request['checks'])
        self.accept()
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])

    def test_accepted_legacy_direction_is_not_retroactively_upgraded(self):
        self.legacy_stage()
        self.accept()
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'accepted direction is historical'):
            tc.plan(self.root, dict(id='S01', direction_gate_version=1))
        self.assertEqual(before, nc.inventory(self.root))

    def test_gate_version_cannot_be_disabled_or_used_on_another_workflow(self):
        for value in (0, None, False, '1', 2):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'cannot be downgraded'):
                    tc.plan(self.root, dict(id='S01', direction_gate_version=value))
        with self.assertRaisesRegex(ValueError, 'outside planning'):
            tc.plan(self.root, dict(id='S02', title='其他阶段', workflow='chapter', chapter=1,
                                   direction_gate_version=1))

    def test_orphan_outline_blocks_execution_and_cannot_advance_direction(self):
        outline = self.root / '大纲/分章大纲.md'
        outline.write_text('技术大纲产物。', encoding='utf-8')
        before = nc.inventory(self.root)
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertTrue(any('大纲/分章大纲.md' in warning for warning in checked['warnings']))
        self.assertTrue(any('大纲/分章大纲.md' in issue for issue in checked['artifact_blockers']))
        self.assertEqual(checked['resume']['task'], 'S01.direction')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S01', 'direction')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S01', 'concept')
        self.assertEqual(before, nc.inventory(self.root))

    def test_author_outline_input_needs_provenance_before_execution(self):
        outline = self.root / '大纲/作者给定大纲.md'
        outline.write_text('作者给定技术资料。', encoding='utf-8')
        before = outline.read_bytes()
        request = self.request()
        request['inputs'] = [dict(path='大纲/作者给定大纲.md', sha256=nc.digest(outline))]
        tc.update(self.root, dict(stage='S01', task='direction', status='todo', inputs=request['inputs']))
        with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
            tc.submit(self.root, request)
        source_path = '创作约定/技术已有输入来源.md'
        quote = '合成来源：作者给定大纲.md 是本轮预先提供的技术输入，仅验证流程，不是小说。'
        (self.root / source_path).write_text(quote, encoding='utf-8')
        tc.recover(self.root, dict(
            artifacts=[dict(request['inputs'][0], classification='author_input',
                            source=dict(path=source_path, sha256=nc.digest(self.root / source_path)),
                            source_quote=quote, scope='本轮技术流程的作者既有输入')],
            verification=dict(agent_id='fixture-main', source='已核对合成来源和技术输入')))
        tc.submit(self.root, request)
        self.assertFalse(any('大纲/作者给定大纲.md' in warning for warning in tc.check(self.root)['warnings']))
        self.assertEqual(outline.read_bytes(), before)

    def test_plan_cli_can_upgrade_unaccepted_legacy_planning(self):
        self.legacy_stage()
        proc = subprocess.run([sys.executable, str(Path(tc.__file__)), 'plan', '--book', str(self.root),
                               '--request', json.dumps(dict(id='S01', direction_gate_version=1))],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(tc.load_plan(self.root)['stages'][0]['direction_gate_version'], 1)


if __name__ == '__main__':
    unittest.main()
