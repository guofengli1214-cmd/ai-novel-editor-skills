"""Neutral review dispatch and revision rounds with mechanical fixtures only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_editorial_gates as fixtures


tc, nc = fixtures.tc, fixtures.nc


class RoleReviewRoundTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.EditorialGateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.temp_root = Path(self.f.temp.name).resolve()
        self.source = self.f.ev('审稿与修订/技术依据.md')

    def enter_chapter(self):
        tc.plan(self.root, dict(id='S02', title='角色轮次机械检查', workflow='chapter', chapter=1))
        self.f.done('S01', 'direction')
        self.f.done('S01', 'concept')
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.f.done('S01', 'next_plan')
        tc.close_stage(self.root, 'S01')
        outputs = [self.source, self.f.ev(self.f.policy_path)]
        checks = self.f.checks('prepare', outputs)
        checks['prose_policy'] = dict(outputs[1], summary='已有机械规范技术夹具')
        self.f.done('S02', 'prepare', outputs, checks=checks)
        self.f.done('S02', 'draft', [self.f.draft])
        self.f.done('S02', 'first_read')

    def data_and_stage(self):
        data = tc.load_plan(self.root)
        stage = next(s for s in data['stages'] if s['id'] == 'S02')
        return data, stage

    def task(self, task_id):
        return tc.selected_task(self.data_and_stage()[1], task_id)

    def insert_roles(self, prefix, before, predecessor):
        data, stage = self.data_and_stage()
        ids = [prefix + '.' + suffix for suffix in ('M01', 'A01', 'A02')]
        tasks = []
        for index, task_id in enumerate(ids):
            tasks.append(dict(
                id=task_id, kind='role_materials' if index == 0 else 'role_perspective',
                title='中性角色技术任务', role='materials' if index == 0 else 'isolated-character-reader',
                status='todo', depends_on=[predecessor] if index == 0 else [ids[0]],
                inputs=[], outputs=[], checks={}, acceptance='只验证机械输入与报告，不认证实际代入',
                verification=None, reason='', history=[], operation_id=''))
        core = tc.selected_task(stage, before)
        stage['tasks'][stage['tasks'].index(core):stage['tasks'].index(core)] = tasks
        core['depends_on'] += ids[1:]
        data['current_task'] = next(t['id'] for t in stage['tasks'] if t['status'] not in tc.FINISHED)
        tc.save(self.root, data)
        return ids

    def complete(self, task_id, path, inputs=()):
        task = self.task(task_id)
        combined = list(inputs)
        if self.source not in combined:
            combined.append(self.source)
        preflight = {}
        if task['kind'] in tc.CREATIVE_DECISION_TASKS:
            preflight['creative_decisions'] = dict(
                self.source, summary='机械夹具原约定', items=[],
                none_reason='本次只验证技术任务和既有决定，不生成任何创作选择。')
        tc.update(self.root, dict(stage='S02', task=task_id, status='todo',
                                  inputs=combined, outputs=[], checks=preflight))
        tc.gate(self.root, 'S02', task_id)
        tc.update(self.root, dict(stage='S02', task=task_id, status='doing',
                                  inputs=combined, reason='按本轮技术输入显式执行或重新执行角色任务'))
        self.f.write(path, '纯技术夹具的轮次报告或版本标记：' + path + '。不是小说或实际阅读结论。')
        evidence = self.f.ev(path)
        checks = self.f.checks(task['kind'], [evidence])
        checks.update(preflight)
        tc.submit(self.root, dict(stage='S02', task=task_id, inputs=combined,
                                  outputs=[evidence], checks=checks))
        self.assertEqual(self.task(task_id)['status'], 'submitted')
        tc.update(self.root, dict(stage='S02', task=task_id, status='done',
                                  verification=dict(agent_id='fixture-main', source='显式机械夹具验收记录')))
        nc.index_book(self.root)
        return evidence

    def initial_role_review(self):
        self.enter_chapter()
        ids = self.insert_roles('S02.R01', 'review', 'S02.first_read')
        package = self.complete(ids[0], '审稿与修订/R01-M01.md', [self.f.draft])
        reports = [self.complete(task_id, '审稿与修订/R01-A0' + str(index) + '.md', [package])
                   for index, task_id in enumerate(ids[1:], 1)]
        self.complete('review', '审稿与修订/R01-E01.md', [self.f.draft, *reports])
        return ids

    def private_dispatch(self):
        renamed = self.temp_root / 'SECRET_ROOT_INTERNAL_82'
        self.root.rename(renamed)
        self.root = self.f.root = renamed
        self.enter_chapter()
        card = '人物/SECRET_CARD_INTERNAL_37.md'
        draft = '大纲/SECRET_DRAFT_INTERNAL_24.md'
        self.f.write(card, '仅用于检测内部材料路径是否回显的技术文本。')
        self.f.write(draft, '仅用于检测内部版本路径是否回显的技术文本。')
        self.f.register_fixture_artifacts()
        ids = self.insert_roles('S02.R01', 'review', 'S02.first_read')
        self.complete(ids[0], '审稿与修订/R01-M01.md', [self.f.ev(card), self.f.ev(draft)])
        reference = self.temp_root / 'G01.json'
        value = dict(schema_version=1, book=str(self.root), stage='S02', task=ids[1])
        reference.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return reference, value, card, draft

    def cli_ref(self, reference, task_id='S02.R01.A01', neutral=True, stage='S02'):
        command = [sys.executable, str(Path(tc.__file__)), 'gate', '--book-ref', str(reference)]
        if stage is not None:
            command += ['--stage', stage]
        if task_id is not None:
            command += ['--before', task_id]
        if neutral:
            command.append('--neutral')
        return subprocess.run(command, capture_output=True, text=True)

    def assert_neutral(self, result, ok, task_id='S02.R01.A01', stage='S02'):
        expected = dict(ok=ok)
        if stage is not None:
            expected['stage'] = stage
        if task_id is not None:
            expected['before'] = task_id
        if not ok:
            expected['error'] = 'gate_not_ready'
        self.assertEqual(result, expected)
        encoded = json.dumps(result, ensure_ascii=False)
        for marker in ('SECRET_ROOT_INTERNAL_82', 'SECRET_CARD_INTERNAL_37', 'SECRET_DRAFT_INTERNAL_24'):
            self.assertNotIn(marker, encoded)

    def test_book_ref_cli_and_api_hide_private_metadata_on_success_and_stale_failure(self):
        reference, value, card, draft = self.private_dispatch()
        before = nc.inventory(self.root)
        pointer_bytes = reference.read_bytes()
        result = self.cli_ref(reference)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        self.assert_neutral(json.loads(result.stdout), True)
        self.assert_neutral(tc.gate_ref(reference, 'S02', value['task']), True)
        self.assertEqual(nc.inventory(self.root), before)
        self.assertEqual(reference.read_bytes(), pointer_bytes)
        self.f.write(card, '技术夹具：真实材料版本改变，原来源哈希失效。')
        before = nc.inventory(self.root)
        result = self.cli_ref(reference)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        self.assert_neutral(json.loads(result.stdout), False)
        self.assert_neutral(tc.gate_ref(reference, 'S02', value['task']), False)
        self.assertEqual(nc.inventory(self.root), before)
        self.assertEqual(reference.read_bytes(), pointer_bytes)
        self.assertTrue((self.root / draft).is_file())

    def test_book_ref_without_caller_ids_hides_the_descriptor_stage_and_task_names(self):
        reference, value, card, _ = self.private_dispatch()
        data, stage = self.data_and_stage()
        old_stage = stage['id']
        hidden_stage = 'S02.INTERNAL_STAGE_SECRET_95'
        mapping = {task['id']: task['id'].replace('S02.', hidden_stage + '.', 1) for task in stage['tasks']}
        stage['id'] = hidden_stage
        for task in stage['tasks']:
            task['id'] = mapping[task['id']]
            task['depends_on'] = [mapping.get(dep, dep) for dep in task['depends_on']]
        for item in data['stages']:
            if item['next_stage'] == old_stage:
                item['next_stage'] = hidden_stage
        data['current_stage'] = hidden_stage
        data['current_task'] = mapping[data['current_task']]
        tc.save(self.root, data)
        reference.write_text(json.dumps(dict(value, stage=hidden_stage, task=mapping[value['task']]),
                                        ensure_ascii=False), encoding='utf-8')
        before = nc.inventory(self.root)
        self.assert_neutral(tc.gate_ref(reference), True, stage=None, task_id=None)
        result = self.cli_ref(reference, stage=None, task_id=None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        self.assert_neutral(json.loads(result.stdout), True, stage=None, task_id=None)
        self.assertNotIn('INTERNAL_STAGE_SECRET_95', result.stdout + result.stderr)
        self.assertEqual(nc.inventory(self.root), before)
        self.f.write(card, '技术夹具：隐藏绑定的已验收材料版本失效。')
        before = nc.inventory(self.root)
        self.assert_neutral(tc.gate_ref(reference), False, stage=None, task_id=None)
        result = self.cli_ref(reference, stage=None, task_id=None)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_neutral(json.loads(result.stdout), False, stage=None, task_id=None)
        self.assertNotIn('INTERNAL_STAGE_SECRET_95', result.stdout + result.stderr)
        self.assertEqual(nc.inventory(self.root), before)
        normal = subprocess.run([sys.executable, str(Path(tc.__file__)), 'gate', '--book', str(self.root),
                                 '--neutral'], capture_output=True, text=True)
        self.assertNotEqual(normal.returncode, 0)

    def test_bad_or_mismatched_references_and_non_neutral_mode_reject_without_exposing_content(self):
        reference, valid, _, _ = self.private_dispatch()
        cases = [dict(valid, schema_version=True), dict(valid, schema_version=2),
                 dict(valid, book='SECRET_ROOT_INTERNAL_82'), dict(valid, stage='OTHER'),
                 dict(valid, task='SECRET_TASK_INTERNAL_51'), dict(valid, task='role_perspective')]
        for value in cases:
            with self.subTest(value=value):
                reference.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
                before = nc.inventory(self.root)
                self.assert_neutral(tc.gate_ref(reference, 'S02', valid['task']), False)
                self.assertEqual(nc.inventory(self.root), before)
        reference.write_text('malformed PRIVATE_CONTENT_INTERNAL_61', encoding='utf-8')
        result = self.cli_ref(reference)
        self.assertEqual(result.returncode, 1)
        self.assert_neutral(json.loads(result.stdout), False)
        self.assertNotIn('PRIVATE_CONTENT_INTERNAL_61', result.stdout + result.stderr)
        missing = self.temp_root / 'G02.json'
        self.assert_neutral(tc.gate_ref(missing, 'S02', valid['task']), False)
        reference.write_text(json.dumps(valid, ensure_ascii=False), encoding='utf-8')
        result = self.cli_ref(reference, neutral=False)
        self.assertNotEqual(result.returncode, 0)
        self.assert_neutral(json.loads(result.stdout), False)
        before = nc.inventory(self.root)
        inside = self.root / '创作约定/G03.json'
        inside.write_text(json.dumps(valid, ensure_ascii=False), encoding='utf-8')
        after_creation = nc.inventory(self.root)
        self.assert_neutral(tc.gate_ref(inside, 'S02', valid['task']), False)
        self.assertEqual(nc.inventory(self.root), after_creation)
        self.assertNotEqual(before, after_creation)

    def test_book_ref_does_not_relax_root_or_reference_symlink_guards(self):
        reference, value, _, _ = self.private_dispatch()
        alias = self.temp_root / 'B01'
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest('directory symlinks unavailable on this actual platform: ' + str(exc))
        before = nc.inventory(self.root)
        reference.write_text(json.dumps(dict(value, book=str(alias)), ensure_ascii=False), encoding='utf-8')
        self.assert_neutral(tc.gate_ref(reference, 'S02', value['task']), False)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            nc.root_path(alias)
        reference.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        linked_ref = self.temp_root / 'G04.json'
        linked_ref.symlink_to(reference)
        self.assert_neutral(tc.gate_ref(linked_ref, 'S02', value['task']), False)
        actual_folder = self.temp_root / 'J01'
        actual_folder.mkdir()
        nested_ref = actual_folder / 'G05.json'
        nested_ref.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        parent_alias = self.temp_root / 'J02'
        parent_alias.symlink_to(actual_folder, target_is_directory=True)
        self.assert_neutral(tc.gate_ref(parent_alias / 'G05.json', 'S02', value['task']), False)
        self.assertEqual(nc.inventory(self.root), before)

    def test_revisions_reopen_stable_role_nodes_and_preserve_each_round_report(self):
        initial_ids = self.initial_role_review()
        version2 = self.complete('revise', '草稿与备选/R02-D01.md', [self.f.draft])
        repeat_ids = self.insert_roles('S02.R02', 'recheck', 'S02.revise')
        self.assertTrue(set(initial_ids).isdisjoint(repeat_ids))
        package2 = self.complete(repeat_ids[0], '审稿与修订/R02-M01.md', [version2])
        roles2 = [self.complete(task_id, '审稿与修订/R02-A0' + str(index) + '.md', [package2, version2])
                  for index, task_id in enumerate(repeat_ids[1:], 1)]
        review2 = self.complete('recheck', '审稿与修订/R02-E01.md', [version2, *roles2])
        old_reports = {item['path']: (self.root / item['path']).read_bytes()
                       for item in [version2, package2, *roles2, review2]}
        tc.update(self.root, dict(stage='S02', task='revise', status='todo', reason='技术夹具的下一轮真实返工'))
        self.assertTrue(all(self.task(task_id)['status'] == 'done' for task_id in initial_ids))
        self.assertTrue(all(self.task(task_id)['status'] == 'todo' for task_id in repeat_ids))
        self.assertEqual(self.task('recheck')['status'], 'todo')
        self.assertTrue(tc.gate(self.root, 'S02', 'revise')['ok'])
        version3 = self.complete('revise', '草稿与备选/R03-D01.md', [version2, review2])
        self.assertIn(review2, self.task('revise')['inputs'])
        self.assertNotIn('S02.recheck', self.task('revise')['depends_on'])
        package3 = self.complete(repeat_ids[0], '审稿与修订/R03-M01.md', [version3])
        roles3 = [self.complete(task_id, '审稿与修订/R03-A0' + str(index) + '.md', [package3, version3])
                  for index, task_id in enumerate(repeat_ids[1:], 1)]
        self.complete('recheck', '审稿与修订/R03-E01.md', [version3, *roles3])
        for path, original in old_reports.items():
            self.assertEqual((self.root / path).read_bytes(), original)
        for task_id, evidence in zip(repeat_ids, [package2, *roles2]):
            self.assertTrue(any(record.get('status') == 'done' and evidence in record.get('outputs', [])
                                for record in self.task(task_id)['history']))
            self.assertEqual(self.task(task_id)['status'], 'done')
            self.assertNotIn(evidence, self.task(task_id)['outputs'])
        self.assertTrue(tc.gate(self.root, 'S02', 'adopt')['ok'])
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(nc.check_book(self.root), [])

    def test_reverse_edges_to_initial_materials_or_recheck_are_rejected_without_writes(self):
        initial_ids = self.initial_role_review()
        version = self.complete('revise', '草稿与备选/R02-D01.md', [self.f.draft])
        self.insert_roles('S02.R02', 'recheck', 'S02.revise')
        baseline = tc.load_plan(self.root)
        before = nc.inventory(self.root)
        for task_id, dependency in ((initial_ids[0], 'S02.revise'), ('revise', 'S02.recheck')):
            with self.subTest(task=task_id):
                data = copy.deepcopy(baseline)
                stage = next(s for s in data['stages'] if s['id'] == 'S02')
                tc.selected_task(stage, task_id)['depends_on'].append(dependency)
                with self.assertRaisesRegex(ValueError, 'task dependency cycle'):
                    tc.save(self.root, data)
                self.assertEqual(nc.inventory(self.root), before)
        self.assertTrue(tc.gate(self.root, 'S02', 'S02.R02.M01')['ok'])
        self.assertEqual(nc.digest(self.root / version['path']), version['sha256'])


if __name__ == '__main__':
    unittest.main()
