"""Task gates integrated with real files; all literary/host data are fixtures."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import bookctl as bc
import chapterctl as cc
import novelctl as nc
import taskctl as tc


class TaskIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.book = nc.init_book(self.parent, '任务集成夹具')
        # This class exercises the original v1 contract explicitly. The v2
        # success/failure chain lives in test_editorial_gates.py.
        data = tc.load_plan(self.book)
        data['stages'][0]['workflow_version'] = 1
        data['stages'][0].pop('direction_gate_version', None)
        data['stages'][0].pop('hidden_plot_gate_version', None)
        data['stages'][0].pop('decision_gate_version', None)
        data['stages'][0].pop('execution_gate_version', None)
        data['stages'][0].pop('result_gate_version', None)
        tc.save(self.book, data)
        self.write('草稿与备选/第一章.md', '仅用于机械测试的正文夹具。')
        self.sha = nc.digest(self.book / '草稿与备选/第一章.md')
        self.write('审稿与修订/任务依据.md', '测试夹具：任务完成依据，不是真实文学评价。')
        self.save('审稿与修订/第一章.json', dict(
            schema_version=1, writer_task='fixture-writer-task',
            reviewer=dict(task_id='fixture-review-task', agent_id='fixture-reviewer'),
            draft_sha256=self.sha, coverage=['literary', 'continuity'],
            verdict='pass', issues=[]))
        self.request = dict(
            number=1, target='第0001章.md', draft='草稿与备选/第一章.md',
            expected_sha256=None,
            writer=dict(task_id='fixture-writer-task', agent_id='fixture-writer'),
            reviews=['审稿与修订/第一章.json'],
            authorization=dict(mode='trial', source='explicit test fixture approval',
                               approved_sha256=self.sha),
            change_record='连贯性记录/第一章变化.json')
        nc.index_book(self.book)

    def write(self, name, text):
        (self.book / name).write_text(text, encoding='utf-8')

    def save(self, name, value):
        self.write(name, json.dumps(value, ensure_ascii=False))

    def evidence(self, name):
        return dict(path=name, sha256=nc.digest(self.book / name))

    def done(self, stage, task, **fields):
        request = dict(
            stage=stage, task=task, status='done',
            outputs=[self.evidence('审稿与修订/任务依据.md')],
            verification=dict(agent_id='fixture-main', source='explicit test fixture verification'))
        request.update(fields)
        return tc.update(self.book, request)

    def prepare_chapter(self):
        tc.plan(self.book, dict(id='S02', title='第一章夹具', workflow='chapter', chapter=1, workflow_version=1,
                                compatibility_reason='原v1采用集成回归夹具'))
        self.done('S01', 'direction')
        self.done('S01', 'concept')
        tc.plan(self.book, dict(id='S01', next_stage='S02'))
        self.done('S01', 'next_plan')
        tc.close_stage(self.book, 'S01')
        for task in ('prepare', 'draft', 'first_read', 'review', 'revise'):
            self.done('S02', task)
        self.done('S02', 'recheck',
                  inputs=[self.evidence(self.request['draft'])],
                  outputs=[self.evidence(name) for name in self.request['reviews']])

    def synchronization_record(self):
        self.write('摘要/第一章.md', '测试夹具：第一章摘要。')
        self.write('历史版本/采用记录.md', '测试夹具：采用第一章。')
        self.write('创作约定/交接.md', '测试夹具：第1章已采用，待完成同步。')
        nc.index_book(self.book)
        evidence = dict(
            summaries=['摘要/第一章.md'], history=['历史版本/采用记录.md'],
            indexes=['索引.md'], handoff=['创作约定/交接.md'])
        self.save(self.request['change_record'], dict(
            draft_sha256=self.sha,
            categories={key: 'updated' if key in cc.REQUIRED_UPDATED else 'unchanged'
                        for key in cc.CATEGORIES},
            checked_sources=['第0001章.md'],
            updated_files=[self.evidence(names[0]) for names in evidence.values()],
            category_files=evidence, handoff=dict(path='创作约定/交接.md', chapter=1)))
        nc.index_book(self.book)

    def adopt_task(self, result, operation_id=None):
        self.done('S02', 'adopt', operation_id=operation_id or result['operation_id'],
                  outputs=[self.evidence('第0001章.md')])

    def complete_chapter(self):
        self.prepare_chapter()
        result = cc.adopt(self.book, self.request)
        self.synchronization_record()
        self.adopt_task(result)
        cc.finish(self.book, result)
        self.done('S02', 'sync', outputs=[self.evidence(self.request['change_record'])])
        return result

    def final_record(self):
        self.write('审稿与修订/全书阅读.md', '测试夹具：完整阅读，不是真实阅读认证。')
        self.write('审稿与修订/全书复核.md', '测试夹具：结构处理与最终复核。')
        nc.index_book(self.book)
        record = dict(
            schema_version=1, chapters=bc.manifest(self.book)['chapters'],
            reviewer=dict(task_id='fixture-whole-task', agent_id='fixture-whole-reader'),
            reading_scope='full', verdict='ready', task_stage='S03',
            reading_report=self.evidence('审稿与修订/全书阅读.md'),
            revision_report=self.evidence('审稿与修订/全书复核.md'))
        self.save(bc.DEFAULT_RECORD, record)
        nc.index_book(self.book)
        return record

    def test_default_init_and_cli_enable_task_management(self):
        self.assertIs(nc.load_state(self.book)['task_management'], True)
        self.assertIsInstance(tc.load_plan(self.book), dict)
        handoff = (self.book / '创作约定/写作进度与接续说明.md').read_text(encoding='utf-8')
        self.assertIn('任务计划与验收.md', handoff)
        self.assertEqual(nc.check_book(self.book), [])
        self.assertTrue(cc.check(self.book)['ok'])  # Planned work is not a file error.
        with self.assertRaises(ValueError):
            cc.adopt(self.book, self.request)
        self.assertFalse((self.book / self.request['target']).exists())
        self.assertEqual(nc.load_state(self.book)['pending_operations'], [])
        proc = subprocess.run(
            [sys.executable, str(Path(nc.__file__)), 'init', '--parent', str(self.parent),
             '--title', 'CLI默认任务', '--title-decision', json.dumps(dict(
                 resolution='provided', value='CLI默认任务', source_quote='合成作者明确给定此技术书名。',
                 scope='CLI初始化技术夹具', prior_status='proposed'), ensure_ascii=False)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        created = self.parent / 'CLI默认任务'
        self.assertIs(nc.load_state(created)['task_management'], True)
        self.assertTrue((created / tc.TASK_FILE).is_file())

    def test_missing_ledger_cannot_disable_new_book_gate(self):
        (self.book / tc.TASK_FILE).unlink()
        with self.assertRaises((ValueError, OSError)):
            cc.adopt(self.book, self.request)
        self.assertFalse((self.book / self.request['target']).exists())
        self.assertEqual(nc.load_state(self.book)['pending_operations'], [])

    def test_adoption_selects_stage_and_finish_requires_matching_adopt_task(self):
        self.prepare_chapter()
        original_request = json.loads(json.dumps(self.request))
        result = cc.adopt(self.book, self.request)
        state = nc.load_state(self.book)
        pending = state['pending_operations'][0]
        self.assertEqual(pending['request']['task_stage'], 'S02')
        self.assertEqual(pending['task_stage'], 'S02')
        self.assertEqual(state['chapters'][0]['task_stage'], 'S02')
        self.assertEqual(self.request, original_request)
        self.synchronization_record()
        with self.assertRaises(ValueError):
            cc.finish(self.book, result)
        self.assertTrue(nc.load_state(self.book)['pending_operations'])
        self.adopt_task(result, operation_id='different-operation')
        with self.assertRaises(ValueError):
            cc.finish(self.book, result)
        self.adopt_task(result)
        with self.assertRaises(ValueError):
            self.done('S02', 'sync', outputs=[self.evidence(self.request['change_record'])])
        cc.finish(self.book, result)  # sync task must not be a precondition for itself.
        self.assertEqual(nc.load_state(self.book)['pending_operations'], [])
        self.done('S02', 'sync', outputs=[self.evidence(self.request['change_record'])])
        self.assertTrue(cc.check(self.book)['ok'])

    def test_changed_task_evidence_blocks_adoption_without_writes(self):
        self.prepare_chapter()
        self.write('审稿与修订/任务依据.md', '任务原有验收依据已改变。')
        with self.assertRaises(ValueError):
            cc.adopt(self.book, self.request)
        self.assertFalse((self.book / self.request['target']).exists())
        self.assertEqual(nc.load_state(self.book)['pending_operations'], [])

    def test_legacy_book_migration_enables_gate_without_second_state(self):
        self.book = nc.init_book(self.parent, '旧书夹具', task_management=False)
        self.setUp_legacy_files()
        result = cc.adopt(self.book, self.request)
        self.synchronization_record()
        cc.finish(self.book, result)
        output = self.parent / '旧书工作稿.md'
        nc.export_book(self.book, output)
        self.assertTrue(output.read_text(encoding='utf-8').strip())
        self.assertNotIn('task_management', nc.load_state(self.book))
        tc.initialize(self.book)
        nc.index_book(self.book)
        self.assertNotIn('task_management', nc.load_state(self.book))
        request = dict(self.request, expected_sha256=self.sha)
        with self.assertRaises(ValueError):
            cc.adopt(self.book, request)
        nc.export_book(self.book, output)  # Migration does not block ordinary export.
        self.assertTrue(cc.check(self.book)['ok'])

    def setUp_legacy_files(self):
        self.write(self.request['draft'], '仅用于机械测试的正文夹具。')
        self.write('审稿与修订/任务依据.md', '测试夹具：不是真实文学评价。')
        self.save(self.request['reviews'][0], dict(
            schema_version=1, writer_task=self.request['writer']['task_id'],
            reviewer=dict(task_id='fixture-review-task', agent_id='fixture-reviewer'),
            draft_sha256=self.sha, coverage=['literary', 'continuity'],
            verdict='pass', issues=[]))
        nc.index_book(self.book)

    def test_finalization_needs_whole_tasks_but_manifest_and_export_do_not(self):
        self.complete_chapter()
        tc.plan(self.book, dict(id='S03', title='全书夹具验收', workflow='finalization', workflow_version=1,
                                compatibility_reason='原v1全书定稿集成回归夹具'))
        tc.plan(self.book, dict(id='S02', next_stage='S03'))
        self.done('S02', 'next_plan')
        tc.close_stage(self.book, 'S02')
        record = self.final_record()
        self.assertTrue(bc.manifest(self.book)['ok'])
        with self.assertRaises(ValueError):
            bc.check(self.book)
        self.done('S03', 'whole_read', outputs=[record['reading_report']])
        self.done('S03', 'structural_revision', outputs=[record['revision_report']])
        self.done('S03', 'whole_recheck',
                  inputs=[{key: ch[key] for key in ('path', 'sha256')}
                          for ch in record['chapters']],
                  outputs=[record['reading_report'], record['revision_report']])
        self.assertTrue(bc.check(self.book)['ok'])  # finalize remains todo.
        self.done('S03', 'finalize', outputs=[self.evidence(bc.DEFAULT_RECORD)])
        tc.plan(self.book, dict(id='S03', end_reason='测试夹具：本次计划结束'))
        self.done('S03', 'next_plan')
        tc.close_stage(self.book, 'S03')
        self.assertTrue(bc.check(self.book)['ok'])  # Closing a finished stage keeps evidence valid.
        for record_status in ('missing', 'stale'):
            with self.subTest(record_status=record_status):
                if record_status == 'missing':
                    (self.book / bc.DEFAULT_RECORD).unlink()
                else:
                    self.save(bc.DEFAULT_RECORD, dict(record, chapters=[]))
                nc.index_book(self.book)
                self.assertTrue(cc.check(self.book)['ok'])
                self.assertTrue(bc.manifest(self.book)['ok'])
                output = self.parent / '新书工作稿.md'
                nc.export_book(self.book, output)
                self.assertTrue(output.read_text(encoding='utf-8').strip())

    def test_task_management_flag_rejects_nonboolean_values(self):
        state = nc.load_state(self.book)
        for value in ('true', 1, None, {}):
            with self.subTest(value=value):
                self.save(nc.STATE, dict(state, task_management=value))
                with self.assertRaisesRegex(ValueError, 'task_management must be boolean'):
                    nc.load_state(self.book)
        self.save(nc.STATE, state)


if __name__ == '__main__':
    unittest.main()
