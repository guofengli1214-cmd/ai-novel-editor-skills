"""Optional hidden-plot setup with mechanical author-decision fixtures only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

SCRIPTS = Path(__file__).resolve().parents[1] / 'novel-studio/scripts'
sys.path.insert(0, str(SCRIPTS))
import novelctl as nc


class HiddenPlotFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)

    def title_decision(self, title):
        return dict(resolution='provided', value=title, prior_status='proposed',
                    source_quote='合成技术夹具提供目录标题。', scope='仅初始化机械验证')

    def hidden_decision(self, value='enabled', resolution='selected'):
        return dict(resolution=resolution, value=value,
                    question_quote='合成技术提问：本项目需要暗线吗？',
                    source_quote='合成技术答复：' + ('需要。' if value == 'enabled' else '不需要。'),
                    scope='仅本项目暗线管理的启用选择，不含情节授权')

    def init(self, title='暗线管理技术夹具', **options):
        return nc.init_book(self.parent, title, title_decision=self.title_decision(title), **options)

    def assert_hidden_entry(self, book, expected):
        hidden = book / nc.HIDDEN_PLOT_FILE
        self.assertEqual(hidden.exists(), expected)
        main = unquote((book / '索引.md').read_text(encoding='utf-8'))
        folder = unquote((book / '故事线/索引.md').read_text(encoding='utf-8'))
        handoff = unquote((book / '创作约定/写作进度与接续说明.md').read_text(encoding='utf-8'))
        self.assertIn('](故事线/索引.md)', main)
        self.assertEqual('](暗线剧情管理.md)' in folder, expected)
        self.assertEqual('](../' + nc.HIDDEN_PLOT_FILE + ')' in handoff, expected)
        self.assertEqual(nc.check_book(book), [])

    def test_enabled_creates_only_empty_optional_record_and_links(self):
        decision = self.hidden_decision()
        original = copy.deepcopy(decision)
        book = self.init(hidden_plot_decision=decision)
        self.assert_hidden_entry(book, True)
        self.assertEqual((book / nc.HIDDEN_PLOT_FILE).read_text(encoding='utf-8'), nc.HIDDEN_PLOT_TEMPLATE)
        self.assertNotIn(nc.HIDDEN_PLOT_FILE, nc.MANAGEMENT_FILES)
        self.assertEqual(len(nc.MANAGEMENT_FILES), 3)
        for name in nc.MANAGEMENT_FILES:
            self.assertTrue((book / name).is_file())
        agreement = (book / '创作约定/作者约定与授权.md').read_text(encoding='utf-8')
        self.assertIn(json.dumps(decision, ensure_ascii=False, indent=2), agreement)
        title_record = json.loads(agreement.split('```json\n', 1)[1].split('\n```', 1)[0])
        self.assertEqual(title_record, dict(title=book.name, decision=self.title_decision(book.name)))
        self.assertFalse(any('hidden_plot' in key for key in nc.load_state(book)))
        self.assertEqual(decision, original)

    def test_disabled_records_choice_without_creating_optional_file(self):
        decision = self.hidden_decision('disabled')
        book = self.init(hidden_plot_decision=decision)
        self.assert_hidden_entry(book, False)
        self.assertIn(json.dumps(decision, ensure_ascii=False, indent=2),
                      (book / '创作约定/作者约定与授权.md').read_text(encoding='utf-8'))
        self.assertIn('已禁用暗线管理', (book / '创作约定/写作进度与接续说明.md').read_text(encoding='utf-8'))

    def test_omitted_choice_stays_pending_without_optional_file(self):
        book = self.init()
        self.assert_hidden_entry(book, False)
        self.assertIn('待询问作者', (book / '创作约定/写作进度与接续说明.md').read_text(encoding='utf-8'))
        self.assertNotIn('初始化暗线决定', (book / '创作约定/作者约定与授权.md').read_text(encoding='utf-8'))

    def test_hidden_choice_can_be_preserved_without_low_level_title_record(self):
        decision = self.hidden_decision(resolution='provided')
        book = nc.init_book(self.parent, '兼容底层API技术夹具', hidden_plot_decision=decision)
        self.assert_hidden_entry(book, True)
        agreement = (book / '创作约定/作者约定与授权.md').read_text(encoding='utf-8')
        self.assertIn(json.dumps(decision, ensure_ascii=False, indent=2), agreement)
        self.assertNotIn('初始化书名决定', agreement)

    def test_invalid_choices_fail_before_creating_parent(self):
        cases = [[], 'enabled', False, {},
                 self.hidden_decision(value='yes'), self.hidden_decision(value=True),
                 dict(self.hidden_decision(), resolution='unresolved'),
                 dict(self.hidden_decision(), source_quote=''),
                 dict(self.hidden_decision(), question_quote=''),
                 dict(self.hidden_decision(), resolution='delegated')]
        for number, decision in enumerate(cases):
            with self.subTest(decision=decision):
                parent = self.parent / str(number) / 'new-library'
                with self.assertRaises(ValueError):
                    nc.init_book(parent, '无效决定技术夹具', hidden_plot_decision=decision)
                self.assertFalse(parent.exists())
                self.assertFalse(parent.parent.exists())

    def test_explicit_delegation_uses_existing_complete_opt_out_contract(self):
        decision = self.hidden_decision(resolution='delegated')
        decision['opt_out'] = dict(question_quote='合成技术提问：本轮全部按推荐且不再问？',
                                   answer_quote='合成技术答复：本轮全部按推荐且不再问。',
                                   scope=decision['scope'], all_recommendations=True,
                                   no_further_questions=True)
        book = self.init(hidden_plot_decision=decision)
        self.assert_hidden_entry(book, True)

    def test_cli_accepts_inline_file_and_at_file_decisions(self):
        for number, mode in enumerate(('inline-enabled', 'file-disabled', 'at-enabled', 'omitted')):
            with self.subTest(mode=mode):
                title = '暗线CLI技术夹具' + str(number)
                command = [sys.executable, str(SCRIPTS / 'novelctl.py'), 'init',
                           '--parent', str(self.parent), '--title', title,
                           '--title-decision', json.dumps(self.title_decision(title), ensure_ascii=False)]
                if mode != 'omitted':
                    decision = self.hidden_decision('disabled' if 'disabled' in mode else 'enabled')
                    value = json.dumps(decision, ensure_ascii=False)
                    if not mode.startswith('inline'):
                        filename = self.parent / ('choice-' + str(number) + '.json')
                        filename.write_text(value, encoding='utf-8-sig')
                        value = ('@' if mode.startswith('at-') else '') + str(filename)
                    command += ['--hidden-plot-decision', value]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assert_hidden_entry(self.parent / title, 'enabled' in mode)

    def test_cli_rejects_invalid_decision_without_creating_parent(self):
        for number, raw in enumerate(('[]', '{broken', json.dumps(self.hidden_decision('yes')))):
            with self.subTest(raw=raw):
                parent = self.parent / ('invalid-' + str(number))
                title = '无效CLI技术夹具'
                result = subprocess.run([sys.executable, str(SCRIPTS / 'novelctl.py'), 'init',
                                         '--parent', str(parent), '--title', title,
                                         '--title-decision', json.dumps(self.title_decision(title)),
                                         '--hidden-plot-decision', raw], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse(parent.exists())

    def test_optional_record_write_failure_rolls_back_only_new_book(self):
        existing = self.init('已有作品技术夹具')
        before = nc.inventory(existing)
        title = '失败回滚技术夹具'
        target = self.parent / title
        write = nc.atomic_write

        def fail_hidden(path, content):
            if Path(path) == target / nc.HIDDEN_PLOT_FILE:
                raise OSError('injected optional record failure')
            return write(path, content)

        with patch.object(nc, 'atomic_write', side_effect=fail_hidden):
            with self.assertRaisesRegex(OSError, 'injected optional record failure'):
                self.init(title, hidden_plot_decision=self.hidden_decision())
        self.assertFalse(target.exists())
        self.assertEqual(nc.inventory(existing), before)
        self.assert_hidden_entry(self.init(title, hidden_plot_decision=self.hidden_decision()), True)

    def test_init_never_overwrites_existing_hidden_record(self):
        book = self.init(hidden_plot_decision=self.hidden_decision())
        (book / nc.HIDDEN_PLOT_FILE).write_text('合成已有记录，须原样保留。', encoding='utf-8')
        before = nc.inventory(book)
        with self.assertRaises(FileExistsError):
            self.init(hidden_plot_decision=self.hidden_decision('disabled'))
        self.assertEqual(nc.inventory(book), before)

    def test_read_and_check_of_old_layout_do_not_create_optional_record(self):
        book = self.init(task_management=False)
        before = nc.inventory(book)
        self.assertEqual(nc.load_state(book)['chapters'], [])
        self.assertEqual(nc.check_book(book), [])
        self.assertEqual(nc.inventory(book), before)
        self.assertFalse((book / nc.HIDDEN_PLOT_FILE).exists())


if __name__ == '__main__':
    unittest.main()
