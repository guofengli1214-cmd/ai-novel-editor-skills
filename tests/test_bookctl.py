"""Whole-book version gates with explicit fixtures, not literary evidence."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import bookctl as bc
import chapterctl as cc
import novelctl as nc


class BookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # These fixtures isolate the existing whole-book version checks.
        self.root = nc.init_book(Path(self.temp.name), '定稿测试', task_management=False)
        self.add_chapter(2)
        self.add_chapter(1)
        self.write('审稿与修订/完整阅读.md', '测试夹具：全书阅读报告，非实际文学证据。')
        self.write('审稿与修订/整体修订.md', '测试夹具：整体修订对照，非实际文学证据。')
        self.record = dict(
            schema_version=1, chapters=self.current_chapters(),
            reviewer=dict(task_id='fixture-full-review', agent_id='fixture-full-reader'),
            reading_scope='full', verdict='ready',
            reading_report=self.report('审稿与修订/完整阅读.md'),
            revision_report=self.report('审稿与修订/整体修订.md'))
        self.save_record()

    def write(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8')

    def save_json(self, name, value):
        self.write(name, json.dumps(value, ensure_ascii=False))

    def add_chapter(self, number, text=None):
        state = nc.load_state(self.root)
        name = '第%04d章.md' % number
        self.write(name, text or '测试夹具第%d章。' % number)
        sha = nc.digest(self.root / name)
        review_path = '审稿与修订/逐章%d.json' % number
        self.save_json(review_path, dict(
            schema_version=1, writer_task='fixture-w%d' % number,
            reviewer=dict(task_id='fixture-r%d' % number, agent_id='fixture-reader%d' % number),
            draft_sha256=sha, coverage=['literary', 'continuity'], verdict='pass', issues=[]))
        chapter = dict(number=number, path=name, sha256=sha,
                       writer_task='fixture-w%d' % number, writer_agent='fixture-writer%d' % number,
                       reviewer_task='fixture-r%d' % number, review_path=review_path,
                       review_hashes={review_path: nc.digest(self.root / review_path)})
        state['chapters'] = [ch for ch in state['chapters'] if ch['number'] != number] + [chapter]
        self.save_json(nc.STATE, state)
        nc.index_book(self.root)

    def current_chapters(self):
        return [{key: ch[key] for key in ('number', 'path', 'sha256')}
                for ch in sorted(nc.load_state(self.root)['chapters'], key=lambda ch: ch['number'])]

    def report(self, name):
        return dict(path=name, sha256=nc.digest(self.root / name))

    def save_record(self, record=None, name=bc.DEFAULT_RECORD):
        self.save_json(name, self.record if record is None else record)
        nc.index_book(self.root)

    def test_matching_book_and_manifest_are_readonly(self):
        before = nc.inventory(self.root)
        self.assertEqual(bc.manifest(self.root), dict(ok=True, chapters=self.record['chapters']))
        self.assertEqual(bc.check(self.root), dict(ok=True, chapters=self.record['chapters'], record=bc.DEFAULT_RECORD))
        self.assertEqual([ch['number'] for ch in self.record['chapters']], [1, 2])
        self.assertEqual(before, nc.inventory(self.root))

    def test_gapped_formal_numbers_block_manifest_and_finalization(self):
        state = nc.load_state(self.root)
        state['chapters'][0]['number'] = 3
        self.save_json(nc.STATE, state)
        self.record['chapters'] = self.current_chapters()
        self.save_record()
        before = nc.inventory(self.root)
        for operation in (bc.manifest, bc.check):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, 'continuous from 1'):
                    operation(self.root)
        self.assertEqual(nc.inventory(self.root), before)

    def test_default_manifest_and_final_check_reject_bad_snapshot(self):
        snapshot = nc.snapshot_book(self.root, '快照校验回归夹具')
        (snapshot / 'payload/第0001章.md').write_text('损坏的归档正文。', encoding='utf-8')
        before = nc.inventory(self.root)
        for operation in (bc.manifest, bc.check):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, 'invalid snapshot'):
                    operation(self.root)
        self.assertEqual(bc.manifest(self.root, verify_snapshots=False),
                         dict(ok=True, chapters=self.record['chapters'], snapshots_verified=False))
        self.assertEqual(before, nc.inventory(self.root))

    def test_quick_manifest_never_hashes_snapshot_payload(self):
        snapshot = nc.snapshot_book(self.root, '不得读取快照 payload')
        payload = snapshot / 'payload'
        before = nc.inventory(self.root)
        original = nc.digest
        def no_payload_reads(path):
            candidate = Path(path)
            if candidate == payload or payload in candidate.parents:
                self.fail('quick manifest attempted to hash snapshot payload: ' + str(candidate))
            return original(path)
        with patch.object(nc, 'digest', side_effect=no_payload_reads):
            result = bc.manifest(self.root, verify_snapshots=False)
        self.assertTrue(result['ok'])
        self.assertIs(result['snapshots_verified'], False)
        self.assertEqual(result['chapters'], self.record['chapters'])
        self.assertEqual(before, nc.inventory(self.root))
        self.write('第0001章.md', '尚未登记的正文修改。')
        with self.assertRaisesRegex(ValueError, 'chapter sha256 mismatch'):
            bc.manifest(self.root, verify_snapshots=False)

    def test_quick_manifest_detects_concurrent_live_file_changes(self):
        nc.snapshot_book(self.root, '并发检查夹具')
        original = cc.check
        def changed(book, *, verify_snapshots=True):
            self.assertIs(verify_snapshots, False)
            result = original(book, verify_snapshots=verify_snapshots)
            with (self.root / '创作约定/写作进度与接续说明.md').open('a', encoding='utf-8') as stream:
                stream.write('\n快照快速模式期间的并发修改。')
            return result
        with patch.object(cc, 'check', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'book changed during validation'):
                bc.manifest(self.root, verify_snapshots=False)

    def test_skip_snapshots_cli_is_limited_to_manifest(self):
        snapshot = nc.snapshot_book(self.root, 'CLI 快照检查夹具')
        (snapshot / 'payload/第0001章.md').write_text('损坏的快照。', encoding='utf-8')
        before = nc.inventory(self.root)
        proc = subprocess.run([sys.executable, str(Path(bc.__file__)), 'manifest',
                               '--book', str(self.root), '--skip-snapshots'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result['ok'])
        self.assertIs(result['snapshots_verified'], False)
        proc = subprocess.run([sys.executable, str(Path(bc.__file__)), 'check',
                               '--book', str(self.root), '--skip-snapshots'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn('unrecognized arguments: --skip-snapshots', proc.stderr)
        with self.assertRaisesRegex(ValueError, 'snapshot verification is required'):
            bc.inspect_book(self.root, bc.DEFAULT_RECORD, verify_snapshots=False)
        self.assertEqual(before, nc.inventory(self.root))

    def test_empty_book_and_missing_record(self):
        empty = nc.init_book(Path(self.temp.name), '空书', task_management=False)
        for operation in (bc.manifest, bc.check):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, 'no formal chapters'):
                    operation(empty)
        (self.root / bc.DEFAULT_RECORD).unlink()
        nc.index_book(self.root)
        self.assertTrue(bc.manifest(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'missing evidence'):
            bc.check(self.root)

    def test_record_chapters_must_match_exactly(self):
        cases = [[], self.record['chapters'][:1], list(reversed(self.record['chapters'])),
                 self.record['chapters'] + [dict(number=3, path='第0003章.md', sha256='0' * 64)]]
        for field, value in [('number', 3), ('number', True), ('path', '第0009章.md'),
                             ('sha256', '0' * 64), ('extra', 'unexpected')]:
            changed = copy.deepcopy(self.record['chapters'])
            changed[0][field] = value
            cases.append(changed)
        for chapters in cases:
            with self.subTest(chapters=chapters):
                self.save_record(dict(self.record, chapters=chapters))
                with self.assertRaisesRegex(ValueError, 'chapter'):
                    bc.check(self.root)

    def test_current_book_content_addition_and_deletion_invalidate_record(self):
        self.add_chapter(1, '新的已评审夹具正文。')
        self.assertTrue(cc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'chapters do not match'):
            bc.check(self.root)
        self.record['chapters'] = self.current_chapters()
        self.save_record()
        self.assertTrue(bc.check(self.root)['ok'])
        self.add_chapter(3)
        with self.assertRaisesRegex(ValueError, 'chapters do not match'):
            bc.check(self.root)
        self.record['chapters'] = self.current_chapters()
        self.save_record()
        state = nc.load_state(self.root)
        state['chapters'] = [ch for ch in state['chapters'] if ch['number'] != 3]
        self.save_json(nc.STATE, state)
        (self.root / '第0003章.md').unlink()
        nc.index_book(self.root)
        self.assertTrue(cc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'chapters do not match'):
            bc.check(self.root)

    def test_actual_chapter_renumbering_invalidates_record(self):
        state = nc.load_state(self.root)
        first, second = state['chapters']
        first['number'], second['number'] = second['number'], first['number']
        self.save_json(nc.STATE, state)
        self.assertTrue(cc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'chapters do not match'):
            bc.check(self.root)

    def test_actual_chapter_rename_invalidates_record(self):
        state = nc.load_state(self.root)
        chapter = state['chapters'][0]
        old = self.root / chapter['path']
        chapter['path'] = '第0002章-新标题.md'
        old.rename(self.root / chapter['path'])
        self.save_json(nc.STATE, state)
        nc.index_book(self.root)
        self.assertTrue(cc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'chapters do not match'):
            bc.check(self.root)

    def test_report_missing_changed_and_empty(self):
        for field in ('reading_report', 'revision_report'):
            path = self.root / self.record[field]['path']
            original = path.read_text(encoding='utf-8')
            for text in (None, '报告被改', '  \n'):
                with self.subTest(field=field, text=text):
                    if text is None:
                        path.unlink()
                    else:
                        path.write_text(text, encoding='utf-8')
                    nc.index_book(self.root)
                    with self.assertRaises(ValueError):
                        bc.check(self.root)
                    path.write_text(original, encoding='utf-8')
                    nc.index_book(self.root)

    def test_report_paths_and_record_paths_are_constrained(self):
        for name in ('第0001章.md', nc.STATE, '审稿与修订/逐章1.json',
                     '../逃逸.md', '/tmp/外部.md', '审稿与修订/../完整阅读.md',
                     '审稿与修订//完整阅读.md'):
            with self.subTest(report=name):
                self.save_record(dict(self.record, reading_report=dict(path=name, sha256='0' * 64)))
                with self.assertRaises(ValueError):
                    bc.check(self.root)
        self.save_record()
        for name in (None, '', nc.STATE, '第0001章.md', '../外部.json', '审稿与修订/完整阅读.md'):
            with self.subTest(record=name):
                with self.assertRaises(ValueError):
                    bc.check(self.root, name)

    def test_combined_report_and_custom_record_are_supported(self):
        combined = dict(self.record, revision_report=self.record['reading_report'])
        self.save_record(combined, '审稿与修订/最终.json')
        self.assertTrue(bc.check(self.root, '审稿与修订/最终.json')['ok'])

    def test_self_review_empty_identities_scope_and_verdict(self):
        cases = [dict(reviewer=dict(task_id='fixture-w1', agent_id='other')),
                 dict(reviewer=dict(task_id='other', agent_id='fixture-writer2')),
                 dict(reviewer=dict(task_id=' ', agent_id='reader')),
                 dict(reviewer=dict(task_id='reader', agent_id='')),
                 dict(reading_scope='partial'), dict(verdict='revise'),
                 dict(schema_version=True), dict(schema_version=2)]
        for changes in cases:
            with self.subTest(changes=changes):
                self.save_record(dict(self.record, **changes))
                with self.assertRaises(ValueError):
                    bc.check(self.root)

    def test_pending_and_material_integrity_gate_both_commands(self):
        state = nc.load_state(self.root)
        state['pending_operations'] = [dict(kind='fixture-unfinished-materials')]
        self.save_json(nc.STATE, state)
        for operation in (bc.manifest, bc.check):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, 'pending_operations'):
                    operation(self.root)
        state['pending_operations'] = []
        self.save_json(nc.STATE, state)
        (self.root / '人物/索引.md').unlink()
        for operation in (bc.manifest, bc.check):
            with self.assertRaisesRegex(ValueError, 'missing index'):
                operation(self.root)

    def test_concurrent_changes_are_detected(self):
        original = cc.check
        for operation in (bc.manifest, bc.check):
            with self.subTest(operation=operation.__name__):
                def changed(book):
                    result = original(book)
                    with (self.root / '创作约定/写作进度与接续说明.md').open('a', encoding='utf-8') as stream:
                        stream.write('\n并发修改。')
                    return result
                with patch.object(cc, 'check', side_effect=changed):
                    with self.assertRaisesRegex(ValueError, 'book changed during validation'):
                        operation(self.root)

    def test_ordinary_checks_and_export_ignore_missing_or_stale_final_record(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                if stale:
                    self.save_record(dict(self.record, chapters=[]))
                else:
                    (self.root / bc.DEFAULT_RECORD).unlink()
                    nc.index_book(self.root)
                self.assertTrue(cc.check(self.root)['ok'])
                output = Path(self.temp.name) / '普通导出.md'
                nc.export_book(self.root, output)
                self.assertTrue(output.read_text(encoding='utf-8').strip())

    def test_cli_json_success_and_failure(self):
        script = str(Path(bc.__file__))
        before = nc.inventory(self.root)
        for command in ('manifest', 'check'):
            proc = subprocess.run([sys.executable, script, command, '--book', str(self.root)],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)['ok'])
        self.assertEqual(before, nc.inventory(self.root))
        proc = subprocess.run([sys.executable, script, 'check', '--book', str(self.root),
                               '--record', '审稿与修订/缺失.json'], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(json.loads(proc.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
