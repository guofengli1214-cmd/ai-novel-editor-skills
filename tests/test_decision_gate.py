"""Decisive-choice mechanics with synthetic records, never novel generation."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc


class DecisionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = nc.init_book(Path(self.temp.name), '决定门槛技术夹具')
        self.path = '创作约定/合成作者决定.md'
        (self.root / self.path).write_text('合成问题、答复与决定，只检验流程字段。', encoding='utf-8')

    def evidence(self):
        return dict(path=self.path, sha256=nc.digest(self.root / self.path))

    def choice(self, resolution='provided'):
        item = dict(resolution=resolution, value='合成选择', source_quote='合成作者主动给定具体选择。', scope='本次技术任务')
        if resolution == 'selected':
            item['question_quote'] = '合成提问：选择A还是B？'
        if resolution == 'delegated':
            item['opt_out'] = dict(question_quote='合成提问：本轮后续全部按推荐且不再询问吗？',
                                   answer_quote='合成答复：全部按推荐，不用再问。', scope='本次技术任务',
                                   all_recommendations=True, no_further_questions=True)
        return item

    def title_choice(self, resolution='provided', prior='proposed'):
        return dict(self.choice(resolution), value=self.root.name, prior_status=prior)

    def direction_checks(self):
        evidence = self.evidence()
        checks = {key: dict(evidence, summary='合成作者的实际决定记录') for key in tc.EDITORIAL_CHECKS['direction']}
        for category, topics in tc.DIRECTION_DECISIONS.items():
            checks[category]['decisions'] = {topic: self.choice() for topic in topics}
        checks['author_preferences']['decisions']['title'] = self.title_choice()
        checks['startup_mode']['decision'] = self.choice()
        return checks

    def accept_direction(self, checks=None):
        return tc.update(self.root, dict(stage='S01', task='direction', status='done', outputs=[self.evidence()],
                                         checks=self.direction_checks() if checks is None else checks,
                                         verification=dict(agent_id='fixture-main', source='合成记录已核对')))

    def creative_check(self, items=None):
        result = dict(self.evidence(), summary='合成本轮关键选择', items=[] if items is None else items)
        if not result['items']:
            result['none_reason'] = '本轮只验证已有作者决定，不产生新的选择。'
        return result

    def creative_item(self, resolution='provided'):
        return dict(self.choice(resolution), id='decision-fixture-1', topic='character_direction', record_id='作者决定·合成1')

    def register_concept(self, creative):
        return tc.update(self.root, dict(stage='S01', task='concept', status='todo', inputs=[self.evidence()],
                                         checks=dict(creative_decisions=creative)))

    def test_new_v2_stages_enable_choice_gate_without_rewriting_old_stages(self):
        for workflow in tc.WORKFLOWS:
            with self.subTest(workflow=workflow):
                self.assertEqual(tc.new_stage('S1', '合成阶段', workflow, chapter=1)['decision_gate_version'], 1)
                self.assertNotIn('decision_gate_version', tc.new_stage('S1', '旧阶段', workflow, chapter=1, workflow_version=1))
        data = tc.load_plan(self.root)
        data['stages'][0].pop('decision_gate_version')
        tc.save(self.root, data)
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'historical stages'):
            tc.plan(self.root, dict(id='S01', decision_gate_version=1))
        self.assertEqual(before, nc.inventory(self.root))

    def test_choice_gate_cannot_be_disabled_on_existing_or_new_stages(self):
        for value in (0, None, False, '1', 2):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    tc.plan(self.root, dict(id='S01', decision_gate_version=value))
                with self.assertRaises(ValueError):
                    tc.plan(self.root, dict(id='S02', title='合成后续', workflow='chapter', chapter=1,
                                           decision_gate_version=value))

    def test_direction_requires_title_matching_state_and_prior_status(self):
        checks = self.direction_checks()
        del checks['author_preferences']['decisions']['title']
        with self.assertRaisesRegex(ValueError, 'missing author decision'):
            self.accept_direction(checks)
        checks = self.direction_checks()
        checks['author_preferences']['decisions']['title']['value'] = '另一个技术名'
        with self.assertRaisesRegex(ValueError, 'differs from current book state'):
            self.accept_direction(checks)
        checks = self.direction_checks()
        del checks['author_preferences']['decisions']['title']['prior_status']
        with self.assertRaisesRegex(ValueError, 'prior_status'):
            self.accept_direction(checks)

    def test_provided_specific_choice_does_not_require_question_or_opt_out(self):
        self.assertEqual(tc.decision_errors(self.choice(), '合成项目', strict=True), [])
        self.accept_direction()

    def test_selected_needs_actual_question_and_unresolved_is_not_approved(self):
        choice = self.choice('selected')
        del choice['question_quote']
        self.assertTrue(any('question_quote' in error for error in tc.decision_errors(choice, '合成项目', strict=True)))
        self.assertTrue(any('unresolved' in error for error in tc.decision_errors(self.choice('unresolved'), '合成项目', strict=True)))

    def test_generic_delegation_does_not_establish_no_question_exception(self):
        choice = dict(self.choice(), resolution='delegated', source_quote='你决定。')
        self.assertTrue(tc.decision_errors(choice, '合成项目', strict=True))
        for field in ('all_recommendations', 'no_further_questions'):
            for value in (False, 1, 'true', None):
                with self.subTest(field=field, value=value):
                    choice = self.choice('delegated')
                    choice['opt_out'][field] = value
                    self.assertTrue(tc.decision_errors(choice, '合成项目', strict=True))
        for field in ('question_quote', 'answer_quote', 'scope'):
            choice = self.choice('delegated')
            del choice['opt_out'][field]
            self.assertTrue(tc.decision_errors(choice, '合成项目', strict=True))
        self.assertEqual(tc.decision_errors(self.choice('delegated'), '合成项目', strict=True), [])

    def test_retired_title_needs_named_reuse_even_under_valid_opt_out(self):
        for prior in ('retired', 'rejected'):
            for resolution in ('provided', 'selected', 'delegated'):
                with self.subTest(prior=prior, resolution=resolution):
                    choice = self.title_choice(resolution, prior)
                    self.assertTrue(tc.title_decision_errors(self.root.name, choice))
                    choice['history_source'] = '作者决定·原弃用记录'
                    choice['reuse_confirmation'] = dict(question_quote='恢复这个旧名吗？',
                                                         answer_quote=self.root.name + '恢复使用。',
                                                         acknowledges_retired=True)
                    self.assertTrue(tc.title_decision_errors(self.root.name, choice))
                    choice['reuse_confirmation']['question_quote'] = '此前弃用的《' + self.root.name + '》现在恢复吗？'
                    if resolution == 'delegated':
                        choice['opt_out']['question_quote'] = choice['reuse_confirmation']['question_quote']
                        choice['reuse_confirmation']['answer_quote'] = choice['opt_out']['answer_quote']
                    if resolution == 'provided':
                        choice['source_quote'] = '作者主动恢复使用《' + self.root.name + '》。'
                    self.assertEqual(tc.title_decision_errors(self.root.name, choice), [])

    def test_retired_title_answer_must_name_it_or_match_real_opt_out(self):
        choice = self.title_choice('provided', 'retired')
        choice.update(history_source='合成弃用记录', reuse_confirmation=dict(
            question_quote='已弃用《' + self.root.name + '》要恢复吗？', answer_quote='好。', acknowledges_retired=True))
        self.assertTrue(tc.title_decision_errors(self.root.name, choice))
        choice['resolution'] = 'delegated'
        choice['opt_out'] = self.choice('delegated')['opt_out']
        self.assertTrue(tc.title_decision_errors(self.root.name, choice))
        choice['reuse_confirmation']['answer_quote'] = choice['opt_out']['answer_quote']
        self.assertTrue(any('opt-out question' in error for error in tc.title_decision_errors(self.root.name, choice)))
        choice['opt_out']['question_quote'] = '本轮推荐恢复此前弃用的《' + self.root.name + '》，是否全部按推荐且不再询问？'
        self.assertEqual(tc.title_decision_errors(self.root.name, choice), [])

    def test_author_can_proactively_restore_named_title_without_new_question(self):
        choice = self.title_choice('provided', 'retired')
        quote = '我明确恢复使用《' + self.root.name + '》。'
        choice.update(history_source='合成历史记录：作者此前弃用此名。', source_quote=quote,
                      reuse_confirmation=dict(answer_quote=quote, acknowledges_retired=True))
        self.assertNotIn('question_quote', choice)
        self.assertNotIn('question_quote', choice['reuse_confirmation'])
        self.assertNotIn('opt_out', choice)
        self.assertEqual(tc.title_decision_errors(self.root.name, choice), [])
        choice['source_quote'] = '恢复这个旧名。'
        self.assertTrue(any('source must explicitly name' in error for error in tc.title_decision_errors(self.root.name, choice)))

    def test_selected_retired_title_preserves_real_option_answer_and_mapping(self):
        choice = self.title_choice('selected', 'retired')
        question = '合成实际提问：A=恢复此前弃用的《' + self.root.name + '》；B=另取名。请选择。'
        choice.update(history_source='合成弃用记录', question_quote=question, source_quote='A',
                      reuse_confirmation=dict(question_quote=question, answer_quote='A',
                                              selected_title=self.root.name, acknowledges_retired=True))
        self.assertEqual(tc.title_decision_errors(self.root.name, choice), [])
        for selected_title in (None, '另一个技术名'):
            with self.subTest(selected_title=selected_title):
                invalid = copy.deepcopy(choice)
                invalid['reuse_confirmation']['selected_title'] = selected_title
                self.assertTrue(tc.title_decision_errors(self.root.name, invalid))
        choice['source_quote'] = '作者选择了原书名。'
        self.assertTrue(tc.title_decision_errors(self.root.name, choice))

    def test_concept_gate_blocks_before_creation_until_choices_registered(self):
        self.accept_direction()
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'creative_decisions'):
            tc.gate(self.root, 'S01', 'concept')
        self.assertEqual(before, nc.inventory(self.root))
        self.register_concept(self.creative_check())
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])
        self.assertFalse((self.root / '大纲/分章大纲.md').exists())

    def test_registered_unresolved_choice_still_blocks_preflight_and_submission(self):
        self.accept_direction()
        check = self.creative_check([self.creative_item('unresolved')])
        self.register_concept(check)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            tc.gate(self.root, 'S01', 'concept')
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            tc.submit(self.root, dict(stage='S01', task='concept', outputs=[self.evidence()],
                                     checks=dict(story_engine=dict(self.evidence(), summary='合成构思核查'), creative_decisions=check)))

    def test_choice_preflight_requires_current_registered_evidence(self):
        self.accept_direction()
        check = self.creative_check()
        tc.update(self.root, dict(stage='S01', task='concept', status='todo', checks=dict(creative_decisions=check)))
        with self.assertRaisesRegex(ValueError, 'registered task evidence'):
            tc.gate(self.root, 'S01', 'concept')
        self.register_concept(check)
        # Preserve prior direction evidence while testing only this task's stale
        # choice pointer with a second independently registered source document.
        other = '创作约定/后续合成决定.md'
        (self.root / other).write_text('后续合成决定。', encoding='utf-8')
        check.update(path=other, sha256=nc.digest(self.root / other))
        tc.update(self.root, dict(stage='S01', task='concept', status='todo',
                                 inputs=[dict(path=other, sha256=check['sha256'])], checks=dict(creative_decisions=check)))
        (self.root / other).write_text('已改变的合成决定。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale creative decision'):
            tc.gate(self.root, 'S01', 'concept')

    def test_all_selected_task_kinds_apply_same_decision_checks(self):
        for kind in tc.CREATIVE_DECISION_TASKS:
            with self.subTest(kind=kind):
                stage = tc.new_stage('S2', '合成阶段', 'chapter', chapter=1)
                task = dict(kind=kind, checks={}, inputs=[self.evidence()], outputs=[])
                self.assertTrue(tc.decision_preflight_errors(self.root, stage, task))
                task['checks']['creative_decisions'] = self.creative_check()
                self.assertEqual(tc.decision_preflight_errors(self.root, stage, task), [])
                del task['checks']['creative_decisions']['none_reason']
                self.assertTrue(tc.decision_preflight_errors(self.root, stage, task))
                task['checks']['creative_decisions'] = self.creative_check([self.creative_item('selected')])
                self.assertEqual(tc.decision_preflight_errors(self.root, stage, task), [])

    def test_missing_records_duplicate_ids_and_generic_delegation_are_rejected(self):
        stage = tc.new_stage('S2', '合成阶段', 'chapter', chapter=1)
        for items in ([self.creative_item('delegated')], [self.creative_item(), self.creative_item()]):
            items = copy.deepcopy(items)
            if len(items) == 1:
                del items[0]['opt_out']
            task = dict(kind='prepare', checks=dict(creative_decisions=self.creative_check(items)),
                        inputs=[self.evidence()], outputs=[])
            self.assertTrue(tc.decision_preflight_errors(self.root, stage, task))

    def test_historical_direction_and_stage_are_not_forced_to_invent_new_approval(self):
        data = tc.load_plan(self.root)
        stage = data['stages'][0]
        stage.pop('decision_gate_version')
        tc.save(self.root, data)
        checks = self.direction_checks()
        checks['author_preferences']['decisions'].pop('title')
        # Pre-decision-gate history retains the previous delegation contract.
        checks['startup_mode']['decision'] = dict(self.choice(), resolution='delegated')
        self.accept_direction(checks)
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])

    def test_neutral_gate_does_not_disclose_unselected_creative_choice(self):
        self.accept_direction()
        self.register_concept(self.creative_check([dict(self.creative_item('unresolved'), value='私密技术选择')]))
        self.assertEqual(tc.gate(self.root, 'S01', 'concept', neutral=True),
                         dict(ok=False, error='gate_not_ready', stage='S01', before='concept'))


if __name__ == '__main__':
    unittest.main()
