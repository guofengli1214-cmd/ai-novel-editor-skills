"""Deterministic read-back after writes, using synthetic task evidence only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc
import taskpeek as tp


class TaskPeekTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = nc.init_book(Path(self.temp.name), '中文 回读夹具', task_management=False)
        tc.initialize(self.root, workflow_version=1)
        self.report = '审稿与修订/意见 v1.md'
        (self.root / self.report).write_text('技术夹具报告。', encoding='utf-8')
        self.evidence = dict(path=self.report, sha256=nc.digest(self.root / self.report))
        self.request = dict(stage='S01', task='direction', outputs=[self.evidence])
        nc.index_book(self.root)

    def test_successful_submit_survives_later_wrapper_failure_without_resubmit(self):
        # The failed assertion is outside the commit; reading does not retry it.
        try:
            tc.submit(self.root, self.request)
            raise AssertionError('a faulty wrapper expected done instead of submitted')
        except AssertionError:
            pass
        before = nc.inventory(self.root)
        observed = tp.peek(self.root, tasks=['direction'], verify=True)
        self.assertTrue(observed['ok'], observed)
        self.assertEqual(observed['current_task'], 'S01.direction')
        self.assertEqual(observed['tasks'][0]['status'], 'submitted')
        self.assertEqual(observed['tasks'][0]['outputs'], [self.evidence])
        self.assertEqual(observed['validation']['evidence'][0]['actual_sha256'], self.evidence['sha256'])
        self.assertEqual(before, nc.inventory(self.root))
        again = tp.peek(self.root, tasks=['S01.direction', 'direction'])
        self.assertEqual(len(again['tasks']), 1)
        self.assertEqual(again['tasks'][0]['history'], observed['tasks'][0]['history'])

    def test_uses_authoritative_parser_with_json_examples_outside_markers(self):
        path = self.root / tc.TASK_FILE
        path.write_text('```json\n{"status":"done"}\n```\n' + path.read_text(encoding='utf-8') +
                        '\n```json\n{"status":"blocked"}\n```\n', encoding='utf-8')
        self.assertEqual(tp.peek(self.root)['tasks'][0]['status'], 'todo')
        path.write_text(path.read_text(encoding='utf-8') + '\n' + tc.BEGIN, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'managed markers'):
            tp.peek(self.root)

    def test_checks_and_material_files_are_verified_together(self):
        live = '人物/角色.md'
        (self.root / live).write_text('角色原始记录', encoding='utf-8')
        evidence = dict(self.evidence, material_files=[dict(path=live, sha256=nc.digest(self.root / live))])
        tc.submit(self.root, dict(self.request, outputs=[evidence],
                                 checks={'source_status': dict(self.evidence, summary='核对原文')}))
        nc.index_book(self.root)
        (self.root / live).write_text('角色记录被改动', encoding='utf-8')
        observed = tp.peek(self.root, tasks=['direction'], verify=True)
        self.assertFalse(observed['ok'])
        hashes = observed['validation']['evidence']
        self.assertEqual({item['source'] for item in hashes},
                         {'outputs', 'outputs.material_files', 'checks.source_status'})
        self.assertEqual([item['path'] for item in hashes if not item['ok']], [live])
        self.assertEqual(observed['tasks'][0]['status'], 'submitted')

    def test_missing_or_stale_output_is_reported_without_losing_status(self):
        tc.submit(self.root, self.request)
        for text in ('被手改', None):
            with self.subTest(text=text):
                if text is None:
                    (self.root / self.report).unlink()
                else:
                    (self.root / self.report).write_text(text, encoding='utf-8')
                observed = tp.peek(self.root, tasks=['direction'], verify=True)
                self.assertFalse(observed['ok'])
                self.assertEqual(observed['tasks'][0]['status'], 'submitted')
                self.assertFalse(observed['validation']['evidence'][0]['ok'])
                if text is None:
                    self.assertIsNone(observed['validation']['evidence'][0]['actual_sha256'])

    def test_nested_outline_candidate_hashes_are_checked_and_bad_paths_preserve_read_state(self):
        candidate = '大纲/章目 v2.md'
        (self.root / candidate).write_text('候选章目技术夹具', encoding='utf-8')
        candidate_evidence = dict(path=candidate, sha256=nc.digest(self.root / candidate))
        tc.submit(self.root, dict(self.request, checks={
            'current_version': dict(self.evidence, summary='复核指定候选', candidate=candidate_evidence)}))
        nc.index_book(self.root)
        observed = tp.peek(self.root, tasks=['direction'], verify=True)
        self.assertTrue(observed['ok'], observed)
        candidate_records = [item for item in observed['validation']['evidence']
                             if item['source'] == 'checks.current_version.candidate']
        self.assertEqual(candidate_records[0]['actual_sha256'], candidate_evidence['sha256'])
        (self.root / candidate).write_text('候选被手改', encoding='utf-8')
        observed = tp.peek(self.root, tasks=['direction'], verify=True)
        self.assertTrue(observed['read_ok'])
        self.assertFalse(observed['ok'])
        self.assertEqual([item['path'] for item in observed['validation']['evidence'] if not item['ok']], [candidate])
        tc.submit(self.root, dict(self.request, checks={
            'current_version': dict(self.evidence, summary='非法路径不得读取',
                                    candidate=dict(path='../outside.md', sha256='0' * 64))}))
        observed = tp.peek(self.root, tasks=['direction'], verify=True)
        self.assertTrue(observed['read_ok'])
        self.assertFalse(observed['ok'])
        self.assertEqual(observed['tasks'][0]['status'], 'submitted')
        self.assertTrue(observed['validation']['evidence'][-1]['error'])

    def test_nested_scanner_ignores_narrative_dicts_and_hashes_duplicate_paths_once(self):
        task = dict(inputs=[], outputs=[self.evidence], checks={
            'current_version': dict(self.evidence, summary={'path': '不是文件', 'sha256': '0' * 64},
                                    candidate=dict(self.evidence),
                                    notes=[{'path': '叙述路径'}, {'path': '说明', 'sha256': '非哈希'}])})
        with patch.object(nc, 'digest', wraps=nc.digest) as digest:
            shared_hashes = {}
            records = tp.verify_evidence(self.root, task, shared_hashes)
            records += tp.verify_evidence(self.root, task, shared_hashes)
        self.assertEqual(digest.call_count, 1)
        self.assertEqual({item['source'] for item in records},
                         {'outputs', 'checks.current_version', 'checks.current_version.candidate'})
        self.assertTrue(all(item['ok'] for item in records))

    def test_index_verification_is_readonly_and_can_be_repaired_by_existing_index_tool(self):
        tc.submit(self.root, self.request)
        (self.root / '审稿与修订/未登记.md').write_text('新增技术夹具', encoding='utf-8')
        before = nc.inventory(self.root)
        observed = tp.peek(self.root, verify=True)
        self.assertFalse(observed['ok'])
        self.assertTrue(any('index missing entry' in issue for issue in observed['validation']['book_issues']))
        self.assertEqual(before, nc.inventory(self.root))
        nc.index_book(self.root)
        self.assertTrue(tp.peek(self.root, verify=True)['ok'])

    def test_cli_returns_structured_state_and_exit_codes_with_unicode_space_paths(self):
        tc.submit(self.root, self.request)
        command = [sys.executable, str(Path(tp.__file__)), '--book', str(self.root),
                   '--stage', 'S01', '--task', 'direction', '--verify']
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)['tasks'][0]['status'], 'submitted')
        (self.root / self.report).write_text('变更后的夹具报告', encoding='utf-8')
        run = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)['tasks'][0]['status'], 'submitted')
        run = subprocess.run(command + ['--task', 'missing'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertFalse(json.loads(run.stdout)['ok'])

    def test_unknown_stage_and_ambiguous_or_unknown_task_are_errors(self):
        with self.assertRaisesRegex(ValueError, 'unknown stage'):
            tp.peek(self.root, stage='S99')
        with self.assertRaisesRegex(ValueError, 'exactly one task'):
            tp.peek(self.root, tasks=['missing'])


if __name__ == '__main__':
    unittest.main()
