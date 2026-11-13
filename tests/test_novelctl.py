"""Integration tests using isolated ordinary book directories."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import unquote

SCRIPTS = Path(__file__).resolve().parents[1] / 'novel-studio/scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('novelctl', SCRIPTS / 'novelctl.py')
n = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(n)


class NovelctlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        # These fixtures exercise the legacy file protocol independently.
        self.book = n.init_book(self.parent, '雾港', task_management=False)

    def state(self, state):
        (self.book / n.STATE).write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')

    def chapter(self, number, text):
        p = self.book / ('第%04d章.md' % number)
        p.write_text(text, encoding='utf-8')
        review = self.book / '审稿与修订' / (str(number) + '.md')
        review.write_text('评审', encoding='utf-8')
        return dict(number=number, path=p.name, sha256=n.digest(p), writer_task='writer', review_path=review.relative_to(self.book).as_posix(), reviewer_task='reviewer')

    def symlink(self, link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows user lacks symbolic-link permission (WinError 1314)')
            raise

    def test_init_and_read_only_check(self):
        self.assertEqual(n.check_book(self.book), [])
        before = n.inventory(self.book)
        self.assertEqual(n.main(['check', '--book', str(self.book)]), 0)
        self.assertEqual(before, n.inventory(self.book))
        with self.assertRaises(FileExistsError):
            n.init_book(self.parent, '雾港')
        self.assertEqual(before, n.inventory(self.book))

    def test_index_preserves_prose_and_direct_children(self):
        p = self.book / '人物/索引.md'
        p.write_text('# 手写说明\n重要事实不要覆盖。\n', encoding='utf-8')
        nested = self.book / '人物/甲 乙'
        nested.mkdir()
        (nested / '笔记.md').write_text('笔记', encoding='utf-8')
        n.index_book(self.book)
        text = p.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# 手写说明\n重要事实不要覆盖。'))
        self.assertNotIn('笔记.md', text)
        self.assertIn('%20', text)
        before = n.inventory(self.book)
        n.index_book(self.book)
        self.assertEqual(before, n.inventory(self.book))
        self.assertEqual(n.check_book(self.book), [])

    def test_manual_changes_pending_and_links(self):
        state = n.load_state(self.book)
        state['chapters'] = [self.chapter(1, '原文')]
        state['pending_operations'] = [{'task': '补摘要'}]
        self.state(state)
        (self.book / state['chapters'][0]['path']).write_text('手改 [错链](人物/不存在.md) [越界](../逃逸.md)', encoding='utf-8')
        (self.book / '人物/索引.md').unlink()
        issues = '\n'.join(n.check_book(self.book))
        for expected in ('sha256 mismatch', 'pending_operations', 'invalid local md link', 'missing index'):
            self.assertIn(expected, issues)

    def test_export_only_registered_chapters_in_number_order(self):
        state = n.load_state(self.book)
        state['chapters'] = [self.chapter(2, '# 第二章\n乙'), self.chapter(1, '# 第一章\n甲')]
        self.state(state)
        (self.book / '草稿与备选/草稿.md').write_text('不要导出', encoding='utf-8')
        (self.book / '未登记.md').write_text('不要导出', encoding='utf-8')
        for extension in ('txt', 'md'):
            output = self.parent / ('输出.' + extension)
            n.export_book(self.book, output)
            self.assertEqual(output.read_text(encoding='utf-8'), '# 第一章\n甲\n\n# 第二章\n乙\n')
        (self.book / state['chapters'][0]['path']).write_text('手改', encoding='utf-8')
        with self.assertRaises(ValueError):
            n.export_book(self.book, self.parent / 'bad.txt')

    def test_export_blocks_pending_operations_without_touching_output(self):
        state = n.load_state(self.book)
        state['chapters'] = [self.chapter(1, '正文')]
        state['pending_operations'] = [{'task': '同步摘要'}]
        self.state(state)
        output = self.parent / '输出.txt'
        with self.assertRaisesRegex(ValueError, 'pending_operations'):
            n.export_book(self.book, output)
        self.assertFalse(output.exists())
        output.write_text('已有导出', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'pending_operations'):
            n.export_book(self.book, output)
        self.assertEqual(output.read_text(encoding='utf-8'), '已有导出')
        state['pending_operations'] = []
        self.state(state)
        n.export_book(self.book, output)
        self.assertEqual(output.read_text(encoding='utf-8'), '正文\n')

    def test_check_flags_unregistered_top_level_markdown(self):
        state = n.load_state(self.book)
        state['chapters'] = [self.chapter(1, '已登记')]
        self.state(state)
        (self.book / '未登记.MD').write_text('遗漏正文', encoding='utf-8')
        (self.book / '草稿与备选/草稿.md').write_text('草稿', encoding='utf-8')
        n.index_book(self.book)
        before = n.inventory(self.book)
        issues = n.check_book(self.book)
        self.assertEqual(issues, ['unregistered top-level markdown chapter: 未登记.MD'])
        self.assertEqual(before, n.inventory(self.book))
        (self.book / '未登记.MD').unlink()
        n.index_book(self.book)
        self.assertEqual(n.check_book(self.book), [])

    def test_paths_and_symlinks_rejected(self):
        with self.assertRaises(ValueError):
            n.init_book(self.parent, '../逃逸')
        state = n.load_state(self.book)
        ch = self.chapter(1, '正文')
        state['chapters'] = [ch]
        for path in ('../外部.md', '/tmp/外部.md', '草稿与备选/草稿.md'):
            ch['path'] = path
            self.state(state)
            with self.assertRaises(ValueError):
                n.load_state(self.book)
        ch['path'] = '第0001章.md'
        ch['review_path'] = '../评审.md'
        self.state(state)
        with self.assertRaises(ValueError):
            n.load_state(self.book)
        self.state(dict(schema_version=1, title='雾港', chapters=[], pending_operations=[]))
        self.symlink(self.book / '人物/越界', self.parent)
        self.assertTrue(n.check_book(self.book))
        with self.assertRaises(ValueError):
            n.snapshot_book(self.book, '禁止')

    def test_restore_full_book_and_stale_text_backup(self):
        state = n.load_state(self.book)
        state['chapters'] = [self.chapter(1, '旧正文')]
        self.state(state)
        material = self.book / '人物/人物甲.md'
        material.write_text('旧人物', encoding='utf-8')
        n.index_book(self.book)
        snap = n.snapshot_book(self.book, '原始')
        self.assertFalse((snap / 'payload/历史版本').exists())
        (self.book / '第0001章.md').write_text('新正文', encoding='utf-8')
        material.write_text('新人物', encoding='utf-8')
        stale = self.book / '第0002章.md'
        stale.write_text('陈旧正文', encoding='utf-8')
        backup, recovery = n.restore_book(self.book, snap)
        self.assertEqual((self.book / '第0001章.md').read_text(encoding='utf-8'), '旧正文')
        self.assertEqual(material.read_text(encoding='utf-8'), '旧人物')
        self.assertFalse(stale.exists())
        self.assertEqual((n.verify_snapshot(backup) / stale.name).read_text(encoding='utf-8'), '陈旧正文')
        self.assertTrue((recovery / stale.name).exists())
        self.assertEqual(n.load_state(self.book), state)
        self.assertEqual(n.check_book(self.book), [])

    def test_restore_damaged_live_state_preserves_complete_raw_scene(self):
        original_state = n.load_state(self.book)
        marker = self.book / '人物/恢复标记.bin'
        marker.write_bytes(b'original fixture')
        n.index_book(self.book)
        snap = n.snapshot_book(self.book, '有效恢复来源')
        invalid_states = [b'{ malformed state', None, b'[]',
                          b'{"schema_version":1,"title":"fixture","chapters":{}}']
        for index, content in enumerate(invalid_states):
            with self.subTest(content=content):
                marker.write_bytes(b'fault scene\x00' + bytes([index]))
                if content is None:
                    (self.book / n.STATE).unlink()
                else:
                    (self.book / n.STATE).write_bytes(content)
                before = n.inventory(self.book)
                backup, recovery = n.restore_book(self.book, snap)
                self.assertEqual(backup, recovery)
                self.assertEqual(n.inventory(recovery), before)
                self.assertEqual((recovery / marker.relative_to(self.book)).read_bytes(),
                                 b'fault scene\x00' + bytes([index]))
                if content is None:
                    self.assertFalse((recovery / n.STATE).exists())
                else:
                    self.assertEqual((recovery / n.STATE).read_bytes(), content)
                self.assertEqual(n.load_state(self.book), original_state)
                self.assertEqual(marker.read_bytes(), b'original fixture')
                self.assertEqual(n.check_book(self.book), [])
                n.verify_snapshot(snap)
                with self.assertRaisesRegex(ValueError, 'not a snapshot'):
                    n.verify_snapshot(backup)

    def test_restore_damaged_state_failed_commit_reinstates_exact_scene(self):
        snap = n.snapshot_book(self.book, '有效恢复来源')
        (self.book / n.STATE).write_bytes(b'{ broken state\r\n')
        (self.book / '人物/故障附件.bin').write_bytes(b'preserve\x00raw\xff')
        before = n.inventory(self.book)
        original_rename = n.os.rename
        calls = []

        def fail_commit(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError('injected damaged-state commit failure')
            return original_rename(src, dst)

        with patch.object(n.os, 'rename', side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, 'damaged-state commit failure'):
                n.restore_book(self.book, snap)
        self.assertEqual(n.inventory(self.book), before)
        self.assertEqual((self.book / n.STATE).read_bytes(), b'{ broken state\r\n')
        n.verify_snapshot(snap)

    def test_restore_cli_labels_raw_backup(self):
        snap = n.snapshot_book(self.book, '有效恢复来源')
        (self.book / n.STATE).unlink()
        proc = subprocess.run([sys.executable, str(SCRIPTS / 'novelctl.py'), 'restore',
                               '--book', str(self.book), '--snapshot', str(snap)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('backup_kind: raw_recovery', proc.stdout)
        self.assertEqual(n.check_book(self.book), [])

    def test_check_requires_chapters_to_cover_continuous_numbers_from_one(self):
        state = n.load_state(self.book)
        for numbers in ((2,), (1, 3), (3, 1, 2)):
            with self.subTest(numbers=numbers):
                state['chapters'] = [self.chapter(number, '机械检查夹具') for number in numbers]
                self.state(state)
                n.index_book(self.book)
                before = n.inventory(self.book)
                issues = n.check_book(self.book)
                if numbers == (3, 1, 2):
                    self.assertEqual(issues, [])
                else:
                    self.assertIn('chapter numbers must be continuous from 1', '\n'.join(issues))
                self.assertEqual(n.inventory(self.book), before)

    def test_corrupt_snapshot_does_not_touch_book(self):
        snap = n.snapshot_book(self.book, '损坏测试')
        (snap / 'payload/索引.md').write_text('篡改', encoding='utf-8')
        before = n.inventory(self.book)
        with self.assertRaises(ValueError):
            n.restore_book(self.book, snap)
        self.assertEqual(before, n.inventory(self.book))

    def test_snapshot_manifest_traversal_and_symlink(self):
        snap = n.snapshot_book(self.book, '路径攻击')
        manifest = snap / 'manifest.json'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['files']['../outside.md'] = '0' * 64
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            n.restore_book(self.book, snap)
        del data['files']['../outside.md']
        manifest.write_text(json.dumps(data), encoding='utf-8')
        self.symlink(snap / 'payload/外部', self.parent)
        with self.assertRaises(ValueError):
            n.restore_book(self.book, snap)

    def test_snapshot_index_restore_keeps_manifest_valid(self):
        snap = n.snapshot_book(self.book, '不变归档')
        before = n.inventory(snap)
        n.index_book(self.book)
        n.index_book(self.book)
        self.assertEqual(before, n.inventory(snap))
        n.verify_snapshot(snap)
        self.assertEqual(n.check_book(self.book), [])
        (self.book / '人物/新增.md').write_text('新增', encoding='utf-8')
        n.restore_book(self.book, snap)
        self.assertFalse((self.book / '人物/新增.md').exists())
        n.verify_snapshot(snap)
        self.assertEqual(n.check_book(self.book), [])

    def test_missing_index_entry_and_stale_json_link(self):
        target = self.book / '创作约定/索引.md'
        original = target.read_text(encoding='utf-8')
        target.write_text('\n'.join(line for line in original.splitlines() if '作品状态.json' not in unquote(line)), encoding='utf-8')
        self.assertIn('index missing entry', '\n'.join(n.check_book(self.book)))
        n.index_book(self.book)
        with target.open('a', encoding='utf-8') as f:
            f.write('\n[过期](不存在.json)\n')
        self.assertIn('invalid local', '\n'.join(n.check_book(self.book)))

    def test_export_cannot_overwrite_book_via_parent_components(self):
        with self.assertRaises(ValueError):
            n.export_book(self.book, self.parent / 'not-created/../雾港/索引.md')

    def test_noncanonical_book_path_cannot_bypass_export_boundary(self):
        alias = self.book / '..' / self.book.name
        self.assertEqual(n.root_path(alias), self.book)
        before = (self.book / '索引.md').read_bytes()
        with self.assertRaises(ValueError):
            n.export_book(alias, self.book / '索引.md')
        self.assertEqual((self.book / '索引.md').read_bytes(), before)

    def test_failed_commit_rolls_back(self):
        snap = n.snapshot_book(self.book, '回滚')
        (self.book / '人物/新增.md').write_text('必须保留', encoding='utf-8')
        before = n.inventory(self.book, exclude_history=True)
        original = n.os.rename
        calls = []

        def fail_second(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:
                raise OSError('injected commit failure')
            return original(src, dst)

        with patch.object(n.os, 'rename', side_effect=fail_second):
            with self.assertRaises(OSError):
                n.restore_book(self.book, snap)
        self.assertEqual(before, n.inventory(self.book, exclude_history=True))

    def test_chapter_journals_are_immutable_without_indexes(self):
        archive = self.book / n.HISTORY / ('chapter-' + 'a' * 32)
        archive.mkdir()
        (archive / 'draft.md').write_text('采用备份 [不属于当前资料的链接](missing.md)', encoding='utf-8')
        # Use a valid legacy archive: ordinary link checks still skip archived
        # prose, while adoption-specific checks verify its recovery evidence.
        backup = archive.relative_to(self.book).as_posix()
        (archive / 'state.json').write_text(json.dumps(n.load_state(self.book)), encoding='utf-8')
        journal = dict(operation_id='a' * 32, kind='chapter_adopt', phase='prepared',
                       number=1, target='第0001章.md', draft_sha256=n.digest(archive / 'draft.md'),
                       backup=backup, state_backup=backup + '/state.json', draft_backup=backup + '/draft.md',
                       original_sha256=None, review_hashes={'审稿与修订/1.json': '0' * 64},
                       request=dict(number=1, target='第0001章.md', expected_sha256=None,
                                    reviews=['审稿与修订/1.json']))
        (archive / 'journal.json').write_text(json.dumps(journal), encoding='utf-8')
        before = n.inventory(archive)
        for _ in range(2):
            n.index_book(self.book)
            self.assertEqual(n.check_book(self.book), [])
            self.assertEqual(n.inventory(archive), before)
            self.assertFalse((archive / '索引.md').exists())
        history_index = (self.book / n.HISTORY / '索引.md').read_text(encoding='utf-8')
        self.assertIn(archive.name + '/journal.json', history_index)
        ordinary = self.book / n.HISTORY / '人工笔记'
        ordinary.mkdir()
        self.assertIn('missing index: 历史版本/人工笔记', n.check_book(self.book))

    def test_reparse_attribute_blocks_reads_and_index_writes(self):
        junction = self.book / '人物/目录联接'
        junction.mkdir()
        secret = junction / '秘密.md'
        secret.write_text('不能读取的外部内容', encoding='utf-8')
        original = Path.lstat
        def marked(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == junction:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(Path, 'lstat', marked), patch.object(n, 'digest', wraps=n.digest) as digest:
            self.assertTrue(n.is_link_or_reparse(junction))
            for operation in (lambda: n.relative_path(self.book, '人物/目录联接/秘密.md'),
                              lambda: n.inventory(self.book), lambda: n.index_book(self.book),
                              lambda: n.snapshot_book(self.book, '禁止读外部')):
                with self.assertRaisesRegex(ValueError, 'reparse point'):
                    operation()
            self.assertTrue(n.check_book(self.book))
            self.assertNotIn(secret, [call.args[0] for call in digest.call_args_list])
        self.assertFalse((junction / '索引.md').exists())
        self.assertEqual(secret.read_text(encoding='utf-8'), '不能读取的外部内容')

    def test_resolved_containment_is_checked_even_without_link_attribute(self):
        redirected = self.book / '人物/外部'
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == redirected:
                return self.parent / '外部'
            return original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve):
            with self.assertRaisesRegex(ValueError, 'path escapes book'):
                n.relative_path(self.book, '人物/外部')

    @unittest.skipUnless(os.name == 'nt', 'requires native Windows directory junctions')
    def test_internal_junction_is_rejected_and_external_sentinel_is_untouched(self):
        outside = self.parent / '作品外部'
        outside.mkdir()
        sentinel = outside / '秘密.md'
        sentinel.write_text('外部哨兵', encoding='utf-8')
        junction = self.book / '人物/目录联接'
        result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(junction), str(outside)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, repr(result.stderr))
        self.addCleanup(junction.rmdir)
        with patch.object(n, 'digest', wraps=n.digest) as digest:
            for operation in (lambda: n.inventory(self.book), lambda: n.index_book(self.book),
                              lambda: n.snapshot_book(self.book, '禁止')):
                with self.assertRaisesRegex(ValueError, 'reparse point'):
                    operation()
            self.assertTrue(n.check_book(self.book))
            self.assertFalse(any('秘密.md' in str(call.args[0]) for call in digest.call_args_list))
        self.assertEqual(sentinel.read_text(encoding='utf-8'), '外部哨兵')
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ['秘密.md'])

    @unittest.skipUnless(os.name == 'nt', 'requires native Windows directory junctions')
    def test_junction_used_as_explicit_book_root_is_supported(self):
        alias = self.parent / '作品根联接'
        result = subprocess.run(['cmd', '/d', '/c', 'mklink', '/J', str(alias), str(self.book)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, repr(result.stderr))
        self.addCleanup(alias.rmdir)
        self.assertEqual(n.root_path(alias), self.book.resolve())
        n.index_book(alias)
        self.assertEqual(n.check_book(alias), [])

    def test_windows_invalid_titles_fail_before_any_directory_is_created(self):
        library = self.parent / '尚不存在的作品库'
        for title in ('', ' ', '.', '..', '书名 ', '书名.', 'bad/name', 'bad\\name',
                      'CON', 'con.txt', 'NUL', 'PRN', 'AUX', 'COM1', 'LPT9.md',
                      'COM¹', 'LPT².txt', 'bad\x00name', 'bad\x1fname',
                      *('bad' + char + 'name' for char in '<>:"|?*')):
            with self.subTest(title=title):
                with self.assertRaisesRegex(ValueError, 'portable directory name'):
                    n.init_book(library, title)
                self.assertFalse(library.exists())

    def title_decision(self, title, **changes):
        # Explicitly synthetic author evidence; these tests never draft fiction.
        decision = dict(resolution='provided', value=title,
                        source_quote='合成作者原话：本技术夹具书名使用《' + title + '》。',
                        scope='本次初始化技术测试', prior_status='proposed')
        decision.update(changes)
        return decision

    def init_cli(self, library, title, decision=None):
        command = [sys.executable, str(SCRIPTS / 'novelctl.py'), 'init',
                   '--parent', str(library), '--title', title]
        if decision is not None:
            command.extend(['--title-decision', decision])
        return subprocess.run(command, capture_output=True, text=True)

    def test_cli_init_requires_title_decision_before_creating_library(self):
        library = self.parent / '缺决定作品库'
        result = self.init_cli(library, '仅命令行标题')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('--title-decision', result.stderr)
        self.assertFalse(library.exists())

    def test_init_rejects_invalid_title_decision_before_any_directory(self):
        title = '书名校验技术夹具'
        library = self.parent / '无效决定作品库'
        decisions = [
            {},
            self.title_decision('不匹配技术标题'),
            self.title_decision(title, resolution='unresolved'),
            self.title_decision(title, source_quote=''),
            self.title_decision(title, prior_status='retired',
                                history_source='合成旧决定：此名已弃用。'),
        ]
        for decision in decisions:
            with self.subTest(decision=decision):
                with self.assertRaises(ValueError):
                    n.init_book(library, title, title_decision=decision)
                self.assertFalse(library.exists())

    def test_cli_init_preserves_actual_title_source_for_all_json_inputs(self):
        library = self.parent / '有来源作品库'
        for form in ('inline', 'file', '@file'):
            title = '标题来源技术夹具-' + form.replace('@', 'at')
            decision = self.title_decision(title)
            raw = json.dumps(decision, ensure_ascii=False)
            if form != 'inline':
                path = self.parent / (form.replace('@', 'at') + '-技术决定.json')
                path.write_text(raw, encoding='utf-8-sig')
                raw = ('@' if form == '@file' else '') + str(path)
            result = self.init_cli(library, title, raw)
            self.assertEqual(result.returncode, 0, result.stderr)
            book = library / title
            text = (book / '创作约定/作者约定与授权.md').read_text(encoding='utf-8')
            recorded = json.loads(text.split('```json\n', 1)[1].split('\n```', 1)[0])
            self.assertEqual(recorded, {'title': title, 'decision': decision})
            self.assertIn('不产生正文、设定、剧情或后续创作授权', text)
            self.assertEqual(n.load_state(book)['title'], title)
            self.assertIs(n.load_state(book)['task_management'], True)
            self.assertEqual(n.check_book(book), [])

    def test_old_title_filename_does_not_supply_author_selection(self):
        title = '旧名技术夹具'
        library = self.parent / '不能按文件名建书'
        old_file = self.parent / (title + '.json')
        # A previous filename and an unresolved record do not prove a new choice.
        old_file.write_text(json.dumps(self.title_decision(
            title, resolution='unresolved', prior_status='unknown',
            source_quote='合成记录：仅找到这个文件名，作者尚未选择。'), ensure_ascii=False),
            encoding='utf-8')
        before = old_file.read_bytes()
        result = self.init_cli(library, title, str(old_file))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('unresolved', result.stderr)
        self.assertFalse(library.exists())
        self.assertEqual(old_file.read_bytes(), before)

    def test_retired_title_can_be_reselected_by_explicit_all_recommendations_answer(self):
        title = '复选旧名技术夹具'
        question = ('合成提问：旧名《' + title + '》已弃用，是否重新使用该名，'
                    '并在本次建书全部按推荐走不用询问？')
        answer = '合成作者答复：本次建书全部按推荐走不用询问。'
        decision = self.title_decision(
            title, resolution='delegated', source_quote=answer, prior_status='retired',
            history_source='合成历史决定：此技术标题已弃用。',
            opt_out=dict(question_quote=question, answer_quote=answer,
                         scope='本次初始化技术测试', all_recommendations=True,
                         no_further_questions=True),
            reuse_confirmation=dict(question_quote=question, answer_quote=answer,
                                    acknowledges_retired=True))
        library = self.parent / '明确复选作品库'
        result = self.init_cli(library, title, json.dumps(decision, ensure_ascii=False))
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = (library / title / '创作约定/作者约定与授权.md').read_text(encoding='utf-8')
        self.assertIn('retired', recorded)
        self.assertIn(question, recorded)
        self.assertIn(answer, recorded)

    def test_cli_rejects_nonobject_or_incomplete_json_decision_without_creating_library(self):
        library = self.parent / '非法JSON决定作品库'
        path = self.parent / '空决定.json'
        path.write_text('null', encoding='utf-8')
        for raw in (str(path), '{}', '{invalid'):
            with self.subTest(raw=raw):
                result = self.init_cli(library, '无决定技术标题', raw)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(library.exists())

    def test_valid_title_decision_does_not_overwrite_existing_book(self):
        before = n.inventory(self.book)
        self.assertFalse((self.book / '创作约定/作者约定与授权.md').exists())
        decision = self.title_decision('雾港')
        with self.assertRaises(FileExistsError):
            n.init_book(self.parent, '雾港', title_decision=decision)
        result = self.init_cli(self.parent, '雾港', json.dumps(decision, ensure_ascii=False))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(n.inventory(self.book), before)

    def test_init_creates_missing_library_and_removes_only_failed_new_book(self):
        library = self.parent / '新建/作品库'
        book = n.init_book(library, '中文书名', task_management=False)
        self.assertEqual(n.check_book(book), [])
        before = n.inventory(book)
        with patch.object(n, 'index_book', side_effect=OSError('injected initialization failure')):
            with self.assertRaisesRegex(OSError, 'injected'):
                n.init_book(library, '失败作品', task_management=False)
            self.assertFalse((library / '失败作品').exists())
            with self.assertRaises(FileExistsError):
                n.init_book(library, '中文书名')
        self.assertEqual(n.inventory(book), before)
        retry = n.init_book(library, '失败作品', task_management=False)
        self.assertEqual(n.check_book(retry), [])

    def test_task_initialization_failure_leaves_no_partial_book_or_writer_lock(self):
        def initialize(book):
            raise OSError('ledger write failed')
        taskctl = SimpleNamespace(initialize=initialize)
        before = sorted(p.name for p in self.parent.iterdir())
        with patch.dict('sys.modules', {'taskctl': taskctl}):
            with self.assertRaisesRegex(OSError, 'ledger write failed'):
                n.init_book(self.parent, '任务初始化失败')
        self.assertEqual(sorted(p.name for p in self.parent.iterdir()), before)

    def test_code_examples_are_not_local_file_links(self):
        note = self.book / '摘要/代码示例.md'
        text = ('# 代码示例\n```markdown\n[围栏](不存在1.md)\n[引用]: 不存在2.json\n```\n'
                '~~~md\n![图](不存在3.png)\n~~~\n'
                '````markdown\n```\n[嵌套](不存在4.md)\n```\n````\n'
                '行内 `[例子](不存在5.md)` 和 ``a ` [例子](不存在6.md)``。\n'
                '[真实入口](../创作约定/写作进度与接续说明.md)\n')
        note.write_text(text, encoding='utf-8')
        n.index_book(self.book)
        self.assertEqual(n.check_book(self.book), [])
        note.write_text(text + '[正文坏链接](确实不存在.md)\n', encoding='utf-8')
        issues = n.check_book(self.book)
        self.assertEqual(len(issues), 1)
        self.assertIn('确实不存在.md', issues[0])

    def test_unclosed_fence_and_multiline_code_spans_are_ignored(self):
        note = self.book / '摘要/未闭合示例.md'
        note.write_text('`多行\n[例子](missing.md)`\n  ~~~~md\n[例子](missing2.md)\n', encoding='utf-8')
        n.index_book(self.book)
        self.assertEqual(n.check_book(self.book), [])

    def test_escaped_backticks_do_not_hide_real_broken_links(self):
        note = self.book / '摘要/转义反引号.md'
        note.write_text('示例：\\`文字[实际坏链接](不存在.md)文字\\`\n'
                        '`代码里的反斜杠\\`\n[另一坏链接](也不存在.md)\n', encoding='utf-8')
        n.index_book(self.book)
        issues = n.check_book(self.book)
        self.assertEqual(len(issues), 2)
        self.assertIn('不存在.md', issues[0])
        self.assertIn('也不存在.md', issues[1])

    def test_pending_diagnostic_contains_summary_and_journal_not_request(self):
        state = n.load_state(self.book)
        state['pending_operations'] = [dict(operation_id='operation-1', number=1,
            target='第0001章.md', phase='adopted', backup='历史版本/chapter-a',
            request={'private': 'FULL_REQUEST_MUST_NOT_LEAK' * 100},
            review_hashes={'many-review-paths': 'a' * 64})]
        self.state(state)
        issues = n.check_book(self.book)
        self.assertEqual(len(issues), 1)
        self.assertIn('operation-1', issues[0])
        self.assertIn('历史版本/chapter-a/journal.json', issues[0])
        self.assertNotIn('FULL_REQUEST_MUST_NOT_LEAK', issues[0])
        self.assertNotIn('review_hashes', issues[0])
        self.assertLess(len(issues[0]), 350)
        self.assertEqual(n.check_book(self.book, allow_pending=True), [])

    def test_default_check_hashes_each_snapshot_payload_once_and_quick_check_never(self):
        snapshots = [n.snapshot_book(self.book, str(i)) for i in range(2)]
        payload_files = {p for snap in snapshots for p in (snap / 'payload').rglob('*') if p.is_file()}
        with patch.object(n, 'digest', wraps=n.digest) as digest:
            self.assertEqual(n.check_book(self.book), [])
            calls = [call.args[0] for call in digest.call_args_list]
            for path in payload_files:
                self.assertEqual(calls.count(path), 1, str(path))
        with patch.object(n, 'digest', wraps=n.digest) as digest:
            self.assertEqual(n.check_book(self.book, verify_snapshots=False), [])
            self.assertTrue(payload_files.isdisjoint(call.args[0] for call in digest.call_args_list))
        (snapshots[0] / 'payload/索引.md').write_text('被篡改', encoding='utf-8')
        self.assertIn('invalid snapshot', '\n'.join(n.check_book(self.book)))
        self.assertEqual(n.check_book(self.book, verify_snapshots=False), [])
        self.assertEqual(n.main(['check', '--book', str(self.book), '--skip-snapshots']), 0)

    def test_restore_rejects_non_snapshot_and_unsupported_schema_without_changes(self):
        before = n.inventory(self.book)
        with self.assertRaisesRegex(ValueError, 'direct child of current book history'):
            n.restore_book(self.book, self.book / '摘要')
        self.assertEqual(n.inventory(self.book), before)
        snap = n.snapshot_book(self.book, '版本测试')
        manifest_path = snap / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for version in (True, 2, '1'):
            manifest_path.write_text(json.dumps(dict(manifest, schema_version=version)), encoding='utf-8')
            before = n.inventory(self.book)
            with self.assertRaisesRegex(ValueError, 'unsupported snapshot schema_version'):
                n.restore_book(self.book, snap)
            self.assertEqual(n.inventory(self.book), before)


if __name__ == '__main__':
    unittest.main()
