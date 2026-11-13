"""Immutable adoption/recovery evidence and durable synchronization receipts."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_chapterctl as fixtures

cc, nc = fixtures.cc, fixtures.nc


class AdoptionArchiveTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ChapterTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.operation = self.f.adopt()
        self.f.record()
        self.archive = self.root / self.operation['backup']

    def strip_new_manifest(self):
        """Represent an actual pre-manifest journal, not an invented empty archive."""
        state = nc.load_state(self.root)
        state['pending_operations'][0].pop('archive_version')
        self.f.json(nc.STATE, state)
        journal = cc.read_json(self.archive / 'journal.json')
        journal.pop('archive_version')
        cc.save(self.archive / 'journal.json', journal)
        (self.archive / 'archive.json').unlink()

    def test_new_archive_and_completion_preserve_fixed_version_evidence(self):
        pending = nc.load_state(self.root)['pending_operations'][0]
        self.assertEqual(cc.validate_adoption_archive(self.root, self.operation['operation_id'], pending), pending)
        result = cc.finish(self.root, self.operation)
        receipt = cc.validate_sync_receipt(self.root, self.operation['operation_id'])
        self.assertEqual(result['completion']['path'], self.operation['backup'] + '/completion.json')
        self.assertEqual(receipt['draft_sha256'], self.f.sha)
        self.assertEqual(cc.read_json(self.archive / 'synchronization.json')['draft_sha256'], self.f.sha)
        self.assertTrue(cc.check(self.root)['ok'])
        # Later legitimate updates to active files cannot erase the earlier finish.
        self.f.json(self.f.req['change_record'], {'later_activity': True})
        self.f.write('创作约定/交接.md', '后续技术交接，不改变上一轮的固定验收依据。')
        self.assertEqual(cc.validate_sync_receipt(self.root, self.operation['operation_id']), receipt)
        self.assertTrue(cc.check(self.root)['ok'])

    def test_corrupt_new_archive_blocks_finish_without_clearing_pending(self):
        for name in ('state.json', 'draft.md', 'journal.json', 'archive.json'):
            with self.subTest(name=name):
                path = self.archive / name
                original = path.read_bytes()
                path.write_bytes(b'{}')
                before = nc.inventory(self.root)
                with self.assertRaises((ValueError, KeyError)):
                    cc.finish(self.root, self.operation)
                self.assertEqual(nc.inventory(self.root), before)
                self.assertTrue(nc.load_state(self.root)['pending_operations'])
                self.assertTrue(any('invalid adoption archive' in issue for issue in nc.check_book(self.root)))
                path.write_bytes(original)
        (self.archive / 'archive.json').unlink()
        with self.assertRaisesRegex(ValueError, 'archive.json'):
            cc.finish(self.root, self.operation)

    def test_replacement_original_backup_is_verified(self):
        cc.finish(self.root, self.operation)
        self.f.req['expected_sha256'] = self.f.sha
        operation = self.f.adopt()
        self.f.record()
        archive = self.root / operation['backup']
        self.operation, self.archive = operation, archive
        original = (archive / 'original.md').read_bytes()
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                if legacy:
                    self.strip_new_manifest()
                (archive / 'original.md').write_text('CORRUPT TECHNICAL BACKUP', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'original hash mismatch'):
                    cc.finish(self.root, operation)
                self.assertTrue(nc.load_state(self.root)['pending_operations'])
                (archive / 'original.md').write_bytes(original)

    def test_legacy_archives_validate_existing_hashes_and_state_relationships(self):
        self.strip_new_manifest()
        for name, data in (('draft.md', b'corrupt'), ('state.json', b'{}')):
            with self.subTest(name=name):
                path = self.archive / name
                original = path.read_bytes()
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    cc.validate_adoption_archive(self.root, self.operation['operation_id'])
                path.write_bytes(original)
        journal = cc.read_json(self.archive / 'journal.json')
        bad = copy.deepcopy(journal)
        bad['request']['target'] = 'another.md'
        cc.save(self.archive / 'journal.json', bad)
        with self.assertRaisesRegex(ValueError, 'request chapter mismatch'):
            cc.finish(self.root, self.operation)
        cc.save(self.archive / 'journal.json', journal)
        cc.finish(self.root, self.operation)
        self.assertIsNone(cc.validate_sync_receipt(self.root, self.operation['operation_id'])['archive_manifest_sha256'])
        self.assertTrue(cc.check(self.root)['ok'])

    def test_adopted_pending_can_finish_when_journal_phase_commit_was_interrupted(self):
        journal = cc.read_json(self.archive / 'journal.json')
        journal['phase'] = 'prepared'
        cc.save(self.archive / 'journal.json', journal)
        cc.finish(self.root, self.operation)
        self.assertEqual(cc.validate_adoption_archive(self.root, self.operation['operation_id'])['phase'], 'completed')
        self.assertTrue(cc.validate_sync_receipt(self.root, self.operation['operation_id']))

    def test_failed_final_state_commit_keeps_receipt_and_supports_identical_retry(self):
        original = cc.save

        def interrupted(path, value):
            if Path(path) == self.root / nc.STATE and not value.get('pending_operations'):
                raise OSError('technical final-state commit interruption')
            return original(path, value)

        with patch.object(cc, 'save', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'commit interruption'):
                cc.finish(self.root, self.operation)
        self.assertTrue(nc.load_state(self.root)['pending_operations'])
        fixed = (self.archive / 'completion.json').read_bytes()
        self.assertEqual(cc.read_json(self.archive / 'journal.json')['phase'], 'completed')
        self.assertTrue(cc.validate_sync_receipt(self.root, self.operation['operation_id']))
        cc.finish(self.root, self.operation)
        self.assertEqual((self.archive / 'completion.json').read_bytes(), fixed)
        self.assertEqual(nc.load_state(self.root)['pending_operations'], [])

    def test_receipt_commit_before_terminal_journal_is_retryable(self):
        original = cc.save

        def interrupted(path, value):
            if Path(path) == self.archive / 'journal.json' and value.get('phase') == 'completed':
                raise OSError('technical terminal-journal interruption')
            return original(path, value)

        with patch.object(cc, 'save', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'terminal-journal interruption'):
                cc.finish(self.root, self.operation)
        self.assertEqual(cc.read_json(self.archive / 'journal.json')['phase'], 'adopted')
        self.assertTrue(cc.validate_sync_receipt(self.root, self.operation['operation_id']))
        fixed = (self.archive / 'completion.json').read_bytes()
        cc.finish(self.root, self.operation)
        self.assertEqual((self.archive / 'completion.json').read_bytes(), fixed)
        self.assertEqual(cc.read_json(self.archive / 'journal.json')['phase'], 'completed')

    def test_previous_operation_missing_completion_is_detected_after_replacement(self):
        cc.finish(self.root, self.operation)
        previous = self.archive
        self.f.req['expected_sha256'] = self.f.sha
        operation = self.f.adopt()
        self.f.record()
        cc.finish(self.root, operation)
        (previous / 'completion.json').unlink()
        self.assertTrue(any('invalid adoption archive' in issue for issue in nc.check_book(self.root)))

    def test_completion_corruption_and_missing_receipt_are_reported(self):
        cc.finish(self.root, self.operation)
        for name in ('completion.json', 'synchronization.json'):
            with self.subTest(name=name):
                path = self.archive / name
                original = path.read_bytes()
                path.write_bytes(b'{}')
                self.assertFalse(cc.check(self.root)['ok'])
                path.write_bytes(original)
        (self.archive / 'completion.json').unlink()
        self.assertFalse(cc.check(self.root)['ok'])

    def test_pending_and_journal_cannot_disagree_about_request(self):
        state = nc.load_state(self.root)
        state['pending_operations'][0]['request']['authorization']['source'] = 'changed technical source'
        self.f.json(nc.STATE, state)
        with self.assertRaisesRegex(ValueError, 'does not match pending'):
            cc.finish(self.root, self.operation)


if __name__ == '__main__':
    unittest.main()
