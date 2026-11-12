"""Mechanical checks using short technical fixtures, never creative samples."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'novel-studio/scripts/prosecheck.py'
SPEC = importlib.util.spec_from_file_location('prosecheck', SCRIPT)
pc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pc)


class ProsecheckTests(unittest.TestCase):
    def codes(self, result):
        return [item['code'] for item in result['findings']]

    def test_counts_exclude_metadata_and_heading_forms(self):
        source = ('<!-- 制作：测试 -->\n# 第一章 检查\n## 第二节\n'
                  '第0001章-检查\n一\u3000检查\n一更\u3000检查\n正文 AB 12。\n')
        result = pc.check_text(source)
        self.assertEqual(result['counts'], {
            'han': 2, 'non_whitespace': 7, 'selected': 2, 'count_by': 'han'})
        self.assertTrue(result['ok'])

    def test_setext_heading_excluded(self):
        result = pc.check_text('检查标题\n=====\n正文\n')
        self.assertEqual(result['counts']['han'], 2)
        self.assertEqual(result['findings'], [])

    def test_normal_prose_not_mistaken_for_heading(self):
        result = pc.check_text('一项检查通过。\n第一个参数有效。\n第二章已经检查。')
        self.assertEqual(result['counts']['han'], 20)

    def test_comments_can_span_lines_without_moving_locations(self):
        source = '<!-- “\n元信息「 -->\n# 标题「\n字段：“缺闭合\n'
        result = pc.check_text(source)
        self.assertEqual(result['findings'], [{
            'severity': 'error', 'code': 'QUOTE_UNCLOSED', 'line': 4, 'column': 4,
            'message': '开引号未闭合；请结合上下文核对。'}])

    def test_inline_comment_preserves_original_columns(self):
        result = pc.check_text('甲<!-- 注 -->乙”')
        finding = result['findings'][0]
        self.assertEqual((finding['line'], finding['column']), (1, 13))
        self.assertEqual(finding['code'], 'QUOTE_UNEXPECTED_CLOSE')
        self.assertEqual(result['counts']['han'], 2)

    def test_crlf_locations_are_original(self):
        result = pc.check_text('# 标题\r\n字段：”\r\n')
        self.assertEqual((result['findings'][0]['line'], result['findings'][0]['column']), (2, 4))

    def test_chinese_double_and_nested_single_are_legal(self):
        result = pc.check_text('提示说：“检查‘键’。”', {'quote_confirmed': True})
        self.assertTrue(result['ok'])
        self.assertEqual(result['findings'], [])

    def test_corner_policy_and_nested_corner_are_legal(self):
        result = pc.check_text('提示说：「检查『键』。」', {
            'quote_style': 'corner', 'quote_confirmed': True})
        self.assertEqual(result['findings'], [])

    def test_legal_nested_different_pair_is_not_outer_style_error(self):
        result = pc.check_text('提示说：“引用「键」。”', {'quote_confirmed': True})
        self.assertTrue(result['ok'])
        self.assertNotIn('QUOTE_STYLE', self.codes(result))

    def test_default_style_is_only_warning(self):
        result = pc.check_text('提示说：「完成。」')
        self.assertTrue(result['ok'])
        self.assertEqual(result['findings'][0]['severity'], 'warning')
        self.assertEqual(result['policy']['quote_style'], 'chinese_double')
        self.assertFalse(result['policy']['quote_confirmed'])

    def test_confirmed_style_and_clear_dialogue_is_error(self):
        result = pc.check_text('提示说：「完成。」', {'quote_confirmed': True})
        self.assertFalse(result['ok'])
        self.assertEqual(result['findings'][0]['code'], 'QUOTE_STYLE')
        self.assertEqual(result['findings'][0]['severity'], 'error')

    def test_dialogue_tag_after_quotation_is_recognized(self):
        result = pc.check_text('「完成。」校验器说。', {'quote_confirmed': True})
        self.assertFalse(result['ok'])
        self.assertEqual(result['findings'][0]['severity'], 'error')

    def test_corner_term_is_not_definitely_dialogue(self):
        result = pc.check_text('术语「参数」用于文档。', {'quote_confirmed': True})
        self.assertTrue(result['ok'])
        self.assertEqual(result['findings'][0]['severity'], 'warning')

    def test_unspecified_disables_style_but_not_pairing(self):
        result = pc.check_text('术语「参数」用于文档。', {'quote_style': 'unspecified'})
        self.assertEqual(result['findings'], [])
        broken = pc.check_text('「字段', {'quote_style': 'unspecified'})
        self.assertFalse(broken['ok'])
        self.assertIn('QUOTE_UNCLOSED', self.codes(broken))

    def test_crossed_quote_pairs_are_errors(self):
        result = pc.check_text('“外层‘内层”’')
        self.assertFalse(result['ok'])
        self.assertIn('QUOTE_CROSSED', self.codes(result))

    def test_repeated_same_quote_nesting_needs_human_review(self):
        result = pc.check_text('“外层“内层”外层”')
        self.assertTrue(result['ok'])
        self.assertIn('QUOTE_NESTING', self.codes(result))

    def test_multi_paragraph_quotation_repeated_opener_is_legal(self):
        source = '提示说：“第一项。\n\n  “第二项。\n“第三项。”'
        result = pc.check_text(source, {'quote_confirmed': True})
        self.assertTrue(result['ok'])
        self.assertEqual(result['findings'], [])

    def test_multi_paragraph_corner_quote_still_checks_style_once(self):
        source = '提示说：「第一项。\r\n「第二项。」'
        result = pc.check_text(source, {'quote_confirmed': True})
        self.assertFalse(result['ok'])
        self.assertEqual(self.codes(result), ['QUOTE_STYLE'])
        allowed = pc.check_text(source, {'quote_style': 'corner', 'quote_confirmed': True})
        self.assertEqual(allowed['findings'], [])

    def test_multi_paragraph_quotation_still_needs_final_close(self):
        result = pc.check_text('提示说：“第一项。\n“第二项。')
        self.assertFalse(result['ok'])
        self.assertEqual(self.codes(result), ['QUOTE_UNCLOSED'])

    def test_repeated_opener_after_new_speech_tag_warns_instead_of_swallowing(self):
        source = '提示甲说：“第一项。\n提示乙说：\n“第二项。”'
        result = pc.check_text(source)
        self.assertTrue(result['ok'])
        self.assertEqual(self.codes(result), ['QUOTE_CONTINUATION'])
        finding = result['findings'][0]
        self.assertEqual((finding['severity'], finding['line'], finding['column']), ('warning', 3, 1))

    def test_repeated_opener_followed_by_speech_tag_warns(self):
        source = '提示甲说：“第一项。\n“第二项。”提示乙说。'
        result = pc.check_text(source)
        self.assertTrue(result['ok'])
        self.assertEqual(self.codes(result), ['QUOTE_CONTINUATION'])
        self.assertEqual(result['findings'][0]['severity'], 'warning')

    def test_same_speaker_tag_on_continuation_is_not_hard_failure(self):
        source = '提示甲说：“第一项。\n提示甲说：\n“第二项。”'
        result = pc.check_text(source)
        self.assertTrue(result['ok'])
        self.assertEqual(self.codes(result), ['QUOTE_CONTINUATION'])
        self.assertTrue(all(item['severity'] == 'warning' for item in result['findings']))

    def test_second_speaker_inline_and_plain_unclosed_still_fail(self):
        for source in ('提示甲说：“第一项。', '提示甲说：“第一项。\n提示乙说：“第二项。”'):
            with self.subTest(source=source):
                result = pc.check_text(source)
                self.assertFalse(result['ok'])
                self.assertIn('QUOTE_UNCLOSED', self.codes(result))

    def test_typographic_english_apostrophe_not_quote_error(self):
        result = pc.check_text('tool’s status: valid.')
        self.assertEqual(result['findings'], [])

    def test_ascii_quotes_are_suspicions_until_confirmed_dialogue(self):
        result = pc.check_text('字段 "key" 有效。')
        self.assertTrue(result['ok'])
        self.assertIn('QUOTE_STYLE', self.codes(result))
        self.assertFalse(pc.check_text('提示说："无效。"', {'quote_confirmed': True})['ok'])

    def test_standard_ellipsis_and_dash_are_not_findings(self):
        result = pc.check_text('参数……结果——有效。')
        self.assertEqual(result['findings'], [])

    def test_punctuation_suspicions_are_warnings(self):
        result = pc.check_text('参数...结果—有效。')
        self.assertTrue(result['ok'])
        self.assertEqual(set(self.codes(result)), {'ELLIPSIS_STYLE', 'DASH_STYLE'})
        self.assertTrue(all(item['severity'] == 'warning' for item in result['findings']))

    def test_length_targets_are_soft(self):
        short = pc.check_text('参数', {'chapter_length': {'target_min': 3}})
        long = pc.check_text('参数有效', {'chapter_length': {'target_max': 3}})
        self.assertTrue(short['ok'])
        self.assertTrue(long['ok'])
        self.assertIn('CHAPTER_TARGET_SHORT', self.codes(short))
        self.assertIn('CHAPTER_TARGET_LONG', self.codes(long))

    def test_hard_max_is_error_at_first_excess_original_position(self):
        result = pc.check_text('# 标题\n甲 乙\n丙', {'chapter_length': {'hard_max': 2}})
        self.assertFalse(result['ok'])
        finding = result['findings'][0]
        self.assertEqual((finding['code'], finding['line'], finding['column']),
                         ('CHAPTER_HARD_MAX', 3, 1))

    def test_non_whitespace_count_can_be_selected(self):
        result = pc.check_text('参数 AB。', {'chapter_length': {
            'count_by': 'non_whitespace', 'hard_max': 4}})
        self.assertEqual(result['counts']['selected'], 5)
        self.assertFalse(result['ok'])

    def test_empty_input_and_bom_have_no_phantom_characters(self):
        result = pc.check_text('\ufeff\n\t ')
        self.assertEqual(result['counts']['non_whitespace'], 0)
        self.assertEqual(result['findings'], [])

    def test_inputs_are_not_mutated(self):
        source = '参数有效。'
        policy = {'chapter_length': {'target_min': 2, 'hard_max': 10}}
        before = copy.deepcopy(policy)
        pc.check_text(source, policy)
        self.assertEqual(policy, before)
        self.assertEqual(source, '参数有效。')

    def test_invalid_policy_is_rejected(self):
        invalid = [[], {'quote_style': 'guess'}, {'quote_confirmed': 'yes'},
                   {'chapter_length': {'count_by': 'words'}},
                   {'chapter_length': {'hard_max': True}},
                   {'chapter_length': {'target_min': 5, 'target_max': 4}},
                   {'chapter_length': {'target_max': 5, 'hard_max': 4}},
                   {'chapter_length': {'hard_max': -1}}, {'typo': 1}]
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                pc.check_text('参数', policy)

    def test_cli_only_reads_source_and_policy_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'input.md'
            source.write_bytes('# 检查\r\n提示说：「完成。」\r\n'.encode('utf-8'))
            policy = root / 'policy.json'
            policy.write_text(json.dumps({'quote_confirmed': True}), encoding='utf-8')
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
            run = subprocess.run([sys.executable, str(SCRIPT), 'check', '--file', str(source),
                                  '--policy', str(policy)], capture_output=True, text=True,
                                 encoding='utf-8')
            self.assertEqual(run.returncode, 1)
            result = json.loads(run.stdout)
            self.assertFalse(result['ok'])
            self.assertEqual(run.stderr, '')
            after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
            self.assertEqual(after, before)

    def test_cli_warnings_do_not_fail_and_bad_policy_is_json_error(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'input.txt'
            source.write_text('参数...有效。', encoding='utf-8')
            run = subprocess.run([sys.executable, str(SCRIPT), 'check', '--file', str(source)],
                                 capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(run.returncode, 0)
            self.assertTrue(json.loads(run.stdout)['ok'])
            policy = root / 'policy.json'
            policy.write_text('{broken', encoding='utf-8')
            run = subprocess.run([sys.executable, str(SCRIPT), 'check', '--file', str(source),
                                  '--policy', str(policy)], capture_output=True, text=True,
                                 encoding='utf-8')
            self.assertEqual(run.returncode, 2)
            self.assertEqual(json.loads(run.stdout)['findings'][0]['code'], 'INPUT_ERROR')

    def test_book_cli_binds_report_to_raw_source_and_policy_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_bytes = '\ufeff# 检查\r\n参数有效。\r\n'.encode('utf-8')
            policy_bytes = b'{"quote_confirmed": false, "chapter_length": {"hard_max": 5}}\n'
            (root / 'input.md').write_bytes(source_bytes)
            (root / 'policy.json').write_bytes(policy_bytes)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            run = subprocess.run([sys.executable, str(SCRIPT), 'check', '--book', str(root),
                                  '--file', 'input.md', '--policy', 'policy.json'],
                                 capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads(run.stdout)
            self.assertEqual(result, {
                'draft_sha256': hashlib.sha256(source_bytes).hexdigest(),
                'policy': {'path': 'policy.json',
                           'sha256': hashlib.sha256(policy_bytes).hexdigest()},
                'result': pc.check_text(source_bytes.decode('utf-8'), json.loads(policy_bytes))})
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_book_cli_requires_policy_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'input.md').write_text('参数有效。', encoding='utf-8')
            (root / 'policy.json').write_text('{}', encoding='utf-8')
            cases = [[], ['--policy', '../policy.json'],
                     ['--policy', str(root / 'policy.json')]]
            for suffix in cases:
                with self.subTest(suffix=suffix):
                    run = subprocess.run([sys.executable, str(SCRIPT), 'check', '--book',
                                          str(root), '--file', 'input.md'] + suffix,
                                         capture_output=True, text=True, encoding='utf-8')
                    self.assertEqual(run.returncode, 2)
                    self.assertEqual(json.loads(run.stdout)['findings'][0]['code'], 'INPUT_ERROR')

    def test_cli_rejects_null_policy_file_instead_of_using_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'input.txt').write_text('参数有效。', encoding='utf-8')
            (root / 'policy.json').write_text('null', encoding='utf-8')
            run = subprocess.run([sys.executable, str(SCRIPT), 'check', '--book', str(root),
                                  '--file', 'input.txt', '--policy', 'policy.json'],
                                 capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(run.returncode, 2)
            self.assertIn('JSON object', json.loads(run.stdout)['findings'][0]['message'])


if __name__ == '__main__':
    unittest.main()
