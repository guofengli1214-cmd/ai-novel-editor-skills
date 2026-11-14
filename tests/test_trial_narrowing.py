"""Explicit finite trial closure using existing technical fixtures only."""
import copy
import json
import unittest

import test_voice_trial_flow as fixtures


tc, nc = fixtures.tc, fixtures.nc


class TrialNarrowingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.VoiceTrialFlowTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root

    def ready(self):
        candidates = self.f.prepare_candidates()
        self.f.complete('S02', 'voice_first_read',
                        ['审稿与修订/有限候选技术反馈.md'], candidates[:2])
        return candidates

    def request(self, **changes):
        request = dict(id='S02', workflow='trial_only',
            reason='合成技术范围只交候选及反馈，不选声、不采用。',
            end_reason='本轮有限技术比较已交付。',
            verification=dict(agent_id='fixture-main', source='技术范围及实际交付核对。'))
        request.update(changes)
        return request

    def test_delivered_candidates_can_explicitly_narrow_and_close(self):
        candidates = self.ready()
        before = copy.deepcopy(tc.load_plan(self.root)['stages'][1])
        tc.plan(self.root, self.request())
        after = tc.load_plan(self.root)['stages'][1]
        self.assertEqual(after['workflow'], 'trial_only')
        self.assertEqual(after['workflow_history'][-1]['stage'], before)
        self.assertEqual([t['kind'] for t in after['tasks']],
                         ['prepare', 'voice_trial', 'voice_first_read', 'next_plan'])
        self.assertTrue(tc.check(self.root)['ok'])
        self.f.complete('S02', 'next_plan', ['审稿与修订/有限技术接续.md'])
        tc.close_stage(self.root, 'S02')
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        self.assertTrue(all((self.root / item['path']).is_file() for item in candidates))
        tc.plan(self.root, dict(id='S03', title='新授权的技术整理', workflow='materials'))
        self.assertEqual(tc.load_plan(self.root)['current_stage'], 'S03')

    def test_narrowing_needs_real_scope_verification_and_delivered_feedback(self):
        before = nc.inventory(self.root)
        with self.assertRaises(ValueError):
            tc.plan(self.root, self.request())
        self.assertEqual(nc.inventory(self.root), before)
        self.ready()
        for changes in (dict(reason=''), dict(end_reason=''), dict(verification=None),
                        dict(verification={'agent_id': 'fixture-main'})):
            with self.subTest(changes=changes):
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'narrowing needs'):
                    tc.plan(self.root, self.request(**changes))
                self.assertEqual(nc.inventory(self.root), before)

    def test_started_or_even_configured_later_work_cannot_be_discarded(self):
        self.ready()
        before = copy.deepcopy(tc.load_plan(self.root))
        self.f.configure('S02', 'voice_selection')
        current = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'cannot discard started or recorded work'):
            tc.plan(self.root, self.request())
        self.assertEqual(nc.inventory(self.root), current)
        tc.save(self.root, before)
        self.f.complete('S02', 'voice_selection', ['创作约定/已选技术标签.md'])
        with self.assertRaisesRegex(ValueError, 'cannot discard started or recorded work'):
            tc.plan(self.root, self.request())

    def test_pending_and_unknown_artifacts_block_narrowing_without_changes(self):
        self.ready()
        state = nc.load_state(self.root)
        state['pending_operations'] = [dict(operation_id='technical-pending')]
        nc.atomic_write(self.root / nc.STATE, json.dumps(state))
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'pending_operations'):
            tc.plan(self.root, self.request())
        self.assertEqual(nc.inventory(self.root), before)
        state['pending_operations'] = []
        nc.atomic_write(self.root / nc.STATE, json.dumps(state))
        self.f.write('草稿与备选/未知技术章稿.md', 'TECHNICAL_UNKNOWN_OUTPUT')
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
            tc.plan(self.root, self.request())
        self.assertEqual(nc.inventory(self.root), before)

    def test_changed_candidates_cannot_use_historical_feedback_to_narrow(self):
        candidates = self.ready()
        self.f.write(candidates[0]['path'], 'TECHNICAL_CHANGED_CANDIDATE')
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'accepted current candidates'):
            tc.plan(self.root, self.request())
        self.assertEqual(nc.inventory(self.root), before)


if __name__ == '__main__':
    unittest.main()
