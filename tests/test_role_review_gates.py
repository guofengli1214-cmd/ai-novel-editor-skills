"""Required role-review work uses real ordered technical reports, never novels."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_editorial_gates as fixtures


tc, nc, cc = fixtures.tc, fixtures.nc, fixtures.cc
KINDS = ('role_materials', 'role_perspective', 'role_editorial')


class RoleReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EditorialGateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.source = self.f.ev('审稿与修订/技术依据.md')

    def stage(self, stage_id):
        return next(s for s in tc.load_plan(self.root)['stages'] if s['id'] == stage_id)

    def enter_chapter_review(self):
        tc.plan(self.root, dict(id='S02', title='角色门槛技术阶段', workflow='chapter', chapter=1))
        self.f.done('S01', 'direction')
        self.f.done('S01', 'concept')
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.f.done('S01', 'next_plan')
        tc.close_stage(self.root, 'S01')
        outputs = [self.source, self.f.ev(self.f.policy_path)]
        checks = self.f.checks('prepare', outputs)
        checks['prose_policy'] = dict(self.f.ev(self.f.policy_path), summary='已列技术规则')
        self.f.done('S02', 'prepare', outputs, checks=checks)
        self.f.done('S02', 'draft', [self.f.draft])
        self.f.done('S02', 'first_read')

    def add_role_chain(self, stage_id='S02', before='review', predecessor='S02.first_read'):
        data = tc.load_plan(self.root)
        stage = next(s for s in data['stages'] if s['id'] == stage_id)
        core = tc.selected_task(stage, before)
        tasks, ids = [], []
        dependency = predecessor
        for number, kind in enumerate(KINDS, 1):
            task_id = stage_id + '.R0' + str(number)
            tasks.append(dict(id=task_id, kind=kind, title='中性角色技术任务', role='role-reviewer',
                              status='todo', depends_on=[dependency], inputs=[self.source], outputs=[], checks={},
                              acceptance='提交真实技术报告，信息不足也须说明，不能以不适用跳过',
                              verification=None, reason='', history=[], operation_id=''))
            ids.append(task_id)
            dependency = task_id
        index = stage['tasks'].index(core)
        stage['tasks'][index:index] = tasks
        core['depends_on'].extend(ids)
        data['current_task'] = next(t['id'] for t in stage['tasks'] if t['status'] not in tc.FINISHED)
        tc.save(self.root, data)
        return ids

    def start_and_submit(self, stage_id, task_id):
        self.assertTrue(tc.gate(self.root, stage_id, task_id)['ok'])
        tc.update(self.root, dict(stage=stage_id, task=task_id, status='doing'))
        path = '审稿与修订/' + task_id + '.md'
        (self.root / path).write_text(
            '显式机械夹具：报告当前资料不足及待补字段，不是小说、真实角色代入或文学判断。', encoding='utf-8')
        evidence = self.f.ev(path)
        result = tc.submit(self.root, dict(stage=stage_id, task=task_id, outputs=[evidence]))
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(tc.selected_task(self.stage(stage_id), task_id)['status'], 'submitted')
        return evidence

    def accept(self, stage_id, task_id):
        tc.update(self.root, dict(stage=stage_id, task=task_id, status='done',
                                  verification=dict(agent_id='fixture-main', source='显式技术报告核验')))

    def test_every_role_kind_rejects_not_applicable_through_api_and_cli_without_writes(self):
        self.enter_chapter_review()
        ids = self.add_role_chain()
        for task_id in ids:
            with self.subTest(task=task_id):
                request = dict(stage='S02', task=task_id, status='not_applicable', reason='有理由也不能跳过必需角色工作',
                               verification=dict(agent_id='fixture-main', source='仅为机械回归'))
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'role review tasks cannot be not_applicable'):
                    tc.update(self.root, request)
                request_file = Path(self.f.temp.name) / 'role-update.json'
                request_file.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
                result = subprocess.run([sys.executable, str(Path(tc.__file__)), 'update', '--book', str(self.root),
                                         '--request', str(request_file)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('role review tasks cannot be not_applicable', result.stdout)
                self.assertEqual(nc.inventory(self.root), before)

    def test_hand_edited_not_applicable_role_tasks_fail_structure_and_render(self):
        self.enter_chapter_review()
        ids = self.add_role_chain()
        ledger = self.root / tc.TASK_FILE
        original = ledger.read_bytes()
        for task_id in ids:
            with self.subTest(task=task_id):
                data = tc.load_plan(self.root)
                stage = data['stages'][-1]
                task = tc.selected_task(stage, task_id)
                task.update(status='not_applicable', reason='不允许绕过真实报告',
                            verification=dict(agent_id='fixture-main', source='机械夹具直接编辑'))
                data['current_task'] = next(t['id'] for t in stage['tasks'] if t['status'] not in tc.FINISHED)
                ledger.write_text(tc.managed(data), encoding='utf-8')
                invalid = nc.inventory(self.root)
                try:
                    with self.assertRaisesRegex(ValueError, 'role review tasks cannot be not_applicable'):
                        tc.render(self.root)
                    self.assertFalse(tc.check(self.root)['ok'])
                    self.assertEqual(nc.inventory(self.root), invalid)
                finally:
                    ledger.write_bytes(original)

    def test_ordered_role_reports_require_acceptance_and_current_hash_before_review(self):
        self.enter_chapter_review()
        ids = self.add_role_chain()
        evidence = []
        for index, task_id in enumerate(ids):
            with self.assertRaisesRegex(ValueError, 'not accepted'):
                tc.gate(self.root, 'S02', 'review')
            evidence.append(self.start_and_submit('S02', task_id))
            with self.assertRaisesRegex(ValueError, 'not accepted'):
                tc.gate(self.root, 'S02', 'review')
            if index + 1 < len(ids):
                with self.assertRaisesRegex(ValueError, 'not accepted'):
                    tc.gate(self.root, 'S02', ids[index + 1])
            self.accept('S02', task_id)
        self.assertTrue(tc.gate(self.root, 'S02', 'review')['ok'])
        accepted = copy.deepcopy(tc.load_plan(self.root))
        for report in evidence:
            path = self.root / report['path']
            original = path.read_bytes()
            path.write_bytes(original + b'\nchanged technical report\n')
            try:
                with self.assertRaisesRegex(ValueError, 'stale evidence'):
                    tc.gate(self.root, 'S02', 'review')
                self.assertFalse(tc.check(self.root)['ok'])
            finally:
                path.write_bytes(original)
        self.assertEqual(tc.load_plan(self.root), accepted)
        self.assertTrue(tc.check(self.root)['ok'])

    def test_missing_report_cannot_finish_role_work_but_limited_report_can_be_accepted(self):
        self.enter_chapter_review()
        task_id = self.add_role_chain()[0]
        tc.update(self.root, dict(stage='S02', task=task_id, status='doing'))
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'submitted task needs output evidence'):
            tc.submit(self.root, dict(stage='S02', task=task_id, outputs=[]))
        self.assertEqual(nc.inventory(self.root), before)
        with self.assertRaisesRegex(ValueError, 'missing output evidence'):
            tc.update(self.root, dict(stage='S02', task=task_id, status='done',
                                      verification=dict(agent_id='fixture-main', source='不能替代报告的核验文字')))
        self.assertEqual(nc.inventory(self.root), before)
        evidence = self.start_and_submit('S02', task_id)
        self.accept('S02', task_id)
        task = tc.selected_task(self.stage('S02'), task_id)
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['outputs'], [evidence])

    def test_finalization_structural_revision_waits_for_role_reports(self):
        self.f.chapter_ready()
        operation = cc.adopt(self.root, self.f.req)
        self.f.synchronize(operation)
        tc.plan(self.root, dict(id='F', title='角色定稿技术阶段', workflow='finalization'))
        tc.plan(self.root, dict(id='S02', next_stage='F'))
        self.f.done('S02', 'next_plan')
        tc.close_stage(self.root, 'S02')
        self.f.done('F', 'whole_read')
        ids = self.add_role_chain('F', 'structural_revision', 'F.whole_read')
        preflight = dict(self.source, summary='仅处理既定技术问题', items=[], none_reason='没有新增创作选择')
        tc.update(self.root, dict(stage='F', task='structural_revision', status='todo', inputs=[self.source],
                                  checks=dict(creative_decisions=preflight)))
        for task_id in ids:
            with self.assertRaisesRegex(ValueError, 'not accepted'):
                tc.gate(self.root, 'F', 'structural_revision')
            evidence = self.start_and_submit('F', task_id)
            self.accept('F', task_id)
        self.assertTrue(tc.gate(self.root, 'F', 'structural_revision')['ok'])
        (self.root / evidence['path']).write_text('改变后的技术编辑结论，旧验收必须失效。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale evidence'):
            tc.gate(self.root, 'F', 'structural_revision')


if __name__ == '__main__':
    unittest.main()
