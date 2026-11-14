"""Optional hidden-plot gates with synthetic choices, never fiction or consent."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_author_intake as fixtures


tc, nc = fixtures.tc, fixtures.nc


class HiddenPlotGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AuthorIntakeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def checks(self, value='enabled', resolution='selected'):
        checks = self.fixture.checks()
        decision = self.fixture.decision(resolution)
        decision.update(value=value, question_quote='合成提问：本技术项目需要暗线剧情吗？',
                        source_quote='合成答复：' + ('需要。' if value == 'enabled' else '不需要。'))
        checks['author_preferences']['decisions']['hidden_plot'] = decision
        return checks

    def without_marker(self):
        data = tc.load_plan(self.root)
        data['stages'][0].pop('hidden_plot_gate_version')
        tc.save(self.root, data)

    def test_only_new_v2_planning_requires_hidden_plot_choice(self):
        self.assertEqual(tc.load_plan(self.root)['stages'][0]['hidden_plot_gate_version'], 1)
        for workflow in tc.WORKFLOWS:
            stage = tc.new_stage('T1', '合成阶段', workflow, chapter=1)
            self.assertEqual('hidden_plot_gate_version' in stage, workflow == 'planning')
        stage = tc.new_stage('T1', '合成旧阶段', 'planning', workflow_version=1)
        self.assertNotIn('hidden_plot_gate_version', stage)

    def test_missing_unresolved_or_invalid_choice_blocks_without_writing(self):
        cases = [None, dict(self.fixture.decision('unresolved'), value='enabled')]
        cases.extend(dict(self.fixture.decision(), value=value)
                     for value in ('', 'later', '技术选择', True, False, None, [], {}))
        for decision in cases:
            with self.subTest(decision=decision):
                checks = self.checks()
                preferences = checks['author_preferences']['decisions']
                if decision is None:
                    preferences.pop('hidden_plot')
                else:
                    preferences['hidden_plot'] = decision
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'hidden_plot'):
                    tc.submit(self.root, self.fixture.request(checks))
                with self.assertRaisesRegex(ValueError, 'hidden_plot'):
                    self.fixture.accept(checks)
                self.assertEqual(nc.inventory(self.root), before)

    def test_both_explicit_choices_accept_without_creating_management_content(self):
        for value in ('enabled', 'disabled'):
            with self.subTest(value=value):
                self.fixture.accept(self.checks(value))
                self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])
                self.assertFalse((self.root / nc.HIDDEN_PLOT_FILE).exists())
                tasks = tc.load_plan(self.root)['stages'][0]['tasks']
                self.assertFalse(any(task['kind'] == 'hidden_plot_management' for task in tasks))
                tc.update(self.root, dict(stage='S01', task='direction', status='todo'))

    def test_author_supplied_choice_needs_no_repeated_question(self):
        checks = self.checks(resolution='provided')
        checks['author_preferences']['decisions']['hidden_plot'].pop('question_quote')
        self.fixture.accept(checks)
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])

    def test_asked_choice_needs_question_and_delegation_needs_real_opt_out(self):
        for resolution, field in (('selected', 'question_quote'), ('delegated', 'opt_out')):
            checks = self.checks(resolution=resolution)
            checks['author_preferences']['decisions']['hidden_plot'].pop(field)
            before = nc.inventory(self.root)
            with self.assertRaises(ValueError):
                self.fixture.accept(checks)
            self.assertEqual(nc.inventory(self.root), before)
        self.fixture.accept(self.checks(resolution='delegated'))

    def test_required_source_and_scope_cannot_be_replaced_by_verification(self):
        for field in ('source_quote', 'scope'):
            checks = self.checks()
            checks['author_preferences']['decisions']['hidden_plot'][field] = ''
            with self.assertRaisesRegex(ValueError, 'value/source_quote/scope'):
                self.fixture.accept(checks)

    def test_historical_direction_is_not_retroactively_invalidated(self):
        self.without_marker()
        checks = self.checks()
        checks['author_preferences']['decisions'].pop('hidden_plot')
        self.fixture.accept(checks)
        before = nc.inventory(self.root)
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])
        with self.assertRaisesRegex(ValueError, 'accepted direction is historical'):
            tc.plan(self.root, dict(id='S01', hidden_plot_gate_version=1))
        self.assertEqual(nc.inventory(self.root), before)

    def test_unaccepted_current_direction_can_explicitly_upgrade_and_cli_cannot_disable(self):
        self.without_marker()
        tc.plan(self.root, dict(id='S01', hidden_plot_gate_version=1))
        stage = tc.load_plan(self.root)['stages'][0]
        self.assertTrue(stage['tasks'][0]['history'])
        checks = self.checks()
        checks['author_preferences']['decisions'].pop('hidden_plot')
        with self.assertRaisesRegex(ValueError, 'hidden_plot'):
            self.fixture.accept(checks)
        self.fixture.accept(self.checks())
        for value in (None, False, 0, 2, '1'):
            before = nc.inventory(self.root)
            request = dict(id='S01', hidden_plot_gate_version=value)
            proc = subprocess.run([sys.executable, str(Path(tc.__file__)), 'plan', '--book', str(self.root),
                                   '--request', json.dumps(request)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(nc.inventory(self.root), before)

    def test_marker_is_rejected_outside_planning_and_on_legacy_workflows(self):
        for workflow in tc.WORKFLOWS.keys() - {'planning'}:
            before = nc.inventory(self.root)
            with self.assertRaisesRegex(ValueError, 'v2 planning'):
                tc.plan(self.root, dict(id='S02', title='合成阶段', workflow=workflow,
                                       chapter=1, hidden_plot_gate_version=1))
            self.assertEqual(nc.inventory(self.root), before)
        self.without_marker()
        data = tc.load_plan(self.root)
        data['stages'][0]['workflow_version'] = 1
        data['stages'][0].pop('decision_gate_version')
        data['stages'][0].pop('result_gate_version')
        tc.save(self.root, data)
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'v2 planning'):
            tc.plan(self.root, dict(id='S01', hidden_plot_gate_version=1))
        self.assertEqual(nc.inventory(self.root), before)

    def test_standalone_scopes_do_not_require_hidden_plot_and_reject_creative_specialists(self):
        for workflow in tc.STANDALONE_WORKFLOWS:
            stage = tc.new_stage('S01', '合成独立阶段', workflow)
            extra = copy.deepcopy(stage['tasks'][1])
            extra.update(id='S01.hidden', kind='hidden_plot_management',
                         depends_on=['S01.scope'])
            stage['tasks'].insert(1, extra)
            data = dict(schema_version=1, current_stage='S01', current_task='S01.scope', stages=[stage])
            with self.assertRaisesRegex(ValueError, 'standalone workflow cannot include'):
                tc.structure(self.root, data)


if __name__ == '__main__':
    unittest.main()
