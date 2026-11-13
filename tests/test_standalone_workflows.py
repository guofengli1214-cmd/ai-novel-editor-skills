"""Standalone task closure with synthetic materials, never fiction or real author approval."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import novelctl as nc
import taskctl as tc


class StandaloneWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.root = None
        self.verification = dict(agent_id='fixture-main', source='explicit synthetic scope and artifact verification')

    def create(self, workflow='materials', managed=True):
        self.root = nc.init_book(self.parent, '独立任务技术夹具', task_management=managed,
                                 initial_workflow=workflow)
        self.authorization = '创作约定/本轮授权来源.dat'
        self.source = '创作约定/已有技术输入.dat'
        (self.root / self.authorization).write_text(
            '合成授权：本轮只整理或只读评审技术材料，不新增设定、大纲、正文，不采用或定稿。', encoding='utf-8')
        (self.root / self.source).write_text('合成已有材料；不包含小说内容。', encoding='utf-8')
        return self.root

    def evidence(self, path):
        return dict(path=path, sha256=nc.digest(self.root / path))

    def checks(self, kind, report=None):
        source = self.evidence(self.authorization)
        checks = {key: dict(source, summary='本轮仅处理已给技术材料；授权和读取、写入范围已核对。')
                  for key in tc.EDITORIAL_CHECKS.get(kind, ())}
        if kind in tc.CREATIVE_DECISION_TASKS:
            checks['creative_decisions'] = dict(source, summary='本轮独立任务不新增创作选择。',
                items=[], none_reason='仅整理、审查既有技术材料或记录本轮结束。')
        if report:
            for key in checks:
                if key not in ('creative_decisions', 'authorization'):
                    checks[key] = dict(self.evidence(report), summary='已提交实际技术检查、限制和本轮交付依据。')
        return checks

    def complete(self, kind, stage='S01'):
        inputs = [self.evidence(self.authorization), self.evidence(self.source)]
        checks = self.checks(kind)
        tc.update(self.root, dict(stage=stage, task=kind, status='todo', inputs=inputs, checks=checks))
        self.assertTrue(tc.gate(self.root, stage, kind)['ok'])
        tc.update(self.root, dict(stage=stage, task=kind, status='doing'))
        path = '审稿与修订/' + stage + '-' + kind + '-技术交付.md'
        (self.root / path).write_text('技术任务报告；注明实际读取范围、来源、未改稿及本轮结束。', encoding='utf-8')
        tc.submit(self.root, dict(stage=stage, task=kind, outputs=[self.evidence(path)],
                                 checks=self.checks(kind, path)))
        tc.update(self.root, dict(stage=stage, task=kind, status='done', verification=self.verification))

    def finish(self, workflow, stage='S01'):
        for kind in tc.WORKFLOWS[workflow][:-1]:
            self.complete(kind, stage)
        tc.plan(self.root, dict(id=stage, end_reason='本轮限定授权已交付；不继续创作。'))
        self.complete('next_plan', stage)
        result = tc.close_stage(self.root, stage)
        self.assertEqual(result['current_stage'], stage)
        self.assertEqual(tc.load_plan(self.root)['current_task'], '')
        self.assertTrue(tc.check(self.root)['ok'])

    def test_materials_can_close_without_creative_direction_or_concept(self):
        self.create('materials')
        before = nc.digest(self.root / self.source)
        self.finish('materials')
        state = nc.load_state(self.root)
        self.assertEqual(state['chapters'], [])
        self.assertEqual(state['pending_operations'], [])
        self.assertEqual(nc.digest(self.root / self.source), before)
        self.assertNotIn('direction_gate_version', tc.load_plan(self.root)['stages'][0])

    def test_read_only_review_closes_and_can_start_a_later_independent_stage(self):
        self.create('review_only')
        before = nc.digest(self.root / self.source)
        self.finish('review_only')
        tc.plan(self.root, dict(id='S02', title='下一轮只整理已有材料', workflow='materials'))
        self.finish('materials', 'S02')
        self.assertEqual(nc.digest(self.root / self.source), before)
        self.assertEqual(nc.load_state(self.root)['chapters'], [])
        self.assertEqual([stage['status'] for stage in tc.load_plan(self.root)['stages']], ['done', 'done'])

    def test_scope_requires_authorization_and_source_evidence_without_startup_questionnaire(self):
        self.create()
        tc.update(self.root, dict(stage='S01', task='scope', status='doing'))
        path = '审稿与修订/缺授权技术报告.md'
        (self.root / path).write_text('技术交付，尚未绑定本轮授权。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'authorization'):
            tc.submit(self.root, dict(stage='S01', task='scope', outputs=[self.evidence(path)]))
        checks = self.checks('scope', path)
        tc.submit(self.root, dict(stage='S01', task='scope', inputs=[self.evidence(self.authorization)],
                                 outputs=[self.evidence(path)], checks=checks))
        tc.update(self.root, dict(stage='S01', task='scope', status='done', verification=self.verification))
        self.assertEqual(tc.load_plan(self.root)['stages'][0]['tasks'][0]['status'], 'done')

    def test_independent_workflow_cannot_be_used_for_formal_adoption(self):
        self.create('review_only')
        with self.assertRaisesRegex(ValueError, 'task stage must uniquely match'):
            tc.require_adoption(self.root, dict(number=1, task_stage='S01'))
        original = tc.load_plan(self.root)
        for kind in ('concept', 'draft', 'adopt', 'structural_revision', 'storyline_management', 'voice_selection'):
            with self.subTest(kind=kind):
                data = copy.deepcopy(original)
                task = copy.deepcopy(data['stages'][0]['tasks'][0])
                task.update(id='S01.forbidden', kind=kind)
                data['stages'][0]['tasks'].insert(-1, task)
                with self.assertRaisesRegex(ValueError, 'standalone workflow cannot include'):
                    tc.save(self.root, data)
        self.assertEqual(tc.load_plan(self.root), original)

    def extra_task(self, data, task_id='S01.research'):
        task = copy.deepcopy(data['stages'][0]['tasks'][0])
        task.update(id=task_id, kind='research', title='仅收集已有技术材料',
                    depends_on=['S01.scope'])
        data['stages'][0]['tasks'].insert(-1, task)
        return task

    def test_additional_material_research_waits_for_scope_and_can_deliver(self):
        self.create('materials')
        data = tc.load_plan(self.root)
        extra = self.extra_task(data)
        extra['depends_on'] = []
        with self.assertRaisesRegex(ValueError, 'additional tasks must depend'):
            tc.save(self.root, data)
        extra['depends_on'] = ['S01.scope']
        tc.save(self.root, data)
        with self.assertRaisesRegex(ValueError, 'scope: not accepted'):
            tc.gate(self.root, 'S01', extra['id'])
        self.complete('scope')
        self.complete('research')
        self.assertEqual(tc.selected_task(tc.load_plan(self.root)['stages'][0], 'research')['status'], 'done')

    def test_materials_extra_task_cannot_register_draft_outline_or_formal_outputs(self):
        self.create('materials')
        original = tc.load_plan(self.root)
        for path in ('草稿与备选/技术越权.md', '大纲/技术越权.md', '第0001章-技术越权.md'):
            with self.subTest(path=path):
                data = copy.deepcopy(original)
                task = self.extra_task(data)
                task['outputs'] = [dict(path=path, sha256='0' * 64)]
                with self.assertRaisesRegex(ValueError, 'materials outputs cannot write'):
                    tc.save(self.root, data)
                self.assertEqual(tc.load_plan(self.root), original)

    def test_read_only_review_rejects_story_record_outputs_and_live_material_declarations(self):
        self.create('review_only')
        original = tc.load_plan(self.root)
        for path in ('草稿与备选/技术越权.md', '大纲/技术越权.md', '第0001章-技术越权.md',
                     '人物/技术资料.md', '故事线/故事线管理.md'):
            with self.subTest(path=path):
                data = copy.deepcopy(original)
                task = self.extra_task(data)
                task['outputs'] = [dict(path=path, sha256='0' * 64)]
                with self.assertRaisesRegex(ValueError, 'review_only outputs must remain'):
                    tc.save(self.root, data)
        data = copy.deepcopy(original)
        extra = self.extra_task(data)
        extra['outputs'] = [dict(path='审稿与修订/技术报告.md', sha256='0' * 64,
                                material_files=[dict(path='人物/技术资料.md', sha256='0' * 64)])]
        with self.assertRaisesRegex(ValueError, 'review_only outputs must remain'):
            tc.save(self.root, data)
        self.assertEqual(tc.load_plan(self.root), original)

    def route(self, **fields):
        request = dict(id='S01', workflow='materials', reason='作者只授权整理已有材料，初始骨架未开工。',
                       verification=self.verification)
        request.update(fields)
        return tc.plan(self.root, request)

    def test_untouched_initial_planning_can_explicitly_route_and_preserve_history(self):
        self.create('planning')
        original = tc.load_plan(self.root)['stages'][0]
        self.route()
        current = tc.load_plan(self.root)['stages'][0]
        self.assertEqual(current['workflow'], 'materials')
        self.assertEqual(current['workflow_history'][0]['stage'], original)
        self.assertEqual(current['workflow_history'][0]['verification'], self.verification)
        self.assertEqual(tc.load_plan(self.root)['current_task'], 'S01.scope')
        self.finish('materials')

    def test_routing_requires_reason_verification_and_a_pristine_initial_stage(self):
        self.create('planning')
        original = nc.inventory(self.root)
        for fields in (dict(reason=''), dict(verification=None), dict(verification={'agent_id': 'fixture-main'})):
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, 'routing needs'):
                    self.route(**fields)
                self.assertEqual(nc.inventory(self.root), original)
        tc.update(self.root, dict(stage='S01', task='direction', status='doing'))
        started = tc.load_plan(self.root)
        with self.assertRaisesRegex(ValueError, 'untouched initial planning'):
            self.route()
        self.assertEqual(tc.load_plan(self.root), started)

    def test_both_initialization_clis_expose_standalone_modes(self):
        decision = dict(resolution='provided', value='只读技术夹具', source_quote='本轮项目名用只读技术夹具。',
                        scope='本次项目标识', prior_status='proposed')
        process = subprocess.run([sys.executable, str(Path(nc.__file__)), 'init', '--parent', str(self.parent),
                                  '--title', decision['value'], '--title-decision', json.dumps(decision, ensure_ascii=False),
                                  '--workflow', 'review_only'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        book = self.parent / decision['value']
        self.assertEqual(tc.load_plan(book)['stages'][0]['workflow'], 'review_only')
        legacy = nc.init_book(self.parent, '迁入技术夹具', task_management=False)
        process = subprocess.run([sys.executable, str(Path(tc.__file__)), 'init', '--book', str(legacy),
                                  '--workflow', 'materials'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(tc.load_plan(legacy)['stages'][0]['workflow'], 'materials')

    def test_initial_modes_and_result_gate_cannot_disable_required_guards(self):
        with self.assertRaisesRegex(ValueError, 'invalid initial workflow'):
            nc.init_book(self.parent / 'not-created', '技术夹具', initial_workflow='chapter')
        self.assertFalse((self.parent / 'not-created').exists())
        with self.assertRaisesRegex(ValueError, 'standalone workflows require workflow v2'):
            tc.new_stage('S01', '技术阶段', 'materials', workflow_version=1)
        self.create()
        for stage in ('S01', 'S02'):
            request = dict(id=stage, result_gate_version=0)
            if stage == 'S02':
                request.update(title='技术后续', workflow='materials')
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(ValueError, 'result gate'):
                    tc.plan(self.root, request)
        self.assertEqual(tc.load_plan(self.root)['stages'][0]['result_gate_version'], 1)


if __name__ == '__main__':
    unittest.main()
