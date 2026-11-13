"""Execution/recovery mechanics with synthetic files, never novel generation."""
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


class TaskEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = nc.init_book(Path(self.temp.name), '执行门槛技术夹具', task_management=False)
        tc.initialize(self.root, workflow_version=1)
        tc.plan(self.root, dict(id='S01', execution_gate_version=1))
        self.source = '创作约定/固定恢复来源.md'
        self.quote = '合成来源：此文件用于来源、封存或恢复登记的技术检查，不授予继续创作。'
        self.write(self.source, self.quote)
        self.basis = '创作约定/固定已有依据.md'
        self.write(self.basis, '技术夹具：已有真实前置资料的占位符，不是小说。')
        self.draft = '草稿与备选/第01章-草稿-v1.md'
        self.verification = dict(agent_id='fixture-main', source='合成来源和当前文件已人工核对的技术声明')

    def write(self, path, content):
        (self.root / path).write_text(content, encoding='utf-8')

    def ev(self, path):
        return dict(path=path, sha256=nc.digest(self.root / path))

    def data(self):
        return tc.load_plan(self.root)

    def recovery(self, classification='author_input', path=None, **fields):
        item = dict(self.ev(path or self.draft), classification=classification,
                    source=self.ev(self.source), source_quote=self.quote, scope='本轮技术检查中的文件处置')
        item.update(fields)
        return dict(artifacts=[item], verification=self.verification)

    def done(self, kind, stage='S01', recovery_only=False, outputs=None):
        outputs = [self.ev(self.basis)] if outputs is None else outputs
        if recovery_only:
            tc.update(self.root, dict(stage=stage, task=kind, status='todo', inputs=[self.ev(self.basis)]))
        request = dict(stage=stage, task=kind, status='done', outputs=outputs, verification=self.verification)
        if recovery_only:
            request.update(recovery_only=True, reason='只核对现存来源并修复账本，不写作')
        return tc.update(self.root, request)

    def test_new_v2_marker_and_explicit_active_legacy_upgrade_preserve_history(self):
        for workflow in tc.WORKFLOWS:
            self.assertEqual(tc.new_stage('S', '技术阶段', workflow, chapter=1)['execution_gate_version'], 1)
        stage = self.data()['stages'][0]
        self.assertEqual(stage['workflow_version'], 1)
        self.assertEqual(stage['execution_gate_version'], 1)
        self.assertTrue(all(task['status'] == 'todo' for task in stage['tasks']))
        for value in (False, 0, None, '1', 2):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    tc.plan(self.root, dict(id='S01', execution_gate_version=value))

    def test_unregistered_output_blocks_check_all_execution_and_is_read_only(self):
        self.write(self.draft, '合成预存产物。')
        before = nc.inventory(self.root)
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertTrue(any(self.draft in issue for issue in checked['artifact_blockers']))
        for action in (lambda: tc.gate(self.root, 'S01', 'direction'),
                       lambda: tc.update(self.root, dict(stage='S01', task='direction', status='doing')),
                       lambda: self.done('direction')):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(nc.inventory(self.root), before)
        self.assertEqual(tc.gate(self.root, 'S01', 'direction', neutral=True)['error'], 'gate_not_ready')

    def test_no_chapter_filename_is_needed_to_detect_draft(self):
        self.write('草稿与备选/旧稿.md', '合成预存产物。')
        self.assertFalse(tc.check(self.root)['ok'])

    def test_unregistered_review_and_chinese_chapter_names_cannot_hide(self):
        self.write('审稿与修订/首读报告.md', '合成预存报告。')
        self.write('草稿与备选/第一章.md', '合成预存产物。')
        issues = tc.check(self.root)['artifact_blockers']
        self.assertTrue(any('首读报告.md' in issue for issue in issues))
        self.assertTrue(any('第一章.md' in issue for issue in issues))

    def test_todo_blocked_and_doing_cannot_preregister_new_outputs(self):
        self.write(self.draft, '合成预存产物。')
        for status in ('todo', 'blocked', 'doing'):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, 'preregister outputs'):
                    tc.update(self.root, dict(stage='S01', task='direction', status=status, outputs=[self.ev(self.draft)]))

    def test_unknown_input_cannot_launder_old_output_through_recovery_update(self):
        self.write(self.draft, '合成预存产物。')
        tc.update(self.root, dict(stage='S01', task='direction', status='todo', inputs=[self.ev(self.draft)]))
        with self.assertRaisesRegex(ValueError, 'use recover first'):
            tc.update(self.root, dict(stage='S01', task='direction', status='done', outputs=[self.ev(self.draft)],
                                      recovery_only=True, reason='合成记录修复', verification=self.verification))
        self.assertFalse(tc.check(self.root)['ok'])

    def test_upgrade_does_not_launder_legacy_todo_outputs_into_started_work(self):
        data = self.data()
        data['stages'][0].pop('execution_gate_version')
        tc.save(self.root, data)
        path = '大纲/合成旧产物.md'
        self.write(path, '合成预存产物。')
        tc.update(self.root, dict(stage='S01', task='direction', status='todo', outputs=[self.ev(path)]))
        tc.plan(self.root, dict(id='S01', execution_gate_version=1))
        for status in ('doing', 'submitted', 'done'):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, 'provenance recovery'):
                    tc.update(self.root, dict(stage='S01', task='direction', status=status,
                                              verification=self.verification))

    def test_author_input_recovery_preserves_bytes_and_does_not_accept_tasks(self):
        self.write(self.draft, '作者既有输入的技术占位符。')
        before_bytes = (self.root / self.draft).read_bytes()
        before_tasks = copy.deepcopy(self.data()['stages'])
        tc.recover(self.root, self.recovery())
        self.assertEqual(self.data()['stages'], before_tasks)
        self.assertEqual((self.root / self.draft).read_bytes(), before_bytes)
        self.assertTrue(tc.gate(self.root, 'S01', 'direction')['ok'])
        with self.assertRaisesRegex(ValueError, 'not accepted'):
            tc.gate(self.root, 'S01', 'concept')

    def test_recovery_requires_current_separate_source_real_quote_and_verification(self):
        self.write(self.draft, '合成预存产物。')
        variants = []
        for field, value in [('source_quote', '来源中不存在的合成原话'), ('source', self.ev(self.draft)),
                             ('source', dict(path=self.source, sha256='0' * 64))]:
            request = self.recovery()
            request['artifacts'][0][field] = value
            variants.append(request)
        request = self.recovery()
        request['verification'] = None
        variants.append(request)
        before = nc.inventory(self.root)
        for request in variants:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    tc.recover(self.root, request)
                self.assertEqual(before, nc.inventory(self.root))

    def test_changed_recovered_file_or_provenance_blocks_again(self):
        self.write(self.draft, '合成预存产物。')
        tc.recover(self.root, self.recovery())
        self.write(self.source, self.quote + '\n合成变更。')
        self.assertFalse(tc.check(self.root)['ok'])
        tc.recover(self.root, self.recovery())
        self.write(self.draft, '合成变更后的预存产物。')
        self.assertFalse(tc.check(self.root)['ok'])

    def test_excluded_file_stays_preserved_and_cannot_be_reused_as_task_evidence(self):
        self.write(self.draft, '合成封存产物。')
        tc.recover(self.root, self.recovery('excluded'))
        self.assertTrue(tc.gate(self.root, 'S01', 'direction')['ok'])
        tc.update(self.root, dict(stage='S01', task='direction', status='todo', inputs=[self.ev(self.draft)]))
        with self.assertRaisesRegex(ValueError, 'excluded artifact'):
            tc.gate(self.root, 'S01', 'direction')
        self.assertTrue((self.root / self.draft).exists())

    def test_held_output_requires_ordered_repair_and_never_grants_doing(self):
        tc.plan(self.root, dict(id='S02', title='合成章任务', workflow='chapter', chapter=1, workflow_version=1,
                                execution_gate_version=1, compatibility_reason='既有技术阶段'))
        self.write(self.draft, '合成预存产物。')
        tc.recover(self.root, self.recovery('held_output', task_id='S02.draft'))
        for kind in ('direction', 'concept'):
            with self.assertRaises(ValueError):
                tc.gate(self.root, 'S01', kind)
        with self.assertRaisesRegex(ValueError, 'never authorizes'):
            tc.update(self.root, dict(stage='S01', task='direction', status='doing', recovery_only=True,
                                      reason='机械恢复', verification=self.verification))
        self.done('direction', recovery_only=True)
        self.done('concept', recovery_only=True)
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.done('next_plan', recovery_only=True, outputs=[])
        tc.close_stage(self.root, 'S01')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'prepare')
        self.done('prepare', 'S02', recovery_only=True)
        self.done('draft', 'S02', recovery_only=True, outputs=[self.ev(self.draft)])
        self.assertTrue(tc.gate(self.root, 'S02', 'first_read')['ok'])
        self.assertEqual((self.root / self.draft).read_text(encoding='utf-8'), '合成预存产物。')

    def test_recovery_mode_still_checks_prerequisites_and_requires_fresh_verification(self):
        self.write(self.draft, '合成预存产物。')
        tc.recover(self.root, self.recovery('held_output', task_id='S01.concept'))
        with self.assertRaisesRegex(ValueError, 'prerequisites'):
            self.done('concept', recovery_only=True, outputs=[self.ev(self.draft)])
        with self.assertRaisesRegex(ValueError, 'fresh host verification'):
            tc.update(self.root, dict(stage='S01', task='direction', status='submitted', recovery_only=True,
                                      reason='合成修复', outputs=[self.ev(self.basis)]))

    def test_recovered_chapter_draft_cannot_be_accepted_as_planning_output(self):
        self.write(self.draft, '合成作者输入。')
        tc.recover(self.root, self.recovery())
        with self.assertRaisesRegex(ValueError, 'out-of-order output'):
            self.done('direction', outputs=[self.ev(self.draft)])

    def test_doing_checks_new_preflight_after_applying_request_fields(self):
        data = self.data()
        stage = data['stages'][0]
        stage.update(workflow_version=2, direction_gate_version=1, decision_gate_version=1)
        tc.save(self.root, data)
        evidence = self.ev(self.basis)
        decision = dict(resolution='provided', value='技术约定', source_quote='合成作者已主动给定', scope='技术夹具')
        checks = {name: dict(evidence, summary='合成明确作者决定') for name in tc.EDITORIAL_CHECKS['direction']}
        for category, topics in tc.DIRECTION_DECISIONS.items():
            checks[category]['decisions'] = {topic: dict(decision) for topic in topics}
        checks['startup_mode']['decision'] = dict(decision)
        checks['author_preferences']['decisions']['title'] = dict(decision, value=self.root.name, prior_status='proposed')
        tc.update(self.root, dict(stage='S01', task='direction', status='done', outputs=[evidence],
                                  checks=checks, verification=self.verification))
        with self.assertRaisesRegex(ValueError, 'creative_decisions'):
            tc.update(self.root, dict(stage='S01', task='concept', status='doing'))
        preflight = dict(evidence, summary='合成无新增决定', items=[], none_reason='只验证既有技术约定')
        tc.update(self.root, dict(stage='S01', task='concept', status='doing', inputs=[evidence],
                                  checks=dict(creative_decisions=preflight)))
        self.assertEqual(self.data()['stages'][0]['tasks'][1]['status'], 'doing')

    def test_started_task_can_submit_new_outline_then_be_accepted(self):
        self.done('direction')
        tc.update(self.root, dict(stage='S01', task='concept', status='doing'))
        path = '大纲/合成流程产物.md'
        self.write(path, '仅验证先开始后登记产物的技术文本。')
        tc.submit(self.root, dict(stage='S01', task='concept', outputs=[self.ev(path)]))
        self.assertEqual(self.data()['stages'][0]['tasks'][1]['status'], 'submitted')
        self.assertTrue(tc.check(self.root)['ok'])
        self.done('concept', outputs=[self.ev(path)])

    def test_raw_future_cursor_cannot_skip_unclosed_predecessor(self):
        tc.plan(self.root, dict(id='S02', title='合成未来', workflow='chapter', chapter=1, workflow_version=1,
                                compatibility_reason='历史技术阶段'))
        data = self.data()
        data['current_stage'] = 'S02'
        data['current_task'] = 'S02.prepare'
        (self.root / tc.TASK_FILE).write_text('# 技术原始JSON改动\n' + tc.managed(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'earlier stages must close'):
            tc.render(self.root)
        self.assertFalse(tc.check(self.root)['ok'])

    def test_fake_closed_flag_and_task_cursor_cannot_hide_unaccepted_tasks(self):
        tc.plan(self.root, dict(id='S02', title='合成未来', workflow='chapter', chapter=1, workflow_version=1,
                                compatibility_reason='历史技术阶段'))
        original = self.data()
        data = copy.deepcopy(original)
        data['stages'][0]['status'] = 'done'
        data['stages'][0]['tasks'][0]['status'] = 'submitted'
        data['current_stage'], data['current_task'] = 'S02', 'S02.prepare'
        (self.root / tc.TASK_FILE).write_text('# 技术原始JSON改动\n' + tc.managed(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'closed stages cannot contain unaccepted'):
            tc.render(self.root)
        data = copy.deepcopy(original)
        data['current_task'] = 'S01.concept'
        (self.root / tc.TASK_FILE).write_text('# 技术原始JSON改动\n' + tc.managed(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'task cursor must match'):
            tc.render(self.root)

    def test_legacy_unupgraded_stage_remains_advisory(self):
        data = self.data()
        data['stages'][0].pop('execution_gate_version')
        tc.save(self.root, data)
        self.write(self.draft, '历史技术夹具中的预存产物。')
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertTrue(tc.check(self.root)['warnings'])
        self.assertTrue(tc.gate(self.root, 'S01', 'direction')['ok'])

    def test_recover_cli_does_not_accept_or_advance_task(self):
        self.write(self.draft, '合成作者输入。')
        request_path = Path(self.temp.name) / 'synthetic-recovery.json'
        request_path.write_text(json.dumps(self.recovery(), ensure_ascii=False), encoding='utf-8')
        proc = subprocess.run([sys.executable, str(Path(tc.__file__)), 'recover', '--book', str(self.root),
                               '--request', str(request_path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)['ok'])
        self.assertEqual(self.data()['current_task'], 'S01.direction')


if __name__ == '__main__':
    unittest.main()
