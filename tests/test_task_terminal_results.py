"""Terminal task claims require real durable operations; technical fixtures only."""
import copy
import unittest

import test_editorial_gates as fixtures


tc, nc, cc, bc = fixtures.tc, fixtures.nc, fixtures.cc, fixtures.bc
VERIFY = dict(agent_id='fixture-main', source='显式机械结果核验，不代表真实创作或阅读')


class TerminalResultTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EditorialGateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.f.chapter_ready()
        self.source = self.f.ev('审稿与修订/技术依据.md')

    def assert_rejected_unchanged(self, action, pattern):
        before = nc.inventory(self.root)
        with self.assertRaisesRegex((ValueError, OSError), pattern):
            action()
        self.assertEqual(nc.inventory(self.root), before)

    def adopted(self):
        result = cc.adopt(self.root, self.f.req)
        self.f.done('S02', 'adopt', [self.f.ev(self.f.req['target'])],
                    operation_id=result['operation_id'])
        return result

    def finished(self):
        result = self.adopted()
        self.f.prepare_sync(result)
        cc.finish(self.root, result)
        return result

    def enter_finalization(self):
        result = self.finished()
        self.f.done('S02', 'sync', [self.f.ev(self.f.req['change_record'])])
        tc.plan(self.root, dict(id='F', title='终态结果机械核验', workflow='finalization'))
        tc.plan(self.root, dict(id='S02', next_stage='F'))
        self.f.done('S02', 'next_plan')
        tc.close_stage(self.root, 'S02')
        formal = self.f.ev(self.f.req['target'])
        self.f.done('F', 'whole_read', inputs=[formal])
        self.f.done('F', 'structural_revision')
        self.f.done('F', 'whole_recheck', inputs=[formal])
        tc.update(self.root, dict(stage='F', task='finalize', status='doing'))
        return result

    def final_record(self, stage='F'):
        value = dict(schema_version=1, chapters=bc.manifest(self.root)['chapters'],
                     reviewer=dict(task_id='fixture-whole-task', agent_id='fixture-whole-reader'),
                     reading_scope='full', verdict='ready', task_stage=stage,
                     reading_report=self.source, revision_report=self.source)
        self.f.save(bc.DEFAULT_RECORD, value)
        nc.index_book(self.root)
        return self.f.ev(bc.DEFAULT_RECORD)

    def test_adopt_submission_and_acceptance_require_a_real_operation(self):
        for status in ('submitted', 'done'):
            request = dict(stage='S02', task='adopt', status=status, outputs=[self.source],
                           verification=VERIFY)
            self.assert_rejected_unchanged(lambda: tc.update(self.root, request), 'operation_id')
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        self.assert_rejected_unchanged(
            lambda: tc.update(self.root, dict(stage='S02', task='adopt', status='done',
                                              outputs=[self.source], operation_id='0' * 32,
                                              verification=VERIFY)), 'missing')

    def test_adopt_requires_the_installed_chapter_as_output(self):
        result = cc.adopt(self.root, self.f.req)
        self.assert_rejected_unchanged(
            lambda: tc.submit(self.root, dict(stage='S02', task='adopt', outputs=[self.source],
                                              operation_id=result['operation_id'])),
            'adopt outputs do not cover')
        self.f.done('S02', 'adopt', [self.f.ev(self.f.req['target'])],
                    operation_id=result['operation_id'])
        self.assertTrue(tc.gate(self.root, 'S02', 'sync')['ok'])

    def test_sync_requires_finish_and_its_matching_result_evidence(self):
        result = self.adopted()
        self.f.prepare_sync(result)
        self.assert_rejected_unchanged(
            lambda: self.f.done('S02', 'sync', [self.f.ev(self.f.req['change_record'])]),
            'pending_operations')
        cc.finish(self.root, result)
        self.assert_rejected_unchanged(
            lambda: tc.submit(self.root, dict(stage='S02', task='sync', outputs=[self.source],
                                              checks=self.f.checks('sync', [self.source]))),
            'completed change_record')
        self.f.done('S02', 'sync', [self.f.ev(self.f.req['change_record'])])
        self.assertTrue(tc.check(self.root)['ok'])

    def test_missing_finish_receipt_blocks_sync_even_when_pending_is_clear(self):
        result = self.finished()
        (self.root / result['backup'] / 'completion.json').unlink()
        self.assertEqual(nc.load_state(self.root)['pending_operations'], [])
        self.assert_rejected_unchanged(
            lambda: self.f.done('S02', 'sync', [self.f.ev(self.f.req['change_record'])]),
            'completion.json|unregistered artifact')

    def test_fixed_finish_record_can_be_used_as_sync_output(self):
        result = self.finished()
        receipt = cc.validate_sync_receipt(self.root, result['operation_id'])
        self.f.done('S02', 'sync', [receipt['change_record']])
        self.assertTrue(tc.check(self.root)['ok'])

    def test_finalization_rejects_a_report_without_current_finalization_record(self):
        self.enter_finalization()
        for status in ('submitted', 'done'):
            request = dict(stage='F', task='finalize', status=status, outputs=[self.source],
                           verification=VERIFY)
            self.assert_rejected_unchanged(lambda: tc.update(self.root, request),
                                           'valid current finalization record')

    def test_finalization_record_is_bound_to_this_stage_and_complete_book(self):
        self.enter_finalization()
        record = self.final_record(stage='OTHER')
        self.assert_rejected_unchanged(
            lambda: tc.submit(self.root, dict(stage='F', task='finalize', outputs=[record])),
            'task_stage')
        record = self.final_record()
        tc.submit(self.root, dict(stage='F', task='finalize', outputs=[record]))
        tc.update(self.root, dict(stage='F', task='finalize', status='done', verification=VERIFY))
        self.assertTrue(bc.check(self.root)['ok'])
        tc.plan(self.root, dict(id='F', end_reason='技术核验到此结束。'))
        self.f.done('F', 'next_plan')
        self.assertTrue(tc.close_stage(self.root, 'F')['ok'])
        self.assertTrue(tc.check(self.root)['ok'])

    def test_check_and_close_detect_hand_edited_empty_adoption_claims(self):
        # Simulate an intentionally edited ledger, not successful execution.
        data = tc.load_plan(self.root)
        stage = next(stage for stage in data['stages'] if stage['id'] == 'S02')
        adopt = tc.selected_task(stage, 'adopt')
        adopt.update(status='done', outputs=[copy.deepcopy(self.source)], verification=VERIFY)
        data['current_task'] = tc.selected_task(stage, 'sync')['id']
        tc.save(self.root, data)
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertIn('S02.adopt', checked['stale_tasks'])
        self.assert_rejected_unchanged(lambda: tc.close_stage(self.root, 'S02'), 'operation_id')

    def test_check_and_close_detect_hand_edited_empty_finalization_claims(self):
        self.enter_finalization()
        data = tc.load_plan(self.root)
        stage = next(stage for stage in data['stages'] if stage['id'] == 'F')
        finalize = tc.selected_task(stage, 'finalize')
        finalize.update(status='done', outputs=[copy.deepcopy(self.source)], verification=VERIFY)
        data['current_task'] = tc.selected_task(stage, 'next_plan')['id']
        tc.save(self.root, data)
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertIn('F.finalize', checked['stale_tasks'])
        self.assert_rejected_unchanged(lambda: tc.close_stage(self.root, 'F'),
                                       'valid current finalization record')


if __name__ == '__main__':
    unittest.main()
