"""Task-ledger mechanics with explicit fixtures, not agent/literary proof."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = nc.init_book(Path(self.temp.name), '任务夹具', task_management=False)
        tc.initialize(self.root, workflow_version=1)
        self.write('审稿与修订/验收依据.md', '测试夹具，不是真实子 agent 或文学质量证据。')

    def write(self, path, text):
        (self.root / path).write_text(text, encoding='utf-8')

    def evidence(self, path='审稿与修订/验收依据.md'):
        return dict(path=path, sha256=nc.digest(self.root / path))

    def done(self, kind, stage='S01', **fields):
        request = dict(stage=stage, task=kind, status='done',
                       outputs=[self.evidence()],
                       verification=dict(agent_id='fixture-main', source='explicit fixture acceptance'))
        request.update(fields)
        return tc.update(self.root, request)

    def data(self):
        return tc.load_plan(self.root)

    def start_v2_prepare(self, stage='S02'):
        evidence = self.evidence()
        tc.update(self.root, dict(stage=stage, task='prepare', status='doing', inputs=[evidence],
                                 checks=dict(creative_decisions=dict(evidence, summary='合成已选技术决定',
                                                                     items=[], none_reason='只验证既有技术选择'))))

    def raw_data(self, data, render=True):
        if render:
            self.write(tc.TASK_FILE, '# 测试\n' + tc.managed(data) + '\n')
        else:
            _, text, start, end = tc.extract(self.root)
            old_json = json.dumps(json.loads(text[text.index('```json') + 7:text.index('\n```', text.index('```json'))]), ensure_ascii=False, indent=2)
            self.write(tc.TASK_FILE, text.replace(old_json, json.dumps(data, ensure_ascii=False, indent=2), 1))

    def chapter_stage(self, workflow='chapter'):
        tc.plan(self.root, dict(id='S02', title='首章', workflow=workflow, chapter=1, workflow_version=1,
                                compatibility_reason='原v1机械账本回归夹具'))
        self.done('direction')
        self.done('concept')
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.done('next_plan', outputs=[])
        tc.close_stage(self.root, 'S01')

    def test_one_authoritative_file_init_and_readonly_checks(self):
        before = nc.inventory(self.root)
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(self.data()['current_stage'], 'S01')
        self.assertEqual(before, nc.inventory(self.root))
        with self.assertRaises(ValueError):
            tc.initialize(self.root)
        self.assertEqual(before, nc.inventory(self.root))
        text = (self.root / tc.TASK_FILE).read_text(encoding='utf-8')
        self.assertEqual(text.count('```json'), 1)
        self.assertLess(text.index('- [ ]'), text.index('```json'))

    def test_core_tasks_predecessors_and_cycles_cannot_be_removed(self):
        original = self.data()
        variants = []
        data = copy.deepcopy(original)
        data['stages'][0]['tasks'].pop(1)
        variants.append(data)
        data = copy.deepcopy(original)
        data['stages'][0]['tasks'][1]['depends_on'] = []
        variants.append(data)
        data = copy.deepcopy(original)
        data['stages'][0]['tasks'][0]['depends_on'] = ['S01.next_plan']
        variants.append(data)
        data = copy.deepcopy(original)
        data['stages'][0]['tasks'].insert(1, copy.deepcopy(data['stages'][0]['tasks'][0]))
        variants.append(data)
        for data in variants:
            with self.subTest(data=data):
                self.raw_data(data)
                self.assertFalse(tc.check(self.root)['ok'])
        self.raw_data(original)

    def test_cannot_start_out_of_order_or_execute_future_stage(self):
        for status in ('doing', 'submitted', 'done'):
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    tc.update(self.root, dict(stage='S01', task='concept', status=status))
        tc.plan(self.root, dict(id='S02', title='下一阶段', workflow='chapter', chapter=1))
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S02', task='prepare', status='doing'))
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'prepare')

    def test_cross_stage_dependencies_cannot_deadlock_serial_stages(self):
        tc.plan(self.root, dict(id='S02', title='下一章', workflow='chapter', chapter=1))
        data = self.data()
        data['stages'][0]['tasks'][0]['depends_on'] = ['S02.prepare']
        self.raw_data(data)
        self.assertFalse(tc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'same stage'):
            tc.render(self.root)

    def test_acceptance_requires_verification_outputs_and_current_hashes(self):
        variants = [dict(verification=None), dict(outputs=[]),
                    dict(outputs=[dict(path='审稿与修订/验收依据.md', sha256='0' * 64)]),
                    dict(inputs=[dict(path='人物/不存在.md', sha256='0' * 64)])]
        for fields in variants:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    self.done('direction', **fields)
        self.assertEqual(self.data()['stages'][0]['tasks'][0]['status'], 'todo')

    def test_changed_inputs_and_outputs_propagate_staleness(self):
        self.write('人物/写前来源.md', '原始来源')
        self.done('direction', inputs=[self.evidence('人物/写前来源.md')])
        self.done('concept')
        self.write('人物/写前来源.md', '手改来源')
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertEqual(set(checked['stale_tasks']), {'S01.direction', 'S01.concept'})
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S01', 'next_plan')
        self.write('人物/写前来源.md', '原始来源')
        self.write('审稿与修订/验收依据.md', '产物手改')
        self.assertFalse(tc.check(self.root)['ok'])

    def test_reopen_preserves_evidence_history_and_reopens_downstream(self):
        self.done('direction')
        self.done('concept')
        tc.plan(self.root, dict(id='S01', end_reason='夹具授权结束'))
        self.done('next_plan', outputs=[])
        tc.update(self.root, dict(stage='S01', task='direction', status='todo'))
        tasks = self.data()['stages'][0]['tasks']
        self.assertEqual([task['status'] for task in tasks], ['todo', 'todo', 'todo'])
        self.assertTrue(all(task['history'] for task in tasks))
        self.assertTrue(tasks[1]['outputs'])
        self.assertIsNone(tasks[1]['verification'])
        self.assertTrue(any(item['status'] == 'done' for item in tasks[1]['history']))
        self.assertTrue(tc.check(self.root)['ok'])  # Unfinished work is not an error.

    def test_replacing_accepted_evidence_requires_fresh_verification_and_reopens_dependents(self):
        self.done('direction')
        self.done('concept')
        self.write('审稿与修订/新依据.md', '修订后的夹具依据')
        replacement = [self.evidence('审稿与修订/新依据.md')]
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S01', task='direction', status='done', outputs=replacement))
        self.done('direction', outputs=replacement)
        tasks = self.data()['stages'][0]['tasks']
        self.assertEqual(tasks[0]['status'], 'done')
        self.assertEqual(tasks[1]['status'], 'todo')
        self.assertTrue(tasks[1]['history'])
        self.assertIsNone(tasks[1]['verification'])

    def test_changing_successor_reopens_previously_accepted_next_plan(self):
        self.done('direction')
        self.done('concept')
        tc.plan(self.root, dict(id='S01', end_reason='暂时结束'))
        self.done('next_plan', outputs=[])
        tc.plan(self.root, dict(id='S02', title='继续第一章', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        next_plan = self.data()['stages'][0]['tasks'][-1]
        self.assertEqual(next_plan['status'], 'todo')
        self.assertTrue(next_plan['history'])
        with self.assertRaises(ValueError):
            tc.close_stage(self.root, 'S01')

    def test_next_plan_needs_next_stage_or_end_reason_and_close_does_not_execute(self):
        self.done('direction')
        self.done('concept')
        with self.assertRaises(ValueError):
            self.done('next_plan', outputs=[])
        tc.plan(self.root, dict(id='S02', title='下一章', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.done('next_plan', outputs=[])
        tc.close_stage(self.root, 'S01')
        data = self.data()
        self.assertEqual(data['current_stage'], 'S02')
        self.assertEqual(data['stages'][1]['status'], 'planned')
        self.assertTrue(all(task['status'] == 'todo' for task in data['stages'][1]['tasks']))
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S01', task='direction', status='todo'))

    def test_stage_link_cycles_and_completed_predecessors_are_rejected(self):
        tc.plan(self.root, dict(id='S02', title='下一章', workflow='chapter', chapter=1))
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        with self.assertRaises(ValueError):
            tc.plan(self.root, dict(id='S02', next_stage='S01'))
        self.done('direction')
        self.done('concept')
        self.done('next_plan')
        tc.close_stage(self.root, 'S01')
        with self.assertRaises(ValueError):
            tc.plan(self.root, dict(id='S02', next_stage='S01'))

    def test_new_authorization_after_end_can_plan_a_new_current_stage(self):
        self.done('direction')
        self.done('concept')
        tc.plan(self.root, dict(id='S01', end_reason='上次授权结束'))
        self.done('next_plan', outputs=[])
        tc.close_stage(self.root, 'S01')
        tc.plan(self.root, dict(id='S02', title='新授权首章', workflow='chapter', chapter=1))
        self.assertEqual(self.data()['current_stage'], 'S02')
        self.assertEqual(self.data()['stages'][1]['status'], 'planned')
        self.start_v2_prepare()

    def test_new_authorization_can_select_an_existing_planned_stage_after_end(self):
        tc.plan(self.root, dict(id='S02', title='预备首章', workflow='chapter', chapter=1))
        self.done('direction')
        self.done('concept')
        tc.plan(self.root, dict(id='S01', end_reason='本次先结束'))
        self.done('next_plan', outputs=[])
        tc.close_stage(self.root, 'S01')
        tc.plan(self.root, dict(id='S02', title='新授权开始首章'))
        self.assertEqual(self.data()['current_stage'], 'S02')
        self.start_v2_prepare()

    def test_first_read_required_and_trial_roles_are_correct(self):
        self.chapter_stage('trial')
        stage = self.data()['stages'][1]
        self.assertEqual(tc.selected_task(stage, 'voice_trial')['role'], 'writer')
        self.assertEqual(tc.selected_task(stage, 'recheck')['role'], 'reviewer')
        self.done('prepare', 'S02')
        self.done('voice_trial', 'S02')
        self.done('draft', 'S02')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'review')
        self.done('first_read', 'S02')
        self.assertTrue(tc.gate(self.root, 'S02', 'review')['ok'])

    def test_extra_conditional_task_needs_reason_and_verification(self):
        data = self.data()
        task = copy.deepcopy(data['stages'][0]['tasks'][0])
        task.update(id='S01.research', kind='research', depends_on=[], title='条件研究')
        data['stages'][0]['tasks'].insert(-1, task)
        self.raw_data(data, render=False)
        tc.render(self.root)
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S01', task='research', status='not_applicable', verification=dict(agent_id='main', source='fixture')))
        tc.update(self.root, dict(stage='S01', task='research', status='not_applicable', reason='本次不涉及此研究', verification=dict(agent_id='main', source='fixture')))
        self.assertTrue(tc.check(self.root)['ok'])
        text = (self.root / tc.TASK_FILE).read_text(encoding='utf-8')
        self.assertIn('- [ ] S01.research', text)
        self.assertNotIn('- [x] S01.research', text)
        with self.assertRaises(ValueError):
            tc.update(self.root, dict(stage='S01', task='direction', status='not_applicable', reason='不能跳过', verification=dict(agent_id='main', source='fixture')))

    def test_self_hashes_and_checkbox_only_edits_are_rejected(self):
        for path in (tc.TASK_FILE, nc.STATE):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.done('direction', outputs=[self.evidence(path)])
        text = (self.root / tc.TASK_FILE).read_text(encoding='utf-8')
        self.write(tc.TASK_FILE, text.replace('- [ ] S01.direction', '- [x] S01.direction', 1))
        self.assertFalse(tc.check(self.root)['ok'])
        tc.render(self.root)
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(self.data()['stages'][0]['tasks'][0]['status'], 'todo')

    def test_render_preserves_notes_and_does_not_grant_acceptance(self):
        with (self.root / tc.TASK_FILE).open('a', encoding='utf-8') as stream:
            stream.write('作者的额外记录。\n')
        data = self.data()
        data['stages'][0]['tasks'][0]['status'] = 'done'
        self.raw_data(data, render=False)
        tc.render(self.root)
        self.assertIn('作者的额外记录。', (self.root / tc.TASK_FILE).read_text(encoding='utf-8'))
        self.assertFalse(tc.check(self.root)['ok'])  # Rendering cannot manufacture verification.

    def test_json_examples_outside_managed_region_survive_check_render_and_update(self):
        path = self.root / tc.TASK_FILE
        prefix = '# 作者示例\n\n```json\n{"示例": 1}\n```\n\n'
        suffix = '\n## 备忘\n\n```json\n{not valid JSON}\n```\n'
        original = path.read_text(encoding='utf-8')
        self.write(tc.TASK_FILE, prefix + original + suffix)
        before = path.read_bytes()
        before_text = path.read_text(encoding='utf-8')
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(path.read_bytes(), before)

        # Render must still repair only the generated view of authoritative data.
        self.write(tc.TASK_FILE, path.read_text(encoding='utf-8').replace(
            '- [ ] S01.direction', '- [x] S01.direction', 1))
        self.assertFalse(tc.check(self.root)['ok'])
        tc.render(self.root)
        # 换行符按平台归一（Windows 文本模式写入 CRLF），内容需与渲染前一致。
        self.assertEqual(path.read_text(encoding='utf-8'), before_text)

        self.done('direction')
        current = path.read_text(encoding='utf-8')
        self.assertTrue(current.startswith(prefix))
        self.assertTrue(current.endswith(suffix))
        self.assertEqual(self.data()['stages'][0]['tasks'][0]['status'], 'done')
        self.assertTrue(tc.check(self.root)['ok'])

    def test_managed_json_is_required_and_unique_even_with_outside_examples(self):
        data = self.data()
        original = (self.root / tc.TASK_FILE).read_text(encoding='utf-8')
        fenced = '```json\n' + json.dumps(data, ensure_ascii=False, indent=2) + '\n```'
        variants = (
            fenced + '\n' + tc.BEGIN + '\n没有权威数据\n' + tc.END,
            original.replace(tc.END, fenced + '\n' + tc.END),
        )
        for text in variants:
            with self.subTest(text=text):
                self.write(tc.TASK_FILE, text)
                before = nc.inventory(self.root)
                self.assertFalse(tc.check(self.root)['ok'])
                with self.assertRaisesRegex(ValueError, 'one authoritative'):
                    tc.render(self.root)
                with self.assertRaisesRegex(ValueError, 'one authoritative'):
                    self.done('direction')
                self.assertEqual(nc.inventory(self.root), before)

    def test_invalid_managed_markers_fail_without_writing(self):
        original = (self.root / tc.TASK_FILE).read_text(encoding='utf-8')
        variants = (
            original.replace(tc.BEGIN, ''),
            original.replace(tc.END, ''),
            tc.BEGIN + '\n' + original,
            original + '\n' + tc.END,
            tc.END + '\n' + original.replace(tc.BEGIN, '').replace(tc.END, '') + '\n' + tc.BEGIN,
        )
        for text in variants:
            with self.subTest(text=text):
                self.write(tc.TASK_FILE, text)
                before = nc.inventory(self.root)
                self.assertFalse(tc.check(self.root)['ok'])
                with self.assertRaisesRegex(ValueError, 'managed markers'):
                    tc.render(self.root)
                with self.assertRaisesRegex(ValueError, 'managed markers'):
                    self.done('direction')
                self.assertEqual(nc.inventory(self.root), before)

    def test_legacy_compatibility_and_managed_missing_ledger(self):
        legacy = nc.init_book(Path(self.temp.name), '旧书', task_management=False)
        self.assertIsNone(tc.require_adoption(legacy, {}))
        self.assertIsNone(tc.require_sync(legacy, {}))
        self.assertIsNone(tc.require_finalization(legacy, {}))
        self.assertTrue(tc.check(legacy)['ok'])
        state = nc.load_state(legacy)
        state['task_management'] = True
        (legacy / nc.STATE).write_text(json.dumps(state), encoding='utf-8')
        self.assertFalse(tc.check(legacy)['ok'])
        with self.assertRaises(OSError):
            tc.require_adoption(legacy, {})

    def test_adoption_recheck_bound_to_current_draft_and_reviews(self):
        self.chapter_stage()
        self.write('草稿与备选/当前.md', '夹具草稿')
        self.write('审稿与修订/当前.json', '{}')
        for kind in ('prepare', 'draft', 'first_read', 'review', 'revise'):
            self.done(kind, 'S02')
        request = dict(number=1, draft='草稿与备选/当前.md', reviews=['审稿与修订/当前.json'])
        self.done('recheck', 'S02')
        with self.assertRaises(ValueError):
            tc.require_adoption(self.root, request)
        self.done('recheck', 'S02', inputs=[self.evidence(request['draft'])], outputs=[self.evidence(request['reviews'][0])])
        before = nc.inventory(self.root)
        self.assertEqual(tc.require_adoption(self.root, request), 'S02')
        self.assertEqual(before, nc.inventory(self.root))
        self.write(request['draft'], '夹具新稿')
        with self.assertRaises(ValueError):
            tc.require_adoption(self.root, request)

    def test_completed_previous_stage_evidence_does_not_block_unrelated_current_stage(self):
        self.chapter_stage()
        self.write('审稿与修订/验收依据.md', '旧规划依据修改')
        self.assertFalse(tc.check(self.root, 'S01')['ok'])
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertTrue(tc.gate(self.root, 'S02', 'prepare')['ok'])

    def test_readonly_checks_detect_concurrent_changes(self):
        original = tc.load
        def mutated(root, rendered=True):
            result = original(root, rendered=rendered)
            with (self.root / '审稿与修订/验收依据.md').open('a', encoding='utf-8') as stream:
                stream.write('并发变更')
            return result
        with patch.object(tc, 'load', side_effect=mutated):
            checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertIn('book changed', checked['issues'][0])

    def test_cli_request_forms_and_json_failure(self):
        request = dict(id='S01', title='调整后的方向')
        path = Path(self.temp.name) / 'request.json'
        path.write_text(json.dumps(request), encoding='utf-8')
        script = str(Path(tc.__file__))
        for raw in (json.dumps(request), str(path), '@' + str(path)):
            proc = subprocess.run([sys.executable, script, 'plan', '--book', str(self.root), '--request', raw], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(json.loads(proc.stdout)['ok'])
        before = nc.inventory(self.root)
        proc = subprocess.run([sys.executable, script, 'check', '--book', str(self.root)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(before, nc.inventory(self.root))
        proc = subprocess.run([sys.executable, script, 'gate', '--book', str(self.root), '--stage', 'S01', '--before', 'concept'], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(json.loads(proc.stdout)['ok'])

    def prepare_first_read_gate(self):
        self.chapter_stage()
        secret = '大纲/内部技术答案-仅核查者可读.md'
        self.write(secret, '合成内部材料，不得进入首读反馈。')
        self.done('prepare', 'S02')
        self.done('draft', 'S02', inputs=[self.evidence(secret)])
        return secret

    def test_neutral_gate_success_returns_only_mechanical_identifiers(self):
        self.prepare_first_read_gate()
        before = nc.inventory(self.root)
        self.assertEqual(tc.gate(self.root, 'S02', 'first_read', neutral=True),
                         dict(ok=True, stage='S02', before='first_read'))
        self.assertEqual(before, nc.inventory(self.root))

    def test_neutral_gate_hides_stale_private_input_but_host_diagnostics_remain(self):
        secret = self.prepare_first_read_gate()
        self.write(secret, '合成内部材料已经变化。')
        before = nc.inventory(self.root)
        result = tc.gate(self.root, 'S02', 'first_read', neutral=True)
        self.assertEqual(result, dict(ok=False, error='gate_not_ready', stage='S02', before='first_read'))
        with self.assertRaisesRegex(ValueError, secret):
            tc.gate(self.root, 'S02', 'first_read')
        self.assertTrue(any(secret in issue for issue in tc.check(self.root)['issues']))
        self.assertEqual(before, nc.inventory(self.root))

    def test_neutral_gate_hides_ledger_structure_and_inventory_errors(self):
        for failure in (ValueError('内部技术答案-结构错误'), OSError('内部技术答案-路径错误'),
                        RuntimeError('内部技术答案-其他读取故障')):
            with self.subTest(failure=type(failure).__name__):
                for function in ('load', 'reading'):
                    with patch.object(tc, function, side_effect=failure):
                        self.assertEqual(tc.gate(self.root, 'S01', 'direction', neutral=True),
                                         dict(ok=False, error='gate_not_ready', stage='S01', before='direction'))

    def test_neutral_gate_never_echoes_nonmechanical_identifiers(self):
        result = tc.gate(self.root, '大纲/内部技术答案.md', '作者选择了隐藏结局', neutral=True)
        self.assertEqual(result, dict(ok=False, error='gate_not_ready'))

    def test_neutral_gate_cli_does_not_expose_stale_filename_or_ledger_errors(self):
        secret = self.prepare_first_read_gate()
        self.write(secret, '已变化的合成内部材料。')
        command = [sys.executable, str(Path(tc.__file__)), 'gate', '--book', str(self.root),
                   '--stage', 'S02', '--before', 'first_read', '--neutral']
        proc = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout), dict(ok=False, error='gate_not_ready', stage='S02', before='first_read'))
        self.assertNotIn(secret, proc.stdout + proc.stderr)
        self.assertNotIn(str(self.root), proc.stdout + proc.stderr)
        self.assertNotIn('stale evidence', proc.stdout + proc.stderr)
        self.write(tc.TASK_FILE, '损坏的技术账本，含内部技术答案。')
        proc = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['error'], 'gate_not_ready')
        self.assertNotIn('内部技术答案', proc.stdout + proc.stderr)
        self.assertNotIn('managed markers', proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
