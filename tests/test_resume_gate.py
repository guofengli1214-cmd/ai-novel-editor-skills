"""A private adoption-resume gate never opens ordinary pending execution."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import test_editorial_gates as fixtures


tc, nc, cc = fixtures.tc, fixtures.nc, fixtures.cc


class ResumeGateTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EditorialGateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.f.chapter_ready()

    def prepared_operation(self):
        original = nc.atomic_write
        def interrupted(path, content):
            if Path(path) == self.root / self.f.req['target']:
                raise OSError('technical target installation interruption')
            return original(path, content)
        with patch.object(nc, 'atomic_write', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'target installation interruption'):
                cc.adopt(self.root, self.f.req)
        operation = nc.load_state(self.root)['pending_operations'][0]
        self.assertEqual(operation['phase'], 'prepared')
        return operation

    def test_prepared_resume_gate_preserves_the_normal_pending_block(self):
        operation = self.prepared_operation()
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'pending_operations'):
            tc.gate(self.root, 'S02', 'adopt')
        self.assertFalse(tc.gate(self.root, 'S02', 'adopt', neutral=True)['ok'])
        self.assertEqual(tc.require_adoption(self.root, operation['request'],
                         resume_operation=operation['operation_id']), 'S02')
        with self.assertRaisesRegex(ValueError, 'pending_operations'):
            tc.gate(self.root, 'S02', 'draft')
        self.assertEqual(nc.inventory(self.root), before)

    def test_only_the_original_request_operation_and_doing_adopt_can_resume(self):
        operation = self.prepared_operation()
        request = copy.deepcopy(operation['request'])
        with self.assertRaisesRegex(ValueError, 'original pending request'):
            tc.require_adoption(self.root, dict(request, target='第0001章-其他目标.md'),
                                resume_operation=operation['operation_id'])
        with self.assertRaisesRegex(ValueError, 'original pending request'):
            tc.require_adoption(self.root, request, resume_operation='not-the-operation')
        data = tc.load_plan(self.root)
        tc.selected_task(data['stages'][-1], 'adopt')['status'] = 'blocked'
        tc.save(self.root, data)
        with self.assertRaisesRegex(ValueError, 'doing adopt task'):
            tc.require_adoption(self.root, request, resume_operation=operation['operation_id'])

    def test_resume_revalidates_inputs_dependencies_and_unregistered_artifacts(self):
        operation = self.prepared_operation()
        request = operation['request']
        resume = lambda: tc.require_adoption(self.root, request,
                                             resume_operation=operation['operation_id'])
        source = self.root / self.f.draft['path']
        original = source.read_bytes()
        source.write_bytes(original + b'changed')
        with self.assertRaises(ValueError):
            resume()
        source.write_bytes(original)
        data = tc.load_plan(self.root)
        changed = copy.deepcopy(data)
        tc.selected_task(changed['stages'][-1], 'recheck')['status'] = 'todo'
        changed['current_task'] = 'S02.recheck'
        tc.save(self.root, changed)
        with self.assertRaisesRegex(ValueError, 'not accepted'):
            resume()
        tc.save(self.root, data)
        (self.root / '审稿与修订/未登记恢复产物.md').write_text('未知技术产物', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
            resume()

    def test_adopted_operation_can_resume_only_its_own_installation(self):
        cc.adopt(self.root, self.f.req)
        operation = nc.load_state(self.root)['pending_operations'][0]
        self.assertEqual(operation['phase'], 'adopted')
        self.assertEqual(tc.require_adoption(self.root, operation['request'],
                         resume_operation=operation['operation_id']), 'S02')
        with self.assertRaisesRegex(ValueError, 'not accepted|doing adopt task'):
            tc._gate_details(self.root, 'S02', 'sync', resume_operation=operation['operation_id'])


if __name__ == '__main__':
    unittest.main()
