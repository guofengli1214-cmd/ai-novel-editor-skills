"""Real filesystem and CLI adoption failures, without fabricated host claims."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import chapterctl as cc
import novelctl as nc


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Task management is covered separately; retain legacy gate coverage.
        self.root = nc.init_book(Path(self.temp.name), '测试', task_management=False)
        self.write('草稿与备选/1.md', '正文。\n')
        self.sha = nc.digest(self.root / '草稿与备选/1.md')
        self.review = dict(schema_version=1, writer_task='w', reviewer=dict(task_id='r', agent_id='reader'), draft_sha256=self.sha, coverage=['literary', 'continuity'], verdict='pass', issues=[])
        self.json('审稿与修订/1.json', self.review)
        self.req = dict(number=1, target='第1章.md', draft='草稿与备选/1.md', expected_sha256=None, writer=dict(task_id='w', agent_id='writer'), reviews=['审稿与修订/1.json'], authorization=dict(mode='trial', source='test fixture approval', approved_sha256=self.sha), change_record='连贯性记录/1.json')

    def write(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8')

    def json(self, name, value):
        self.write(name, json.dumps(value, ensure_ascii=False))

    def adopt(self):
        return cc.adopt(self.root, self.req)

    def record(self, handoff='已完成第1章。'):
        self.write('创作约定/交接.md', handoff)
        self.write('摘要/第1章.md', '第1章摘要。')
        self.write('历史版本/采用记录.md', '第1章采用记录。')
        nc.index_book(self.root)
        evidence = {'summaries': ['摘要/第1章.md'],
                    'history': ['历史版本/采用记录.md'],
                    'indexes': ['索引.md'], 'handoff': ['创作约定/交接.md']}
        updates = [dict(path=names[0], sha256=nc.digest(self.root / names[0]))
                   for names in evidence.values()]
        record = dict(draft_sha256=self.sha,
                      categories={k: 'updated' if k in cc.REQUIRED_UPDATED else 'unchanged'
                                  for k in cc.CATEGORIES},
                      checked_sources=[self.req['target']], updated_files=updates,
                      handoff=dict(path='创作约定/交接.md', chapter=1),
                      category_files=evidence)
        self.json(self.req['change_record'], record)
        nc.index_book(self.root)
        return record

    def test_roundtrip_and_readonly_check(self):
        result = self.adopt()
        self.assertFalse(cc.check(self.root)['ok'])
        self.record()
        cc.finish(self.root, result)
        before = nc.inventory(self.root)
        self.assertTrue(cc.check(self.root)['ok'])
        self.assertEqual(before, nc.inventory(self.root))

    def test_review_failures(self):
        cases = [dict(reviewer=dict(task_id='other', agent_id='writer')), dict(draft_sha256='0'*64), dict(coverage=['literary']), dict(verdict='revise'), dict(issues=[dict(id='x', severity='hard', description='x', evidence='x', status='open')]), dict(issues=[dict(id='x', severity='must_fix', description='x', evidence='x', status='open')])]
        for changes in cases:
            with self.subTest(changes=changes):
                self.json('审稿与修订/1.json', dict(self.review, **changes))
                with self.assertRaises(ValueError):
                    self.adopt()
                self.assertFalse((self.root / '第1章.md').exists())

    def test_empty_and_symlink_draft(self):
        self.write(self.req['draft'], '  \n')
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.adopt()
        (self.root / self.req['draft']).unlink()
        try:
            (self.root / self.req['draft']).symlink_to(Path(self.temp.name) / 'outside')
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink creation requires permission (WinError 1314)')
            raise
        with self.assertRaises(ValueError):
            self.adopt()

    def test_retry_does_not_overwrite_and_backup(self):
        result = self.adopt()
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.adopt()
        self.assertEqual(nc.digest(self.root / '第1章.md'), self.sha)
        self.assertEqual(json.loads((self.root / result['backup'] / 'state.json').read_text())['chapters'], [])

    def test_interruption_leaves_recoverable_pending(self):
        original = nc.atomic_write
        def fail(path, data):
            if Path(path) == self.root / '第1章.md':
                raise OSError('simulated disk failure')
            return original(path, data)
        with patch.object(nc, 'atomic_write', side_effect=fail):
            with self.assertRaises(OSError):
                self.adopt()
        p = nc.load_state(self.root)['pending_operations'][0]
        self.assertEqual(p['phase'], 'prepared')
        self.assertTrue((self.root / p['draft_backup']).is_file())
        with self.assertRaises(ValueError):
            cc.finish(self.root, p)
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.adopt()

    def test_manual_old_chapter_edit(self):
        result = self.adopt(); self.record(); cc.finish(self.root, result)
        self.write('第1章.md', '手改')
        self.req['expected_sha256'] = self.sha
        with self.assertRaisesRegex(ValueError, 'old chapter'):
            self.adopt()

    def test_review_mutation_finish_and_check(self):
        result = self.adopt(); self.record()
        self.json('审稿与修订/1.json', dict(self.review, draft_sha256='0'*64))
        with self.assertRaisesRegex(ValueError, 'stale review'):
            cc.finish(self.root, result)
        self.assertTrue(any('stale review' in issue for issue in cc.check(self.root)['issues']))
        self.json('审稿与修订/1.json', self.review)
        cc.finish(self.root, result)
        self.json('审稿与修订/1.json', dict(self.review, reviewer=dict(task_id='r', agent_id='writer')))
        self.assertFalse(cc.check(self.root)['ok'])

    def test_finish_record_failures(self):
        result = self.adopt(); record = self.record()
        cases = [dict(checked_sources=[]), dict(draft_sha256='0'*64), dict(updated_files=[]), dict(categories=dict(record['categories'], world='pending')), dict(categories=dict(record['categories'], world='updated')), dict(handoff=dict(path='创作约定/交接.md', chapter=2)), dict(updated_files=[dict(path='创作约定/交接.md', sha256='0'*64)])]
        for changes in cases:
            with self.subTest(changes=changes):
                self.json(self.req['change_record'], dict(record, **changes))
                with self.assertRaises(ValueError):
                    cc.finish(self.root, result)
                self.assertTrue(nc.load_state(self.root)['pending_operations'])

    def test_required_updated_categories_cannot_be_bypassed(self):
        result = self.adopt(); record = self.record()
        for category in cc.REQUIRED_UPDATED:
            with self.subTest(category=category):
                changed = dict(record, categories=dict(record['categories'], **{category: 'unchanged'}))
                self.json(self.req['change_record'], changed)
                with self.assertRaisesRegex(ValueError, 'must be updated'):
                    cc.finish(self.root, result)
                self.assertTrue(nc.load_state(self.root)['pending_operations'])
        self.json(self.req['change_record'], record)
        cc.finish(self.root, result)

    def test_novelctl_failure_keeps_pending(self):
        result = self.adopt(); self.record()
        self.write('创作约定/交接.md', '第1章 [坏链接](missing.md)')
        r = json.loads((self.root / self.req['change_record']).read_text())
        next(item for item in r['updated_files'] if item['path'] == '创作约定/交接.md')['sha256'] = nc.digest(self.root / '创作约定/交接.md')
        self.json(self.req['change_record'], r)
        with self.assertRaisesRegex(ValueError, 'novelctl check failed'):
            cc.finish(self.root, result)
        self.assertTrue(nc.load_state(self.root)['pending_operations'])

    def test_split_reviews_and_original_backup(self):
        self.json('审稿与修订/1.json', dict(self.review, coverage=['literary']))
        self.json('审稿与修订/2.json', dict(self.review, coverage=['continuity'], reviewer=dict(task_id='r2', agent_id='reader2')))
        self.req['reviews'].append('审稿与修订/2.json')
        first = self.adopt(); self.record(); cc.finish(self.root, first)
        self.req['expected_sha256'] = self.sha
        second = self.adopt()
        self.assertEqual((self.root / second['backup'] / 'original.md').read_bytes(), (self.root / '第1章.md').read_bytes())

    def test_lock_release_and_explicit_stale_lock(self):
        with self.assertRaises(RuntimeError):
            with cc.locked(self.root):
                with self.assertRaisesRegex(ValueError, 'writer lock exists'):
                    self.adopt()
                raise RuntimeError('failure')
        # A normal exception releases the directory lock.
        with cc.locked(self.root):
            pass

    def test_valid_but_changed_review_detected(self):
        result = self.adopt(); self.record(); cc.finish(self.root, result)
        self.json('审稿与修订/1.json', dict(self.review, note='changed after adoption'))
        self.assertTrue(any('review changed' in issue for issue in cc.check(self.root)['issues']))

    def test_finish_detects_mutation_during_check(self):
        result = self.adopt(); self.record()
        original = nc.check_book
        def mutate(book, *, allow_pending=False):
            self.assertEqual(book, self.root)
            self.assertTrue(allow_pending)
            issues = original(book, allow_pending=allow_pending)
            self.write('摘要/第1章.md', 'concurrent modification')
            return issues
        with patch.object(nc, 'check_book', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'book changed during finish'):
                cc.finish(self.root, result)
        self.assertTrue(nc.load_state(self.root)['pending_operations'])

    def test_cli_request_paths(self):
        request_file = Path(self.temp.name) / 'request.json'
        request_file.write_text(json.dumps(self.req))
        script = Path(cc.__file__)
        for raw in [str(request_file), '@'+str(request_file), json.dumps(self.req)]:
            # Invalid approval intentionally exercises parsing without adopting.
            req = dict(self.req, authorization={})
            request_file.write_text(json.dumps(req))
            if raw.startswith('{'):
                raw = json.dumps(req)
            proc = subprocess.run([sys.executable, str(script), 'adopt', '--book', str(self.root), '--request', raw], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn('invalid authorization mode', json.loads(proc.stdout)['error'])

    def test_save_windows_keeps_atomic_write_without_opening_directory(self):
        path = self.root / '摘要/state.json'
        with patch.object(cc.os, 'name', 'nt'), patch.object(nc, 'atomic_write') as write, patch.object(cc.os, 'open') as opened:
            cc.save(path, {'中文': '已落盘'})
        write.assert_called_once_with(path, '{\n  "中文": "已落盘"\n}\n')
        opened.assert_not_called()

    def test_save_posix_retains_directory_fsync(self):
        path = self.root / '摘要/state.json'
        with patch.object(cc.os, 'name', 'posix'), patch.object(nc, 'atomic_write') as write, patch.object(cc.os, 'open', return_value=123) as opened, patch.object(cc.os, 'fsync') as synced, patch.object(cc.os, 'close') as closed:
            cc.save(path, {'ok': True})
        write.assert_called_once()
        opened.assert_called_once_with(path.parent, os.O_RDONLY)
        synced.assert_called_once_with(123)
        closed.assert_called_once_with(123)

    def test_finish_accepts_zero_padded_handoff(self):
        self.req['target'] = '第0001章-雾港来信.md'
        result = self.adopt()
        self.record('当前正式：第0001章。')
        self.assertTrue(cc.finish(self.root, result)['ok'])

    def test_finish_rejects_different_handoff_chapter(self):
        result = self.adopt()
        self.record('当前正式：第0002章。')
        with self.assertRaisesRegex(ValueError, 'handoff does not name chapter'):
            cc.finish(self.root, result)

    def stale_lock(self, *, owner=True):
        lock = cc.lock_path(self.root)
        lock.mkdir()
        if owner:
            cc.save(lock / 'owner.json', dict(pid=123456, book=str(self.root)))
        return lock

    def test_unlock_requires_explicit_confirmation_and_pending_ids(self):
        lock = self.stale_lock()
        for request, error in [({}, 'confirm_no_writer'),
                               ({'confirm_no_writer': True}, 'expected_pending'),
                               ({'confirm_no_writer': True, 'expected_pending': ['wrong']}, 'does not match')]:
            with self.subTest(request=request):
                with self.assertRaisesRegex(ValueError, error):
                    cc.unlock(self.root, request)
                self.assertTrue((lock / 'owner.json').is_file())

    def test_unlock_dead_pid_preserves_pending_and_journals(self):
        result = self.adopt()
        lock = self.stale_lock()
        before = nc.inventory(self.root)
        with patch.object(cc, 'process_alive', return_value=False):
            unlocked = cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[result['operation_id']]))
        self.assertTrue(unlocked['ok'])
        self.assertFalse(lock.exists())
        self.assertEqual(before, nc.inventory(self.root))
        self.assertEqual(nc.load_state(self.root)['pending_operations'][0]['operation_id'], result['operation_id'])
        with self.assertRaisesRegex(ValueError, 'pending'):
            self.adopt()
        self.record()
        cc.finish(self.root, result)

    def test_unlock_empty_lock_and_reuse_writer(self):
        lock = self.stale_lock(owner=False)
        self.assertTrue(cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))['ok'])
        self.assertFalse(lock.exists())
        with cc.locked(self.root):
            self.assertTrue(lock.is_dir())
        self.assertFalse(lock.exists())

    def test_unlock_refuses_live_pid(self):
        with cc.locked(self.root):
            with self.assertRaisesRegex(ValueError, 'PID is alive'):
                cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
            self.assertTrue((cc.lock_path(self.root) / 'owner.json').is_file())

    def test_unlock_refuses_unknown_files_and_wrong_owner(self):
        lock = self.stale_lock()
        (lock / 'recovery.json').write_text('do not delete', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unknown lock files'):
            cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
        self.assertEqual((lock / 'recovery.json').read_text(encoding='utf-8'), 'do not delete')
        (lock / 'recovery.json').unlink()
        cc.save(lock / 'owner.json', dict(pid=123456, book='other-book'))
        with self.assertRaisesRegex(ValueError, 'book mismatch'):
            cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
        self.assertTrue(lock.is_dir())

    def test_writer_and_unlock_respect_recovery_guard(self):
        lock = self.stale_lock(owner=False)
        guard = lock.with_name(lock.name + '.unlock')
        guard.mkdir()
        with self.assertRaisesRegex(ValueError, 'recovery in progress'):
            with cc.locked(self.root):
                self.fail('writer must not enter during recovery')
        with self.assertRaisesRegex(ValueError, 'recovery in progress'):
            cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
        self.assertTrue(guard.is_dir())
        self.assertTrue(lock.is_dir())

    def test_lock_owner_write_failure_releases_empty_lock(self):
        with patch.object(cc, 'save', side_effect=OSError('simulated owner write failure')):
            with self.assertRaisesRegex(OSError, 'simulated owner'):
                with cc.locked(self.root):
                    self.fail('writer must not enter without owner')
        self.assertFalse(cc.lock_path(self.root).exists())

    def test_process_liveness_is_conservative(self):
        with patch.object(cc.os, 'name', 'posix'), patch.object(cc.os, 'kill', side_effect=ProcessLookupError):
            self.assertFalse(cc.process_alive(12345))
        with patch.object(cc.os, 'name', 'posix'), patch.object(cc.os, 'kill', side_effect=PermissionError):
            self.assertTrue(cc.process_alive(12345))
        for pid in (None, True, -1, 0, '123'):
            with self.subTest(pid=pid), self.assertRaisesRegex(ValueError, 'invalid lock owner PID'):
                cc.process_alive(pid)

    def test_windows_process_liveness_uses_query_handle(self):
        import ctypes
        from ctypes import wintypes
        kernel = Mock()
        with patch.object(cc.os, 'name', 'nt'), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel), patch.object(ctypes, 'get_last_error', create=True) as last_error, patch.object(cc.os, 'kill') as killed:
            kernel.OpenProcess.return_value = 0
            last_error.return_value = 87
            self.assertFalse(cc.process_alive(12345))
            last_error.return_value = 5
            self.assertTrue(cc.process_alive(12345))
            last_error.return_value = 999
            with self.assertRaisesRegex(ValueError, 'cannot verify'):
                cc.process_alive(12345)
            kernel.OpenProcess.return_value = 42
            def set_exit_code(handle, result):
                result._obj.value = 259
                return True
            kernel.GetExitCodeProcess.side_effect = set_exit_code
            self.assertTrue(cc.process_alive(12345))
            kernel.CloseHandle.assert_called_once_with(42)
            killed.assert_not_called()

    def windows_kernel(self, started):
        kernel = Mock()
        kernel.OpenProcess.return_value = 42
        def set_exit_code(handle, result):
            result._obj.value = 259
            return True
        def set_times(handle, created, exited, kernel_time, user_time):
            created._obj.dwHighDateTime = started >> 32
            created._obj.dwLowDateTime = started & 0xFFFFFFFF
            return True
        kernel.GetExitCodeProcess.side_effect = set_exit_code
        kernel.GetProcessTimes.side_effect = set_times
        return kernel

    def test_windows_lock_owner_records_full_creation_filetime(self):
        import ctypes
        started = 0x01DD123456789ABC
        kernel = self.windows_kernel(started)
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel):
            with cc.locked(self.root):
                owner = cc.read_json(cc.lock_path(self.root) / 'owner.json')
                self.assertEqual(owner['pid'], os.getpid())
                self.assertEqual(owner['process_start_time'], started)
                self.assertEqual(owner['book'], str(self.root))
        self.assertFalse(cc.lock_path(self.root).exists())
        kernel.OpenProcess.assert_called_once_with(0x1000, False, os.getpid())
        kernel.CloseHandle.assert_called_once_with(42)

    def test_windows_reused_pid_unlocks_without_changing_pending_or_journals(self):
        import ctypes
        result = self.adopt()
        lock = self.stale_lock()
        cc.save(lock / 'owner.json', dict(pid=123456, book=str(self.root), process_start_time=100))
        before = nc.inventory(self.root)
        kernel = self.windows_kernel(200)
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel):
            unlocked = cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[result['operation_id']]))
        self.assertTrue(unlocked['ok'])
        self.assertFalse(lock.exists())
        self.assertEqual(nc.inventory(self.root), before)
        kernel.CloseHandle.assert_called_once_with(42)
        windows_os.kill.assert_not_called()

    def test_windows_same_process_identity_still_blocks_unlock(self):
        import ctypes
        lock = self.stale_lock()
        owner = dict(pid=123456, book=str(self.root), process_start_time=100)
        cc.save(lock / 'owner.json', owner)
        kernel = self.windows_kernel(100)
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel):
            with self.assertRaisesRegex(ValueError, 'PID is alive'):
                cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
        self.assertEqual(cc.read_json(lock / 'owner.json'), owner)
        self.assertFalse(lock.with_name(lock.name + '.unlock').exists())
        kernel.CloseHandle.assert_called_once_with(42)

    def test_windows_legacy_live_pid_is_not_treated_as_reused(self):
        import ctypes
        lock = self.stale_lock()
        kernel = self.windows_kernel(200)
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel):
            with self.assertRaisesRegex(ValueError, 'PID is alive'):
                cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
        self.assertTrue((lock / 'owner.json').is_file())
        kernel.GetProcessTimes.assert_not_called()

    def test_windows_creation_time_query_failure_preserves_lock(self):
        import ctypes
        lock = self.stale_lock()
        cc.save(lock / 'owner.json', dict(pid=123456, book=str(self.root), process_start_time=100))
        original = (lock / 'owner.json').read_bytes()
        kernel = self.windows_kernel(100)
        kernel.GetProcessTimes.side_effect = None
        kernel.GetProcessTimes.return_value = False
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel):
            with self.assertRaisesRegex(ValueError, 'cannot query.*creation time'):
                cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
        self.assertEqual((lock / 'owner.json').read_bytes(), original)
        self.assertFalse(lock.with_name(lock.name + '.unlock').exists())
        kernel.CloseHandle.assert_called_once_with(42)

    def test_windows_access_denied_does_not_prove_a_reused_pid(self):
        import ctypes
        lock = self.stale_lock()
        cc.save(lock / 'owner.json', dict(pid=123456, book=str(self.root), process_start_time=100))
        kernel = self.windows_kernel(200)
        kernel.OpenProcess.return_value = 0
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel), patch.object(ctypes, 'get_last_error', create=True, return_value=5):
            with self.assertRaisesRegex(ValueError, 'PID is alive'):
                cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
        self.assertTrue((lock / 'owner.json').is_file())
        kernel.CloseHandle.assert_not_called()

    def test_windows_owner_identity_failure_releases_empty_lock(self):
        import ctypes
        kernel = self.windows_kernel(100)
        kernel.GetProcessTimes.side_effect = None
        kernel.GetProcessTimes.return_value = False
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel):
            with self.assertRaisesRegex(ValueError, 'creation time'):
                with cc.locked(self.root):
                    self.fail('writer must not enter without its process identity')
        self.assertFalse(cc.lock_path(self.root).exists())
        kernel.CloseHandle.assert_called_once_with(42)

    def test_invalid_owner_creation_time_cannot_release_a_lock(self):
        lock = self.stale_lock()
        for started in (None, True, 0, -1, '100', 2**64):
            with self.subTest(started=started):
                owner = dict(pid=123456, book=str(self.root), process_start_time=started)
                cc.save(lock / 'owner.json', owner)
                with self.assertRaisesRegex(ValueError, 'invalid lock owner process_start_time'):
                    cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))
                self.assertEqual(cc.read_json(lock / 'owner.json'), owner)

    def test_windows_dead_pid_with_creation_time_can_unlock(self):
        import ctypes
        lock = self.stale_lock()
        cc.save(lock / 'owner.json', dict(pid=123456, book=str(self.root), process_start_time=100))
        kernel = self.windows_kernel(100)
        kernel.OpenProcess.return_value = 0
        windows_os = Mock(wraps=os)
        windows_os.name = 'nt'
        with patch.object(cc, 'os', windows_os), patch.object(ctypes, 'WinDLL', create=True, return_value=kernel), patch.object(ctypes, 'get_last_error', create=True, return_value=87):
            self.assertTrue(cc.unlock(self.root, dict(confirm_no_writer=True, expected_pending=[]))['ok'])
        self.assertFalse(lock.exists())

    def test_check_quick_mode_is_readonly_and_opt_in(self):
        nc.index_book(self.root)
        before = nc.inventory(self.root)
        original = nc.check_book
        with patch.object(nc, 'check_book', wraps=original) as checked:
            self.assertTrue(cc.check(self.root)['ok'])
            checked.assert_called_once_with(self.root, verify_snapshots=True)
        with patch.object(nc, 'check_book', wraps=original) as checked:
            self.assertTrue(cc.check(self.root, verify_snapshots=False)['ok'])
            checked.assert_called_once_with(self.root, verify_snapshots=False)
        self.assertEqual(before, nc.inventory(self.root))

    def test_unlock_and_quick_check_cli(self):
        self.stale_lock(owner=False)
        request = dict(confirm_no_writer=True, expected_pending=[])
        proc = subprocess.run([sys.executable, str(Path(cc.__file__)), 'unlock', '--book', str(self.root), '--request', json.dumps(request)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)['unlocked'])
        nc.index_book(self.root)
        proc = subprocess.run([sys.executable, str(Path(cc.__file__)), 'check', '--book', str(self.root), '--skip-snapshots'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(json.loads(proc.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
