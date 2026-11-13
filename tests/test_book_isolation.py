"""Book-local restore and snapshot boundaries using technical fixtures only."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc


class BookIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name)
        # These empty books and JSON sentinels exercise file boundaries only.
        self.book = nc.init_book(self.library, '本书隔离技术夹具', task_management=False)
        self.other = nc.init_book(self.library, '别书隔离技术夹具', task_management=False)
        self.marker = '创作约定/技术哨兵.json'
        (self.book / self.marker).write_text('{"technical_value": "old"}', encoding='utf-8')
        (self.other / self.marker).write_text('{"technical_value": "foreign"}', encoding='utf-8')
        self.snapshot = nc.snapshot_book(self.book, '本书技术快照')
        self.foreign_snapshot = nc.snapshot_book(self.other, '别书技术快照')

    @contextmanager
    def forbid_foreign_content_reads(self):
        original_open = Path.open
        foreign = self.other

        def guarded_open(path, *args, **kwargs):
            if Path(path).resolve().is_relative_to(foreign):
                raise AssertionError('foreign content must not be opened')
            return original_open(path, *args, **kwargs)

        with patch.object(Path, 'open', guarded_open):
            yield

    def assert_rejected_before_verification(self, snapshot, message):
        before = nc.inventory(self.book)
        with self.forbid_foreign_content_reads():
            with patch.object(nc, 'verify_snapshot', wraps=nc.verify_snapshot) as verify:
                with self.assertRaisesRegex(ValueError, message):
                    nc.restore_book(self.book, snapshot)
                verify.assert_not_called()
        self.assertEqual(nc.inventory(self.book), before)

    def test_foreign_snapshot_is_rejected_before_any_foreign_content_is_opened(self):
        foreign_before = nc.inventory(self.other)
        self.assert_rejected_before_verification(
            self.foreign_snapshot, 'direct child of current book history')
        self.assertEqual(nc.inventory(self.other), foreign_before)

    def test_sibling_nonhistory_history_root_and_nested_snapshot_paths_are_rejected(self):
        sibling = self.library / '同级技术目录'
        sibling.mkdir()
        for path in (sibling, self.book, self.book / '摘要', self.book / nc.HISTORY,
                     self.snapshot / 'payload'):
            with self.subTest(path=path):
                self.assert_rejected_before_verification(
                    path, 'direct child of current book history')

    def test_snapshot_symlink_is_rejected_before_foreign_content_is_opened(self):
        link = self.book / nc.HISTORY / 'snapshot-技术软链'
        try:
            link.symlink_to(self.foreign_snapshot, target_is_directory=True)
        except OSError as exc:
            if sys.platform == 'win32' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows user lacks symbolic-link permission')
            raise
        # The rejected link is deliberately present, so inventory itself must
        # not be used as a prerequisite for this ownership rejection test.
        with self.forbid_foreign_content_reads():
            with patch.object(nc, 'verify_snapshot', wraps=nc.verify_snapshot) as verify:
                with self.assertRaisesRegex(ValueError, 'symbolic link or reparse point'):
                    nc.restore_book(self.book, link)
                verify.assert_not_called()

    def test_snapshot_reparse_point_is_rejected_before_verification(self):
        original_check = nc.is_link_or_reparse

        def pretend_reparse(path):
            return Path(path) == self.snapshot or original_check(path)

        with patch.object(nc, 'is_link_or_reparse', side_effect=pretend_reparse):
            with patch.object(nc, 'verify_snapshot', wraps=nc.verify_snapshot) as verify:
                with self.assertRaisesRegex(ValueError, 'symbolic link or reparse point'):
                    nc.restore_book(self.book, self.snapshot)
                verify.assert_not_called()

    def test_history_symlink_is_rejected_before_foreign_content_is_opened(self):
        history = self.book / nc.HISTORY
        history.rename(self.book / '原技术历史')
        try:
            history.symlink_to(self.other / nc.HISTORY, target_is_directory=True)
        except OSError as exc:
            if sys.platform == 'win32' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows user lacks symbolic-link permission')
            raise
        with self.forbid_foreign_content_reads():
            with patch.object(nc, 'verify_snapshot', wraps=nc.verify_snapshot) as verify:
                with self.assertRaisesRegex(ValueError, 'symbolic link or reparse point'):
                    nc.restore_book(self.book, history / self.foreign_snapshot.name)
                verify.assert_not_called()

    def test_snapshot_history_index_symlink_is_rejected_without_foreign_read_or_partial_snapshot(self):
        history = self.book / nc.HISTORY
        index = history / '索引.md'
        index.unlink()
        try:
            index.symlink_to(self.other / nc.HISTORY / '索引.md')
        except OSError as exc:
            if sys.platform == 'win32' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows user lacks symbolic-link permission')
            raise
        before = sorted(path.name for path in history.iterdir())
        foreign_before = nc.inventory(self.other)
        with self.forbid_foreign_content_reads():
            with patch.object(nc, 'verify_snapshot', wraps=nc.verify_snapshot) as verify:
                with self.assertRaisesRegex(ValueError, 'symbolic link or reparse point'):
                    nc.snapshot_book(self.book, '不得创建的技术快照')
                verify.assert_not_called()
        self.assertEqual(sorted(path.name for path in history.iterdir()), before)
        self.assertTrue(index.is_symlink())
        self.assertEqual(nc.inventory(self.other), foreign_before)

    def test_snapshot_history_index_reparse_is_rejected_without_read_or_partial_snapshot(self):
        history = self.book / nc.HISTORY
        index = history / '索引.md'
        before = nc.inventory(self.book)
        entries_before = sorted(path.name for path in history.iterdir())
        original_check = nc.is_link_or_reparse

        def pretend_reparse(path):
            return Path(path) == index or original_check(path)

        with self.forbid_foreign_content_reads():
            with patch.object(nc, 'is_link_or_reparse', side_effect=pretend_reparse):
                with patch.object(nc, 'verify_snapshot', wraps=nc.verify_snapshot) as verify:
                    with self.assertRaisesRegex(ValueError, 'symbolic link or reparse point'):
                        nc.snapshot_book(self.book, '不得创建的模拟reparse技术快照')
                    verify.assert_not_called()
        self.assertEqual(sorted(path.name for path in history.iterdir()), entries_before)
        self.assertEqual(nc.inventory(self.book), before)

    def test_valid_current_book_snapshot_still_restores(self):
        (self.book / self.marker).write_text('{"technical_value": "new"}', encoding='utf-8')
        with self.forbid_foreign_content_reads():
            backup, recovery = nc.restore_book(self.book, self.snapshot)
        self.assertEqual((self.book / self.marker).read_text(encoding='utf-8'),
                         '{"technical_value": "old"}')
        self.assertEqual(backup.parent, self.book / nc.HISTORY)
        self.assertTrue(recovery.is_dir())

    def test_moved_book_restores_its_own_snapshot_without_original_absolute_path(self):
        moved = self.library / '迁移后的本书技术夹具'
        shutil.copytree(self.book, moved)
        (moved / self.marker).write_text('{"technical_value": "moved-new"}', encoding='utf-8')
        original_before = nc.inventory(self.book)
        nc.restore_book(moved, moved / nc.HISTORY / self.snapshot.name)
        self.assertEqual((moved / self.marker).read_text(encoding='utf-8'),
                         '{"technical_value": "old"}')
        self.assertEqual(nc.inventory(self.book), original_before)


if __name__ == '__main__':
    unittest.main()
