"""Resume interrupted installation with technical fixtures, never novel generation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_chapterctl as legacy
import test_editorial_gates as managed

cc, nc, tc = managed.cc, managed.nc, managed.tc


class AdoptionResumeTests(unittest.TestCase):
    def fixture(self, replacement=False):
        fixture = legacy.ChapterTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        if replacement:
            original = fixture.adopt()
            fixture.record()
            cc.finish(fixture.root, original)
            previous = fixture.sha
            fixture.write('草稿与备选/2.md', 'TECHNICAL REPLACEMENT FIXTURE\n')
            fixture.sha = nc.digest(fixture.root / '草稿与备选/2.md')
            fixture.review = dict(fixture.review, draft_sha256=fixture.sha)
            fixture.json('审稿与修订/2.json', fixture.review)
            fixture.req.update(draft='草稿与备选/2.md', expected_sha256=previous,
                               reviews=['审稿与修订/2.json'], change_record='连贯性记录/2.json',
                               authorization=dict(mode='trial', source='technical replacement approval',
                                                  approved_sha256=fixture.sha))
        return fixture

    def interrupt(self, fixture, boundary):
        root, target = fixture.root, fixture.root / fixture.req['target']
        write, save = nc.atomic_write, cc.save

        def target_write(path, data):
            if Path(path) == target:
                if boundary == 'target_after':
                    write(path, data)
                raise OSError('technical installation interruption')
            return write(path, data)

        def state_write(path, value):
            state_boundary = (Path(path) == root / nc.STATE and value.get('pending_operations')
                              and value['pending_operations'][0].get('phase') == 'adopted')
            journal_boundary = (Path(path).name == 'journal.json' and value.get('phase') == 'adopted')
            selected = state_boundary if boundary.startswith('state') else journal_boundary
            if selected:
                if boundary.endswith('_after'):
                    save(path, value)
                raise OSError('technical installation interruption')
            return save(path, value)

        tool, name, handler = ((nc, 'atomic_write', target_write) if boundary.startswith('target')
                               else (cc, 'save', state_write))
        with patch.object(tool, name, side_effect=handler):
            with self.assertRaisesRegex(OSError, 'installation interruption'):
                cc.adopt(root, fixture.req)
        return copy.deepcopy(nc.load_state(root)['pending_operations'][0])

    def test_new_and_replacement_resume_all_installation_boundaries_and_finish(self):
        for replacement in (False, True):
            for boundary in ('target_before', 'target_after', 'state_before', 'state_after',
                             'journal_before', 'journal_after'):
                with self.subTest(replacement=replacement, boundary=boundary):
                    fixture = self.fixture(replacement)
                    pending = self.interrupt(fixture, boundary)
                    archive = fixture.root / pending['backup']
                    fixed = {name: (archive / name).read_bytes()
                             for name in ('state.json', 'draft.md', 'archive.json')}
                    self.assertFalse(list((fixture.root / nc.HISTORY).glob('snapshot-*')))
                    request = dict(operation_id=pending['operation_id'])
                    result = cc.resume(fixture.root, request)
                    self.assertEqual(result['operation_id'], pending['operation_id'])
                    self.assertEqual(nc.load_state(fixture.root)['pending_operations'][0]['request'],
                                     pending['request'])
                    self.assertEqual(nc.digest(fixture.root / fixture.req['target']), fixture.sha)
                    self.assertEqual(cc.read_json(archive / 'journal.json')['phase'], 'adopted')
                    before = nc.inventory(fixture.root)
                    self.assertEqual(cc.resume(fixture.root, request), result)
                    self.assertEqual(nc.inventory(fixture.root), before)
                    self.assertEqual({name: (archive / name).read_bytes() for name in fixed}, fixed)
                    fixture.record()
                    cc.finish(fixture.root, result)
                    self.assertTrue(cc.check(fixture.root)['ok'])

    def test_resume_can_itself_be_interrupted_and_retried(self):
        for boundary in ('state', 'journal'):
            with self.subTest(boundary=boundary):
                fixture = self.fixture()
                pending = self.interrupt(fixture, 'target_before')
                original = cc.save

                def interrupted(path, value):
                    matches = ((Path(path) == fixture.root / nc.STATE) if boundary == 'state'
                               else Path(path).name == 'journal.json')
                    if matches:
                        raise OSError('technical resume interruption')
                    return original(path, value)

                request = dict(operation_id=pending['operation_id'])
                with patch.object(cc, 'save', side_effect=interrupted):
                    with self.assertRaisesRegex(OSError, 'resume interruption'):
                        cc.resume(fixture.root, request)
                result = cc.resume(fixture.root, request)
                fixture.record()
                cc.finish(fixture.root, result)
                self.assertTrue(cc.check(fixture.root)['ok'])

    def test_missing_replacement_target_is_an_external_change_and_is_preserved(self):
        fixture = self.fixture(True)
        pending = self.interrupt(fixture, 'target_before')
        (fixture.root / fixture.req['target']).unlink()
        before = nc.inventory(fixture.root)
        with self.assertRaisesRegex(ValueError, 'target changed'):
            cc.resume(fixture.root, dict(operation_id=pending['operation_id']))
        self.assertEqual(nc.inventory(fixture.root), before)

    def test_resume_rejects_mutated_target_draft_reviews_state_archive_and_operation(self):
        for mutation in ('target', 'draft', 'review', 'state', 'pending', 'backup', 'operation'):
            with self.subTest(mutation=mutation):
                fixture = self.fixture()
                pending = self.interrupt(fixture, 'target_after')
                request = dict(operation_id=pending['operation_id'])
                if mutation in ('target', 'draft', 'review'):
                    path = {'target': fixture.req['target'], 'draft': fixture.req['draft'],
                            'review': fixture.req['reviews'][0]}[mutation]
                    fixture.write(path, 'MANUAL TECHNICAL CHANGE')
                elif mutation in ('state', 'pending'):
                    state = nc.load_state(fixture.root)
                    if mutation == 'state':
                        state['title'] = 'UNRELATED STATE CHANGE'
                    else:
                        state['pending_operations'][0]['request']['authorization']['source'] = 'CHANGED'
                    fixture.json(nc.STATE, state)
                elif mutation == 'backup':
                    fixture.write(pending['draft_backup'], 'CORRUPTED ARCHIVE')
                else:
                    request['operation_id'] = '0' * 32
                before = nc.inventory(fixture.root)
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    cc.resume(fixture.root, request)
                self.assertEqual(nc.inventory(fixture.root), before)

    def test_adopted_state_must_match_original_installation(self):
        fixture = self.fixture()
        pending = self.interrupt(fixture, 'journal_before')
        state = nc.load_state(fixture.root)
        state['chapters'][0]['writer_agent'] = 'ALTERED IDENTITY'
        fixture.json(nc.STATE, state)
        before = nc.inventory(fixture.root)
        with self.assertRaisesRegex(ValueError, 'state differs'):
            cc.resume(fixture.root, dict(operation_id=pending['operation_id']))
        self.assertEqual(nc.inventory(fixture.root), before)

    def test_legacy_archive_without_fixed_manifest_requires_snapshot_recovery(self):
        fixture = self.fixture()
        pending = self.interrupt(fixture, 'target_after')
        state = nc.load_state(fixture.root)
        state['pending_operations'][0].pop('archive_version')
        fixture.json(nc.STATE, state)
        archive = fixture.root / pending['backup']
        journal = cc.read_json(archive / 'journal.json')
        journal.pop('archive_version')
        cc.save(archive / 'journal.json', journal)
        (archive / 'archive.json').unlink()
        before = nc.inventory(fixture.root)
        with self.assertRaisesRegex(ValueError, 'checksum adoption archive'):
            cc.resume(fixture.root, dict(operation_id=pending['operation_id']))
        self.assertEqual(nc.inventory(fixture.root), before)

    def test_extra_change_during_resume_validation_never_installs_candidate(self):
        fixture = self.fixture()
        pending = self.interrupt(fixture, 'target_before')

        def concurrent_change(*args, **kwargs):
            fixture.write(fixture.req['target'], 'EXTRA MANUAL CHANGE DURING VALIDATION')
            return None

        with patch.object(tc, 'require_adoption', side_effect=concurrent_change):
            with self.assertRaisesRegex(ValueError, 'book changed during resume validation'):
                cc.resume(fixture.root, dict(operation_id=pending['operation_id']))
        self.assertEqual((fixture.root / fixture.req['target']).read_text(encoding='utf-8'),
                         'EXTRA MANUAL CHANGE DURING VALIDATION')
        self.assertEqual(nc.load_state(fixture.root)['pending_operations'][0]['phase'], 'prepared')

    def test_state_change_at_initial_inventory_is_never_overwritten(self):
        fixture = self.fixture()
        pending = self.interrupt(fixture, 'target_before')
        inventory = nc.inventory
        initial = True

        def changed_inventory(root, *args, **kwargs):
            nonlocal initial
            if initial:
                initial = False
                state = nc.load_state(root)
                state['title'] = 'MANUAL TITLE CHANGE DURING INITIAL INVENTORY'
                fixture.json(nc.STATE, state)
            return inventory(root, *args, **kwargs)

        with patch.object(nc, 'inventory', side_effect=changed_inventory):
            with self.assertRaisesRegex(ValueError, 'state differs'):
                cc.resume(fixture.root, dict(operation_id=pending['operation_id']))
        self.assertEqual(nc.load_state(fixture.root)['title'],
                         'MANUAL TITLE CHANGE DURING INITIAL INVENTORY')
        self.assertFalse((fixture.root / fixture.req['target']).exists())

    def test_managed_resume_preserves_doing_adopt_then_real_sync_and_finish(self):
        fixture = managed.EditorialGateTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.chapter_ready()
        pending = self.interrupt(fixture, 'target_after')
        with self.assertRaisesRegex(ValueError, 'pending_operations'):
            tc.gate(fixture.root, 'S02', 'adopt')
        result = cc.resume(fixture.root, dict(operation_id=pending['operation_id']))
        fixture.synchronize(result)
        self.assertTrue(tc.check(fixture.root)['ok'])
        self.assertTrue(cc.check(fixture.root)['ok'])

    def test_managed_resume_rejects_changed_task_and_policy(self):
        for mutation in ('task', 'policy'):
            with self.subTest(mutation=mutation):
                fixture = managed.EditorialGateTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                fixture.chapter_ready()
                pending = self.interrupt(fixture, 'target_after')
                if mutation == 'policy':
                    fixture.save(fixture.policy_path, {})
                else:
                    plan = tc.load_plan(fixture.root)
                    task = tc.selected_task(plan['stages'][1], 'adopt')
                    task['status'] = 'blocked'
                    plan['current_task'] = task['id']
                    tc.save(fixture.root, plan)
                before = nc.inventory(fixture.root)
                with self.assertRaises(ValueError):
                    cc.resume(fixture.root, dict(operation_id=pending['operation_id']))
                self.assertEqual(nc.inventory(fixture.root), before)

    def test_resume_cli_uses_original_operation_and_rejects_missing_pending(self):
        fixture = self.fixture()
        pending = self.interrupt(fixture, 'target_after')
        proc = subprocess.run([sys.executable, cc.__file__, 'resume', '--book', str(fixture.root),
                               '--request', json.dumps(dict(operation_id=pending['operation_id']))],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result['operation_id'], pending['operation_id'])
        fixture.record()
        cc.finish(fixture.root, result)
        with self.assertRaisesRegex(ValueError, 'one pending adoption'):
            cc.resume(fixture.root, dict(operation_id=pending['operation_id']))


if __name__ == '__main__':
    unittest.main()
