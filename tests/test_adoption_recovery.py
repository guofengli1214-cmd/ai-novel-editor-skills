"""Recovery of interrupted adoption evidence; all content is technical fixtures."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_chapterctl as fixtures

cc, nc = fixtures.cc, fixtures.nc


class AdoptionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ChapterTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        nc.index_book(self.root)

    def quarantine_request(self, operation):
        return dict(operation_id=operation, confirm_unused=True,
                    reason='Technical interruption before any formal commit',
                    verification=dict(agent_id='fixture-main', source='Verified temporary fixture state and references'))

    def interrupted_adopt(self):
        original = cc.save
        def interrupted(path, value):
            if Path(path).name == 'archive.json':
                raise OSError('technical manifest interruption')
            return original(path, value)
        with patch.object(cc, 'save', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'technical manifest interruption'):
                self.f.adopt()
        archive = next((self.root / nc.HISTORY).glob('chapter-*'))
        self.assertEqual(nc.load_state(self.root)['pending_operations'], [])
        return archive, archive.name[len('chapter-'):]

    def finished_adopt(self):
        operation = self.f.adopt()
        self.f.record()
        cc.finish(self.root, operation)
        return operation

    def test_quarantine_orphan_preserves_bytes_and_allows_real_retry(self):
        archive, operation = self.interrupted_adopt()
        before = nc.inventory(archive)
        result = cc.quarantine(self.root, self.quarantine_request(operation))
        self.assertFalse(archive.exists())
        preserved = Path(result['quarantine']) / archive.name
        self.assertEqual(nc.inventory(preserved), before)
        receipt = cc.read_json(Path(result['receipt']))
        self.assertEqual(receipt['files'], before[0])
        self.assertTrue(cc.check(self.root)['ok'])
        adopted = self.f.adopt()
        self.f.record()
        cc.finish(self.root, adopted)
        self.assertTrue(cc.check(self.root)['ok'])

    def test_quarantine_unrelated_orphan_after_retry_adopt_preserves_pending_and_finishes(self):
        archive, orphan = self.interrupted_adopt()
        operation = self.f.adopt()
        self.f.record()
        with self.assertRaisesRegex(ValueError, 'invalid adoption archive'):
            cc.finish(self.root, operation)
        pending = nc.load_state(self.root)['pending_operations']
        request = self.quarantine_request(orphan)
        with self.assertRaisesRegex(ValueError, 'pending operations do not match expected_pending'):
            cc.quarantine(self.root, request)
        request['expected_pending'] = [operation['operation_id']]
        result = cc.quarantine(self.root, request)
        self.assertEqual(result['pending_operations'], [operation['operation_id']])
        self.assertEqual(nc.load_state(self.root)['pending_operations'], pending)
        self.assertFalse(archive.exists())
        self.assertTrue((self.root / operation['backup']).is_dir())
        cc.finish(self.root, operation)
        self.assertTrue(cc.check(self.root)['ok'])

    def test_explicit_pending_allowance_never_quarantines_the_pending_operation(self):
        operation = self.f.adopt()
        request = self.quarantine_request(operation['operation_id'])
        request['expected_pending'] = [operation['operation_id']]
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'cannot quarantine a pending operation'):
            cc.quarantine(self.root, request)
        self.assertEqual(nc.inventory(self.root), before)

    def test_quarantine_rejects_formal_pending_ledger_operation_and_snapshot_references(self):
        for owner in ('formal', 'pending', 'ledger', 'operation', 'snapshot', 'annotation'):
            with self.subTest(owner=owner):
                temp = fixtures.ChapterTests()
                temp.setUp()
                try:
                    operation = temp.adopt()
                    temp.record()
                    cc.finish(temp.root, operation)
                    archive = temp.root / operation['backup']
                    # Reference types other than the formal chapter are tested
                    # after removing that direct pointer from the live state.
                    state = nc.load_state(temp.root)
                    if owner != 'formal':
                        state['chapters'][0].pop('adoption_operation_id')
                    if owner == 'pending':
                        state['pending_operations'] = [dict(operation_id=operation['operation_id'])]
                    temp.json(nc.STATE, state)
                    if owner == 'ledger':
                        temp.write('创作约定/任务计划与验收.md', json.dumps(dict(history=[dict(operation_id=operation['operation_id'])])))
                    elif owner == 'operation':
                        sibling = temp.root / nc.HISTORY / ('chapter-' + 'a' * 32)
                        sibling.mkdir()
                        (sibling / 'state.json').write_text(json.dumps(dict(operation_id=operation['operation_id'])))
                    elif owner == 'snapshot':
                        nc.snapshot_book(temp.root, 'technical reference snapshot')
                    elif owner == 'annotation':
                        index = temp.root / nc.HISTORY / '索引.md'
                        index.write_text('Preserve reference ' + operation['operation_id'] + '\n' + index.read_text(encoding='utf-8'), encoding='utf-8')
                    before = nc.inventory(temp.root)
                    with self.assertRaisesRegex(ValueError, 'pending operation|still referenced'):
                        cc.quarantine(temp.root, self.quarantine_request(operation['operation_id']))
                    self.assertTrue(archive.is_dir())
                    self.assertEqual(nc.inventory(temp.root), before)
                finally:
                    temp.doCleanups()

    def test_quarantine_refuses_ambiguous_legacy_formal_provenance(self):
        operation = self.finished_adopt()
        state = nc.load_state(self.root)
        state['chapters'][0].pop('adoption_operation_id')
        self.f.json(nc.STATE, state)
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'ambiguous for a legacy formal chapter'):
            cc.quarantine(self.root, self.quarantine_request(operation['operation_id']))
        self.assertEqual(nc.inventory(self.root), before)

    def test_quarantine_cli_requires_explicit_verification_and_uses_request_interface(self):
        archive, operation = self.interrupted_adopt()
        request = self.quarantine_request(operation)
        request.pop('verification')
        with self.assertRaisesRegex(ValueError, 'expected JSON object'):
            cc.quarantine(self.root, request)
        request = self.quarantine_request(operation)
        proc = subprocess.run([sys.executable, cc.__file__, 'quarantine', '--book', str(self.root),
                               '--request', json.dumps(request)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)['ok'])
        self.assertFalse(archive.exists())

    def test_new_snapshot_repairs_corrupt_current_archive_without_blessing_bad_backup(self):
        operation = self.finished_adopt()
        snapshot = nc.snapshot_book(self.root, 'technical trusted archive backup')
        manifest = cc.read_json(snapshot / 'manifest.json')
        self.assertIn('adoption_evidence', manifest)
        self.assertFalse((snapshot / 'payload' / nc.HISTORY).exists())
        self.assertFalse(any('snapshot-' in name for name in manifest['adoption_evidence']['files']))
        archive = self.root / operation['backup']
        (archive / 'draft.md').write_bytes(b'TECHNICAL CORRUPTION')
        before = nc.inventory(self.root)
        backup, recovery = nc.restore_book(self.root, snapshot)
        self.assertEqual(backup, recovery)
        self.assertEqual(nc.inventory(recovery), before)
        self.assertEqual((recovery / operation['backup'] / 'draft.md').read_bytes(), b'TECHNICAL CORRUPTION')
        self.assertTrue(cc.check(self.root)['ok'])
        self.assertEqual(nc.check_book(self.root), [])
        nc.verify_snapshot(snapshot)

    def test_new_snapshot_restores_missing_archive_and_refreshes_live_history_index(self):
        operation = self.finished_adopt()
        snapshot = nc.snapshot_book(self.root, 'technical complete evidence')
        shutil.rmtree(self.root / operation['backup'])
        nc.index_book(self.root)
        self.assertNotIn(operation['operation_id'], (self.root / nc.HISTORY / '索引.md').read_text(encoding='utf-8'))
        nc.restore_book(self.root, snapshot)
        self.assertTrue(cc.check(self.root)['ok'])
        self.assertIn(operation['operation_id'], (self.root / nc.HISTORY / '索引.md').read_text(encoding='utf-8'))

    def legacy_snapshot(self):
        snapshot = nc.snapshot_book(self.root, 'technical legacy snapshot')
        manifest = cc.read_json(snapshot / 'manifest.json')
        manifest.pop('adoption_evidence')
        cc.save(snapshot / 'manifest.json', manifest)
        shutil.rmtree(snapshot / 'adoption-evidence')
        nc.verify_snapshot(snapshot)
        return snapshot

    def test_legacy_snapshot_still_restores_with_intact_current_archives(self):
        self.finished_adopt()
        snapshot = self.legacy_snapshot()
        nc.restore_book(self.root, snapshot)
        self.assertTrue(cc.check(self.root)['ok'])

    def test_legacy_snapshot_rejects_missing_or_corrupt_required_history_before_commit(self):
        operation = self.finished_adopt()
        snapshot = self.legacy_snapshot()
        (self.root / operation['backup'] / 'draft.md').write_bytes(b'TECHNICAL CORRUPTION')
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'legacy snapshot has no adoption evidence backup'):
            nc.restore_book(self.root, snapshot)
        self.assertEqual(nc.inventory(self.root), before)

    def test_new_snapshot_rejects_missing_or_corrupt_evidence_before_touching_live_book(self):
        operation = self.finished_adopt()
        snapshot = nc.snapshot_book(self.root, 'technical evidence integrity')
        evidence = snapshot / 'adoption-evidence' / operation['backup'] / 'draft.md'
        evidence.write_bytes(b'TECHNICAL SNAPSHOT CORRUPTION')
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'snapshot adoption evidence checksum'):
            nc.restore_book(self.root, snapshot)
        self.assertEqual(nc.inventory(self.root), before)

    def test_snapshot_rejects_corrupt_live_adoption_archives(self):
        operation = self.finished_adopt()
        (self.root / operation['backup'] / 'draft.md').write_bytes(b'TECHNICAL CORRUPTION')
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'archived draft hash mismatch'):
            nc.snapshot_book(self.root, 'must not bless corrupt evidence')
        self.assertEqual(nc.inventory(self.root), before)


if __name__ == '__main__':
    unittest.main()
