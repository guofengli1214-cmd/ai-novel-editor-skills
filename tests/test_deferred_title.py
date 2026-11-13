"""Deferred naming uses synthetic author records, never fiction generation."""
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


class DeferredTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / 'library'
        self.decision = dict(
            resolution='deferred', work_id='project-001',
            source_quote='合成作者：书名以后再定，本轮先整理已经给定的材料。',
            scope='本技术项目暂缓定名；不授权补充故事内容')

    def initialize(self):
        return nc.init_book(self.parent, '', work_id='project-001',
                            title_decision=self.decision)

    def direction_request(self, root):
        path = '创作约定/作者约定与授权.md'
        evidence = dict(path=path, sha256=nc.digest(root / path))
        checks = {name: dict(evidence, summary='合成作者约定的实际来源')
                  for name in tc.EDITORIAL_CHECKS['direction']}
        selected = dict(resolution='provided', value='合成的既定范围',
                        source_quote='合成作者：本项按已经给出的具体选择。', scope='本次技术任务')
        for category, topics in tc.DIRECTION_DECISIONS.items():
            checks[category]['decisions'] = {topic: dict(selected) for topic in topics}
        checks['startup_mode']['decision'] = dict(selected)
        checks['author_preferences']['decisions']['title'] = copy.deepcopy(self.decision)
        return dict(stage='S01', task='direction', outputs=[evidence], checks=checks)

    def test_api_keeps_title_unresolved_and_preserves_author_record(self):
        root = self.initialize()
        state = nc.load_state(root)
        self.assertEqual(root.name, 'project-001')
        self.assertEqual(state['title'], '')
        self.assertEqual(state['title_status'], 'unresolved')
        self.assertEqual(state['work_id'], 'project-001')
        self.assertEqual(state['title_deferral'], self.decision)
        self.assertIn(self.decision['source_quote'],
                      (root / '创作约定/作者约定与授权.md').read_text(encoding='utf-8'))
        self.assertEqual(nc.check_book(root), [])

    def test_cli_initializes_neutral_project_without_a_formal_title(self):
        result = subprocess.run(
            [sys.executable, str(Path(nc.__file__)), 'init', '--parent', str(self.parent),
             '--work-id', 'project-001', '--title-decision',
             json.dumps(self.decision, ensure_ascii=False)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(nc.load_state(self.parent / 'project-001')['title'], '')

    def test_deferred_title_does_not_block_already_selected_planning(self):
        root = self.initialize()
        request = self.direction_request(root)
        tc.update(root, dict(stage='S01', task='direction', status='doing'))
        tc.submit(root, request)
        tc.update(root, dict(request, status='done', verification=dict(
            agent_id='fixture-main', source='核对合成的作者选择与暂缓定名记录')))
        evidence = request['outputs'][0]
        tc.update(root, dict(stage='S01', task='concept', status='todo', inputs=[evidence],
                             checks=dict(creative_decisions=dict(
                                 evidence, summary='只处理已经选定的技术范围', items=[],
                                 none_reason='没有新增故事决定'))))
        self.assertTrue(tc.gate(root, 'S01', 'concept')['ok'])

    def test_deferral_does_not_resolve_other_missing_author_choices(self):
        root = self.initialize()
        request = self.direction_request(root)
        request['checks']['startup_mode']['decision']['resolution'] = 'unresolved'
        with self.assertRaisesRegex(ValueError, 'unresolved: startup_mode'):
            tc.submit(root, request)

    def test_invalid_deferrals_fail_before_creating_any_directory(self):
        cases = [dict(self.decision, work_id='other-project'),
                 dict(self.decision, source_quote=''), dict(self.decision, scope=''),
                 dict(self.decision, value='不能偷用的正式或弃用书名'),
                 dict(self.decision, prior_status='retired'),
                 dict(self.decision, resolution='unresolved')]
        for decision in cases:
            with self.subTest(decision=decision):
                with self.assertRaises(ValueError):
                    nc.init_book(self.parent, '', work_id='project-001', title_decision=decision)
                self.assertFalse(self.parent.exists())

    def test_deferral_cannot_bypass_a_named_title_or_other_decisive_choices(self):
        self.assertTrue(tc.title_decision_errors('正式技术名', self.decision))
        self.assertTrue(tc.decision_errors(self.decision, 'character_direction', strict=True))
        self.assertTrue(tc.title_decision_errors('', self.decision))

    def test_known_title_can_have_a_separate_portable_directory_id(self):
        title = '技术名：目录/名称分离'
        decision = dict(resolution='provided', value=title,
                        source_quote='合成作者给定书名：' + title,
                        scope='本技术项目书名', prior_status='proposed')
        root = nc.init_book(self.parent, title, work_id='project-002', title_decision=decision)
        self.assertEqual(root.name, 'project-002')
        self.assertEqual(nc.load_state(root)['title'], title)
        self.assertEqual(nc.check_book(root), [])

    def test_neutral_id_does_not_relax_portable_directory_rules(self):
        for work_id in ('../other', 'CON', 'bad/name', 'ending.'):
            with self.subTest(work_id=work_id):
                with self.assertRaises(ValueError):
                    nc.init_book(self.parent, '', work_id=work_id,
                                 title_decision=dict(self.decision, work_id=work_id))
                self.assertFalse(self.parent.exists())

    def test_invalid_unresolved_state_is_rejected(self):
        root = self.initialize()
        original = nc.load_state(root)
        cases = [dict(original, title='冒充已定'), dict(original, work_id=''),
                 dict(original, title_status='active'), dict(original, title_deferral={})]
        for state in cases:
            with self.subTest(state=state):
                (root / nc.STATE).write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
                with self.assertRaises(ValueError):
                    nc.load_state(root)


if __name__ == '__main__':
    unittest.main()
