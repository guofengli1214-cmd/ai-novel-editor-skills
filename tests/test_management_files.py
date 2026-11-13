"""Empty management-record initialization with mechanical fixtures only."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote


SCRIPTS = Path(__file__).resolve().parents[1] / 'novel-studio/scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('management_novelctl', SCRIPTS / 'novelctl.py')
nc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nc)

RECORDS = {
    '线索与悬念/钩子管理.md': '钩子管理子 agent',
    '故事线/故事线管理.md': '故事线管理子 agent',
    '曲线与节奏/故事起伏曲线.md': '起伏曲线管理子 agent',
}


class ManagementRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)

    def decision(self, title):
        return dict(resolution='provided', value=title,
                    source_quote='合成技术夹具明确指定本次初始化目录名。',
                    scope='初始化机械测试，不是真实作者创作决定', prior_status='proposed')

    def init(self, title='管理入口技术夹具', **options):
        return nc.init_book(self.parent, title, title_decision=self.decision(title), **options)

    def assert_records_and_links(self, book):
        main_index = unquote((book / '索引.md').read_text(encoding='utf-8'))
        handoff = unquote((book / '创作约定/写作进度与接续说明.md').read_text(encoding='utf-8'))
        for name, role in RECORDS.items():
            with self.subTest(record=name):
                path = book / name
                self.assertTrue(path.is_file())
                text = path.read_text(encoding='utf-8')
                self.assertIn(role, text)
                self.assertIn('尚未派工分析', text)
                plan = text.split('## 计划\n\n', 1)[1].split('## 实际', 1)[0]
                actual = text.split('## 实际\n\n', 1)[1].split('## 来源与版本', 1)[0]
                source = text.split('## 来源与版本\n\n', 1)[1]
                self.assertTrue(plan.startswith('待建立。'))
                self.assertIn('尚无已定', plan)
                self.assertTrue(actual.startswith('未分析。'))
                self.assertIn('尚无正式正文', actual)
                self.assertTrue(source.startswith('待登记。'))
                self.assertIn('计划与正文实际分别记录', source)
                self.assertNotRegex(text, r'\|.*\|')  # No invented data rows or curve values.
                folder_index = unquote((path.parent / '索引.md').read_text(encoding='utf-8'))
                self.assertIn('](' + path.parent.name + '/索引.md)', main_index)
                self.assertIn('](' + path.name + ')', folder_index)
                self.assertIn('](../' + name + ')', handoff)
        self.assertEqual(nc.check_book(book), [])

    def test_new_book_has_three_empty_records_and_index_handoff_paths(self):
        book = self.init()
        self.assert_records_and_links(book)
        state = nc.load_state(book)
        self.assertEqual(state['chapters'], [])
        self.assertEqual(state['pending_operations'], [])
        self.assertIs(state['task_management'], True)
        agreement = (book / '创作约定/作者约定与授权.md').read_text(encoding='utf-8')
        self.assertIn(self.decision(book.name)['source_quote'], agreement)
        import taskctl
        plan = taskctl.load_plan(book)
        self.assertEqual(plan['current_task'], 'S01.direction')
        self.assertEqual(len(plan['stages']), 1)
        self.assertTrue(all(task['status'] == 'todo' and not task['outputs']
                            for task in plan['stages'][0]['tasks']))
        before = nc.inventory(book)
        self.assertEqual(nc.check_book(book), [])
        self.assertEqual(nc.inventory(book), before)

    def test_cli_authorized_init_creates_management_records(self):
        title = 'CLI管理入口技术夹具'
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / 'novelctl.py'), 'init',
             '--parent', str(self.parent), '--title', title,
             '--title-decision', json.dumps(self.decision(title), ensure_ascii=False)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_records_and_links(self.parent / title)

    def test_existing_book_init_does_not_replace_management_records(self):
        book = self.init()
        path = book / '故事线/故事线管理.md'
        path.write_text('技术夹具：保留现有管理记录，不是故事内容。', encoding='utf-8')
        before = nc.inventory(book)
        with self.assertRaises(FileExistsError):
            self.init()
        self.assertEqual(nc.inventory(book), before)

    def test_each_management_write_failure_removes_only_new_book(self):
        existing = self.init('已有目录技术夹具')
        existing_before = nc.inventory(existing)
        write = nc.atomic_write
        for number, failed_record in enumerate(RECORDS):
            with self.subTest(record=failed_record):
                title = '失败清理技术夹具' + str(number)
                target = self.parent / title
                parent_before = sorted(path.name for path in self.parent.iterdir())

                def fail_record(path, content):
                    if Path(path) == target / failed_record:
                        raise OSError('injected management write failure')
                    return write(path, content)

                with patch.object(nc, 'atomic_write', side_effect=fail_record):
                    with self.assertRaisesRegex(OSError, 'injected management write failure'):
                        self.init(title)
                self.assertFalse(target.exists())
                self.assertEqual(sorted(path.name for path in self.parent.iterdir()), parent_before)
                self.assertEqual(nc.inventory(existing), existing_before)
                self.assert_records_and_links(self.init(title))

    def test_old_book_reads_and_checks_do_not_create_management_files(self):
        book = self.init('旧目录技术夹具', task_management=False)
        # Model the pre-feature layout, including its original handoff links.
        for name in RECORDS:
            shutil.rmtree((book / name).parent)
        handoff = book / '创作约定/写作进度与接续说明.md'
        lines = handoff.read_text(encoding='utf-8').splitlines(keepends=True)
        handoff.write_text(''.join(line for line in lines
                                   if not any('](../' + name + ')' in line for name in RECORDS)),
                           encoding='utf-8')
        nc.index_book(book)
        before = nc.inventory(book)
        self.assertEqual(nc.load_state(book)['chapters'], [])
        self.assertEqual(nc.check_book(book), [])
        self.assertEqual(nc.inventory(book), before)
        for name in RECORDS:
            self.assertFalse((book / name).exists())
            self.assertFalse((book / name).parent.exists())


if __name__ == '__main__':
    unittest.main()
