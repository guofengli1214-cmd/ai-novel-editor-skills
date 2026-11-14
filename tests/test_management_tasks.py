"""Specialist task gates using explicit mechanical fixtures, never agent reading."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_editorial_gates as fixtures


tc, nc, cc = fixtures.tc, fixtures.nc, fixtures.cc
KINDS = ('hidden_plot_management', 'storyline_management', 'hook_management', 'curve_direction')
ACTIVE_FILES = ('故事线/暗线剧情管理.md', '故事线/故事线管理.md', '线索与悬念/钩子管理.md', '曲线与节奏/故事起伏曲线.md')


class ManagementTaskTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.EditorialGateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.hidden_plot_choice = 'enabled'
        self.source = self.fixture.ev('审稿与修订/技术依据.md')

    def enter_chapter(self):
        f = self.fixture
        tc.plan(self.root, dict(id='S02', title='专项任务机械阶段', workflow='chapter', chapter=1))
        f.done('S01', 'direction')
        f.done('S01', 'concept')
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        f.done('S01', 'next_plan')
        tc.close_stage(self.root, 'S01')
        tc.update(self.root, dict(stage='S02', task='prepare', status='todo', inputs=[self.source],
                                  checks=dict(creative_decisions=self.creative_check())))

    def creative_check(self, items=None):
        check = dict(self.source, summary='显式机械夹具决定依据', items=[] if items is None else items)
        if not check['items']:
            check['none_reason'] = '仅同步和核对既有技术约定，没有新增创作选择。'
        return check

    def task(self, kind, task_id, depends=()):
        return dict(id=task_id, kind=kind, title='专项机械夹具 ' + task_id, role='specialist',
                    status='todo', depends_on=list(depends), inputs=[], outputs=[], checks={},
                    acceptance='验证真实脚本依赖及文件证据，不认证实际阅读或创作',
                    verification=None, reason='', history=[], operation_id='')

    def add_tasks(self, before='prepare', predecessor=None, curve_dependencies=True):
        data = tc.load_plan(self.root)
        stage = next(s for s in data['stages'] if s['id'] == 'S02')
        ids = ['S02.' + before + '.' + kind for kind in KINDS]
        deps = [] if predecessor is None else [predecessor]
        tasks = [self.task(kind, task_id, deps) for kind, task_id in zip(KINDS, ids)]
        if curve_dependencies:
            tasks[-1]['depends_on'] += ids[:-1]
        core = tc.selected_task(stage, before)
        core['depends_on'] += ids
        index = stage['tasks'].index(core)
        stage['tasks'][index:index] = tasks
        data['current_task'] = next(t['id'] for t in stage['tasks'] if t['status'] not in tc.FINISHED)
        tc.save(self.root, data)
        return ids

    def hidden_choice(self):
        return dict(id='fixture-hidden-choice', topic='hidden_plot', record_id='fixture-hidden-author-decision',
                    resolution='provided', value='enabled', source_quote='合成作者已主动要求暗线管理。',
                    scope='本次专项管理机械测试')

    def configure(self, task_id, check=None):
        if check is None:
            check = self.creative_check([self.hidden_choice()] if 'hidden_plot_management' in task_id else None)
        tc.update(self.root, dict(stage='S02', task=task_id, status='todo', inputs=[self.source],
                                  checks=dict(creative_decisions=check)))

    def accept(self, task_id, approved=False):
        items = None
        if approved:
            items = [dict(id='fixture-choice', topic='management_direction', record_id='fixture-author-decision',
                          resolution='selected', value='合成已选技术选项',
                          question_quote='合成技术提问：选择已有选项A还是B？',
                          source_quote='合成技术答复：选择A。', scope='本次机械专项测试')]
        if 'hidden_plot_management' in task_id:
            items = (items or []) + [self.hidden_choice()]
        self.configure(task_id, self.creative_check(items))
        tc.gate(self.root, 'S02', task_id)
        tc.update(self.root, dict(stage='S02', task=task_id, status='doing'))
        path = '历史版本/专项管理/' + task_id + '.md'
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('仅供机械测试的不可变专项依据；不是小说、真实阅读或实际创作决定。', encoding='utf-8')
        evidence = self.fixture.ev(path)
        result = tc.submit(self.root, dict(stage='S02', task=task_id, outputs=[evidence]))
        self.assertEqual(result['status'], 'submitted')
        tc.update(self.root, dict(stage='S02', task=task_id, status='done',
                                  verification=dict(agent_id='fixture-main', source='显式技术证据核验')))
        return evidence

    def test_initialization_does_not_insert_specialist_tasks_automatically(self):
        before = nc.inventory(self.root)
        self.assertFalse(any(t['kind'] in KINDS for s in tc.load_plan(self.root)['stages'] for t in s['tasks']))
        self.assertEqual(nc.inventory(self.root), before)
        self.enter_chapter()
        self.assertFalse(any(t['kind'] in KINDS for s in tc.load_plan(self.root)['stages'] for t in s['tasks']))

    def test_each_specialist_kind_rejects_not_applicable_in_update_and_cli(self):
        self.enter_chapter()
        ids = self.add_tasks(curve_dependencies=False)
        for task_id in ids:
            with self.subTest(task=task_id):
                request = dict(stage='S02', task=task_id, status='not_applicable',
                               reason='技术夹具不能用此理由跳过已列任务',
                               verification=dict(agent_id='fixture-main', source='显式技术核对'))
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'management tasks cannot be not_applicable'):
                    tc.update(self.root, request)
                request_file = Path(self.fixture.temp.name) / 'update-request.json'
                request_file.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
                result = subprocess.run([sys.executable, str(Path(tc.__file__)), 'update', '--book', str(self.root),
                                         '--request', str(request_file)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('management tasks cannot be not_applicable', result.stdout)
                self.assertEqual(nc.inventory(self.root), before)

    def test_structure_rejects_specialist_not_applicable_but_other_extra_kinds_remain_optional(self):
        self.enter_chapter()
        ids = self.add_tasks(curve_dependencies=False)
        original = (self.root / tc.TASK_FILE).read_bytes()
        for task_id in ids:
            with self.subTest(task=task_id):
                data = tc.load_plan(self.root)
                stage = data['stages'][-1]
                tc.selected_task(stage, task_id).update(status='not_applicable', reason='合成跳过',
                                                       verification=dict(agent_id='fixture-main', source='技术核对'))
                data['current_task'] = next(t['id'] for t in stage['tasks'] if t['status'] not in tc.FINISHED)
                (self.root / tc.TASK_FILE).write_text(tc.managed(data), encoding='utf-8')
                try:
                    with self.assertRaisesRegex(ValueError, 'management tasks cannot be not_applicable'):
                        tc.render(self.root)
                    self.assertFalse(tc.check(self.root)['ok'])
                finally:
                    (self.root / tc.TASK_FILE).write_bytes(original)
        data = tc.load_plan(self.root)
        stage = data['stages'][-1]
        stage['tasks'].insert(-1, self.task('conditional_research', 'S02.existing_optional_research'))
        tc.save(self.root, data)
        tc.update(self.root, dict(stage='S02', task='S02.existing_optional_research', status='not_applicable',
                                  reason='既有非专项扩展行为保持', verification=dict(agent_id='fixture-main', source='技术核对')))

    def test_each_kind_requires_resolved_registered_decisions_before_writing(self):
        self.enter_chapter()
        ids = self.add_tasks(curve_dependencies=False)
        for task_id in ids:
            with self.subTest(task=task_id):
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'creative_decisions'):
                    tc.gate(self.root, 'S02', task_id)
                self.assertEqual(nc.inventory(self.root), before)
                unresolved = dict(id='fixture-pending', topic='management_direction', record_id='fixture-record',
                                  resolution='unresolved', value='待选择的合成技术项',
                                  source_quote='合成未答记录', scope='本次机械测试')
                self.configure(task_id, self.creative_check([unresolved]))
                with self.assertRaisesRegex(ValueError, 'author decision unresolved'):
                    tc.gate(self.root, 'S02', task_id)
                with self.assertRaisesRegex(ValueError, 'author decision unresolved'):
                    tc.update(self.root, dict(stage='S02', task=task_id, status='doing'))
                malformed = self.creative_check()
                del malformed['none_reason']
                self.configure(task_id, malformed)
                with self.assertRaisesRegex(ValueError, 'none_reason'):
                    tc.gate(self.root, 'S02', task_id)
                self.configure(task_id)
                self.assertTrue(tc.gate(self.root, 'S02', task_id)['ok'])

    def test_hidden_management_needs_one_enabled_choice_for_start_submission_and_acceptance(self):
        self.fixture.hidden_plot_choice = 'disabled'
        self.enter_chapter()
        task_id = self.add_tasks(curve_dependencies=False)[0]
        enabled = self.hidden_choice()
        cases = [[], [dict(enabled, value='disabled')],
                 [dict(enabled, resolution='unresolved')],
                 [dict(enabled, topic='unrelated')],
                 [enabled, dict(enabled, id='other')]]
        for items in cases:
            with self.subTest(items=items):
                check = self.creative_check(items)
                self.configure(task_id, check)
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'hidden_plot'):
                    tc.gate(self.root, 'S02', task_id)
                for status in ('doing', 'submitted', 'done'):
                    with self.assertRaisesRegex(ValueError, 'hidden_plot'):
                        tc.update(self.root, dict(stage='S02', task=task_id, status=status,
                            outputs=[] if status == 'doing' else [self.source],
                            verification=dict(agent_id='fixture-main', source='不能代替实际启用决定')))
                self.assertEqual(nc.inventory(self.root), before)
        # A real later choice can supersede the first disabled choice. The host
        # must verify that quoted scope and provenance, rather than infer consent.
        self.configure(task_id)
        self.assertTrue(tc.gate(self.root, 'S02', task_id)['ok'])

    def test_hidden_management_enabling_choice_also_applies_to_legacy_stage(self):
        self.enter_chapter()
        task_id = self.add_tasks(curve_dependencies=False)[0]
        data = tc.load_plan(self.root)
        stage = data['stages'][-1]
        stage['workflow_version'] = 1
        for key in ('decision_gate_version', 'execution_gate_version', 'result_gate_version'):
            stage.pop(key)
        tc.save(self.root, data)
        self.configure(task_id, self.creative_check())
        before = nc.inventory(self.root)
        for status in ('doing', 'submitted', 'done'):
            with self.assertRaisesRegex(ValueError, 'hidden_plot'):
                tc.update(self.root, dict(stage='S02', task=task_id, status=status,
                    outputs=[] if status == 'doing' else [self.source],
                    verification=dict(agent_id='fixture-main', source='旧阶段也不能代替启用依据')))
        with self.assertRaisesRegex(ValueError, 'hidden_plot'):
            tc.gate(self.root, 'S02', task_id)
        self.assertEqual(nc.inventory(self.root), before)
        self.configure(task_id)
        tc.gate(self.root, 'S02', task_id)
        self.assertEqual(tc.update(self.root, dict(stage='S02', task=task_id, status='doing'))['status'], 'doing')

    def test_prepare_waits_for_all_approved_specialist_outputs_and_rejects_stale_evidence(self):
        self.enter_chapter()
        ids = self.add_tasks()
        with self.assertRaisesRegex(ValueError, 'not accepted'):
            tc.gate(self.root, 'S02', 'prepare')
        outputs = []
        for index, task_id in enumerate(ids):
            outputs.append(self.accept(task_id, approved=True))
            if index < len(ids) - 1:
                with self.assertRaises(ValueError):
                    tc.gate(self.root, 'S02', 'prepare')
        self.assertTrue(tc.gate(self.root, 'S02', 'prepare')['ok'])
        target = self.root / outputs[0]['path']
        target.write_text('机械夹具：历史依据发生真实变化。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale evidence'):
            tc.gate(self.root, 'S02', 'prepare')

    def test_sync_waits_for_all_adopted_version_updates_and_their_current_evidence(self):
        self.fixture.chapter_ready()
        operation = cc.adopt(self.root, self.fixture.req)
        self.fixture.done('S02', 'adopt', [self.fixture.ev(self.fixture.req['target'])],
                          operation_id=operation['operation_id'])
        ids = self.add_tasks('sync', 'S02.adopt')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S02', 'sync')
        outputs = [self.accept(task_id) for task_id in ids]
        self.assertTrue(tc.gate(self.root, 'S02', 'sync')['ok'])
        self.assertTrue(nc.load_state(self.root)['pending_operations'])  # No premature finish or sync acceptance.
        (self.root / outputs[-1]['path']).write_text('机械夹具：已登记更新依据变化。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale evidence'):
            tc.gate(self.root, 'S02', 'sync')

    def test_updating_active_management_files_keeps_snapshot_bound_acceptance_valid(self):
        self.enter_chapter()
        ids = self.add_tasks()
        records = [self.accept(task_id) for task_id in ids]
        accepted = copy.deepcopy(tc.load_plan(self.root))
        data = tc.load_plan(self.root)
        stage = data['stages'][-1]
        core = tc.selected_task(stage, 'prepare')
        updates = ['S02.update.' + kind for kind in KINDS]
        for kind, task_id, predecessor in zip(KINDS, updates, ids):
            stage['tasks'].insert(stage['tasks'].index(core), self.task(kind, task_id, [predecessor]))
        core['depends_on'] += updates
        data['current_task'] = updates[0]
        tc.save(self.root, data)
        for name, task_id in zip(ACTIVE_FILES, updates):
            self.configure(task_id)
            tc.gate(self.root, 'S02', task_id)
            tc.update(self.root, dict(stage='S02', task=task_id, status='doing'))
            text = '技术夹具：活动入口继续维护，历史依据保持原字节。'
            (self.root / name).write_text(text, encoding='utf-8')
            snapshot = '历史版本/专项管理/' + task_id + '.md'
            (self.root / snapshot).write_text(text, encoding='utf-8')
            output = dict(self.fixture.ev(snapshot), material_files=[self.fixture.ev(name)])
            tc.submit(self.root, dict(stage='S02', task=task_id, outputs=[output]))
            tc.update(self.root, dict(stage='S02', task=task_id, status='done',
                                      verification=dict(agent_id='fixture-main', source='实际技术资料更新核验')))
        nc.index_book(self.root)
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertTrue(tc.gate(self.root, 'S02', 'prepare')['ok'])
        current = tc.load_plan(self.root)['stages'][-1]
        for task_id in ids:
            self.assertEqual(tc.selected_task(current, task_id), tc.selected_task(accepted['stages'][-1], task_id))
        self.assertTrue(all(nc.digest(self.root / item['path']) == item['sha256'] for item in records))
        self.assertEqual(nc.check_book(self.root), [])
        (self.root / ACTIVE_FILES[0]).write_text('未登记的修改。', encoding='utf-8')
        self.assertFalse(tc.check(self.root)['ok'])
        self.assertEqual(tc.check(self.root)['stale_tasks'], [])


if __name__ == '__main__':
    unittest.main()
