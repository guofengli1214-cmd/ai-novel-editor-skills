"""Execution and adoption gates with temporary technical fixtures only."""
import copy
import json
from pathlib import Path
import unittest

import test_editorial_gates as fixtures


tc, nc, cc = fixtures.tc, fixtures.nc, fixtures.cc


class ExecutionGateRegressionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EditorialGateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root

    def stage(self):
        return next(s for s in tc.load_plan(self.root)['stages']
                    if s['id'] == tc.load_plan(self.root)['current_stage'])

    def pending_adoption(self):
        self.f.chapter_ready()
        operation = cc.adopt(self.root, self.f.req)
        self.f.done('S02', 'adopt', [self.f.ev(self.f.req['target'])],
                    operation_id=operation['operation_id'])
        return operation

    def add_task(self, task_id, kind, dependencies, sync_dependency=False):
        data = tc.load_plan(self.root)
        stage = next(s for s in data['stages'] if s['id'] == 'S02')
        task = dict(id=task_id, kind=kind, title='同步链机械夹具', role='materials',
                    status='todo', depends_on=dependencies, inputs=[], outputs=[], checks={},
                    acceptance='只检验技术任务门禁，不是实际创作或阅读',
                    verification=None, reason='', history=[], operation_id='')
        sync = tc.selected_task(stage, 'sync')
        stage['tasks'].insert(stage['tasks'].index(sync), task)
        if sync_dependency:
            sync['depends_on'].append(task_id)
        data['current_task'] = next(t['id'] for t in stage['tasks']
                                    if t['status'] not in tc.FINISHED)
        tc.save(self.root, data)
        return task

    def configure_specialist(self, task_id):
        source = self.f.ev('审稿与修订/技术依据.md')
        tc.update(self.root, dict(
            stage='S02', task=task_id, status='todo', inputs=[source],
            checks=dict(creative_decisions=dict(
                source, summary='只记录已采用稿的技术同步依据', items=[],
                none_reason='本轮没有新增创作选择。'))))

    def test_gate_rechecks_target_inputs_for_all_diagnostics_modes_without_writes(self):
        self.f.chapter_ready()
        path = '创作约定/当前任务专用输入.md'
        self.f.write(path, '技术夹具固定输入版本一。')
        evidence = self.f.ev(path)
        tc.update(self.root, dict(stage='S02', task='draft', status='todo', inputs=[evidence]))
        tc.update(self.root, dict(stage='S02', task='draft', status='doing'))
        self.assertTrue(tc.gate(self.root, 'S02', 'draft')['ok'])
        reference = Path(self.f.temp.name) / 'neutral-task-reference.json'
        reference.write_text(json.dumps(dict(
            schema_version=1, book=str(self.root), stage='S02', task='S02.draft')),
            encoding='utf-8')
        original = (self.root / path).read_bytes()
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    (self.root / path).unlink()
                else:
                    self.f.write(path, '技术夹具输入已真实改变。')
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'stale execution evidence'):
                    tc.gate(self.root, 'S02', 'draft')
                self.assertEqual(tc.gate(self.root, 'S02', 'draft', neutral=True)['error'],
                                 'gate_not_ready')
                self.assertEqual(tc.gate_ref(reference)['error'], 'gate_not_ready')
                self.assertIn('S02.draft', tc.check(self.root)['stale_tasks'])
                self.assertEqual(nc.inventory(self.root), before)
                (self.root / path).write_bytes(original)
        self.assertTrue(tc.gate_ref(reference)['ok'])

    def test_pending_blocks_creative_gate_start_and_acceptance_but_not_verified_repair(self):
        self.pending_adoption()
        tc.update(self.root, dict(stage='S02', task='revise', status='todo'))
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'pending_operations'):
            tc.gate(self.root, 'S02', 'revise')
        self.assertEqual(tc.gate(self.root, 'S02', 'revise', neutral=True)['error'],
                         'gate_not_ready')
        for status in ('doing', 'submitted', 'done'):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, 'pending_operations'):
                    tc.update(self.root, dict(stage='S02', task='revise', status=status))
                self.assertEqual(nc.inventory(self.root), before)
        tc.update(self.root, dict(
            stage='S02', task='revise', status='done', recovery_only=True,
            reason='只修复已有技术证据的行政记录，不产生新稿。',
            verification=dict(agent_id='fixture-main', source='当前采用前已有技术证据核验')))
        self.assertEqual(tc.selected_task(self.stage(), 'revise')['status'], 'done')
        self.assertEqual(len(nc.load_state(self.root)['pending_operations']), 1)

    def test_pending_allows_only_specialists_in_the_current_adopt_to_sync_chain(self):
        self.pending_adoption()
        for index, kind in enumerate(sorted(tc.MANAGEMENT_TASK_KINDS)):
            task_id = 'S02.post_' + str(index)
            self.add_task(task_id, kind, ['S02.adopt'], sync_dependency=True)
            self.configure_specialist(task_id)
            self.assertTrue(tc.gate(self.root, 'S02', task_id)['ok'])
            tc.update(self.root, dict(stage='S02', task=task_id, status='doing'))
            path = '历史版本/专项管理/' + task_id + '.md'
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            self.f.write(path, '纯技术夹具：采用后核查的固定同步依据。')
            tc.submit(self.root, dict(stage='S02', task=task_id, outputs=[self.f.ev(path)]))
            tc.update(self.root, dict(
                stage='S02', task=task_id, status='done',
                verification=dict(agent_id='fixture-main', source='显式同步链技术核验')))
        self.assertTrue(tc.gate(self.root, 'S02', 'sync')['ok'])
        self.f.synchronize(nc.load_state(self.root)['pending_operations'][0])
        self.assertEqual(nc.load_state(self.root)['pending_operations'], [])

    def test_pending_rejects_specialist_kind_without_both_chain_boundaries(self):
        self.pending_adoption()
        for task_id, deps, linked in (
                ('S02.only_kind', [], False),
                ('S02.after_only', ['S02.adopt'], False),
                ('S02.before_only', [], True)):
            self.add_task(task_id, 'curve_direction', deps, sync_dependency=linked)
            self.configure_specialist(task_id)
            before = nc.inventory(self.root)
            with self.assertRaisesRegex(ValueError, 'pending_operations'):
                tc.gate(self.root, 'S02', task_id)
            with self.assertRaisesRegex(ValueError, 'pending_operations'):
                tc.update(self.root, dict(stage='S02', task=task_id, status='doing'))
            self.assertEqual(nc.inventory(self.root), before)

    def test_native_adoption_requires_started_adopt_task_with_execution_gate(self):
        self.f.chapter_ready()
        tc.update(self.root, dict(stage='S02', task='adopt', status='todo'))
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'adopt task must be doing'):
            cc.adopt(self.root, self.f.req)
        self.assertEqual(nc.inventory(self.root), before)
        self.assertFalse((self.root / self.f.req['target']).exists())
        tc.update(self.root, dict(stage='S02', task='adopt', status='doing'))
        self.assertTrue(cc.adopt(self.root, self.f.req)['ok'])

    def test_explicitly_upgraded_legacy_stage_also_requires_started_adoption(self):
        self.f.chapter_ready()
        data = tc.load_plan(self.root)
        stage = next(s for s in data['stages'] if s['id'] == 'S02')
        stage['workflow_version'] = 1
        stage.pop('decision_gate_version')
        stage.pop('result_gate_version', None)
        self.assertEqual(stage['execution_gate_version'], 1)
        tc.save(self.root, data)
        tc.update(self.root, dict(stage='S02', task='adopt', status='todo'))
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'adopt task must be doing'):
            cc.adopt(self.root, self.f.req)
        self.assertEqual(nc.inventory(self.root), before)

    def test_native_finish_requires_started_sync_task_with_execution_gate(self):
        operation = self.pending_adoption()
        self.f.prepare_sync(operation)
        tc.update(self.root, dict(stage='S02', task='sync', status='todo'))
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'sync task must be doing'):
            cc.finish(self.root, operation)
        self.assertEqual(nc.inventory(self.root), before)
        self.assertEqual(len(nc.load_state(self.root)['pending_operations']), 1)
        tc.update(self.root, dict(stage='S02', task='sync', status='doing'))
        self.assertTrue(cc.finish(self.root, operation)['ok'])

    def test_close_rejects_unregistered_output_without_closing_or_moving_cursor(self):
        self.f.done('S01', 'direction')
        self.f.done('S01', 'concept')
        tc.plan(self.root, dict(id='S01', end_reason='技术夹具本轮结束。'))
        self.f.done('S01', 'next_plan')
        path = '审稿与修订/尚未登记的中断产物.md'
        self.f.write(path, '纯技术中断产物，应先恢复来源而不是宣称阶段结束。')
        before = nc.inventory(self.root)
        ledger = copy.deepcopy(tc.load_plan(self.root))
        with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
            tc.close_stage(self.root, 'S01')
        self.assertEqual(nc.inventory(self.root), before)
        self.assertEqual(tc.load_plan(self.root), ledger)
        tc.recover(self.root, dict(
            artifacts=[dict(self.f.ev(path), classification='excluded',
                            source=self.f.ev(self.f.recovery_source),
                            source_quote=self.f.recovery_quote, scope='仅封存纯技术异常产物')],
            verification=dict(agent_id='fixture-main', source='显式技术来源核验')))
        self.assertTrue(tc.close_stage(self.root, 'S01')['ok'])


if __name__ == '__main__':
    unittest.main()
