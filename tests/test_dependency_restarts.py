"""Dependency invalidation and historical adoption, using technical fixtures only."""
import copy
import unittest

import test_standalone_workflows as standalone
import test_editorial_gates as editorial


tc, nc, cc = editorial.tc, editorial.nc, editorial.cc


class DependencyRestartTests(unittest.TestCase):
    def setUp(self):
        self.f = standalone.StandaloneWorkflowTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.create('materials')
        self.f.complete('scope')
        self.inputs = [self.f.evidence(self.f.authorization), self.f.evidence(self.f.source)]
        tc.update(self.root, dict(stage='S01', task='organize', status='doing',
                                 inputs=self.inputs, checks=self.f.checks('organize')))

    def task(self, name='organize'):
        return tc.selected_task(tc.load_plan(self.root)['stages'][0], name)

    def submit_old_output(self):
        self.report = '审稿与修订/旧范围整理.md'
        (self.root / self.report).write_text('旧范围的技术交付，不是小说内容。', encoding='utf-8')
        evidence = self.f.evidence(self.report)
        tc.submit(self.root, dict(stage='S01', task='organize', outputs=[evidence],
                                 checks=self.f.checks('organize', self.report)))
        return evidence

    def reopen_scope(self):
        tc.update(self.root, dict(stage='S01', task='scope', status='todo', reason='本轮技术范围改变'))

    def replace_scope(self):
        tc.update(self.root, dict(stage='S01', task='scope', status='doing'))
        self.new_scope = '审稿与修订/新版范围.md'
        (self.root / self.new_scope).write_text('新版技术范围及重新核对依据。', encoding='utf-8')
        self.scope_evidence = self.f.evidence(self.new_scope)
        tc.submit(self.root, dict(stage='S01', task='scope', outputs=[self.scope_evidence],
                                 checks=self.f.checks('scope', self.new_scope)))
        tc.update(self.root, dict(stage='S01', task='scope', status='done',
                                 verification=self.f.verification))

    def accept_old_output(self):
        evidence = self.submit_old_output()
        tc.update(self.root, dict(stage='S01', task='organize', status='done',
                                 verification=self.f.verification))
        return evidence

    def test_completed_child_requires_a_fresh_execution_after_parent_changes(self):
        old = self.accept_old_output()
        self.reopen_scope()
        task = self.task()
        self.assertEqual(task['status'], 'todo')
        self.assertTrue(task['dependency_restart_required'])
        self.assertEqual(task['inputs'], [])
        self.assertEqual(task['outputs'], [])
        self.assertEqual(task['checks'], {})
        self.assertIsNone(task['verification'])
        self.assertEqual(task['history'][-1]['status'], 'done')
        self.assertEqual(task['history'][-1]['retained_outputs'], [old])
        self.replace_scope()
        self.assertTrue(tc.check(self.root)['ok'])
        # Previous acceptance still proves provenance, but cannot revive progress.
        self.assertIn((old['path'], old['sha256']),
                      tc.known_artifact_pairs(self.root, tc.load_plan(self.root)))
        for status in ('submitted', 'done'):
            for recovery in (False, True):
                with self.subTest(status=status, recovery=recovery):
                    before = nc.inventory(self.root)
                    with self.assertRaisesRegex(ValueError, 'restart task with current inputs'):
                        tc.update(self.root, dict(stage='S01', task='organize', status=status,
                            recovery_only=recovery, reason='技术夹具尝试直接恢复旧结果',
                            outputs=[old], verification=self.f.verification))
                    self.assertEqual(nc.inventory(self.root), before)
        for fields in ({}, {'inputs': []}, {'reason': '缺少本轮输入'}):
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, 'explicit current inputs and reason'):
                    tc.update(self.root, dict(stage='S01', task='organize', status='doing', **fields))
        tc.update(self.root, dict(stage='S01', task='organize', status='doing',
            inputs=[self.scope_evidence, self.f.evidence(self.f.authorization)],
            reason='按照新版范围重新执行技术整理', checks=self.f.checks('organize')))
        self.assertNotIn('dependency_restart_required', self.task())
        path = '审稿与修订/新版范围整理.md'
        (self.root / path).write_text('新版范围技术报告，旧报告保留。', encoding='utf-8')
        tc.submit(self.root, dict(stage='S01', task='organize', outputs=[self.f.evidence(path)],
                                 checks=self.f.checks('organize', path)))
        tc.update(self.root, dict(stage='S01', task='organize', status='done',
                                 verification=self.f.verification))
        self.assertEqual(self.f.evidence(self.report), old)
        self.assertTrue(tc.check(self.root)['ok'])

    def test_completed_transitive_next_plan_without_outputs_must_restart(self):
        self.accept_old_output()
        tc.plan(self.root, dict(id='S01', end_reason='本轮技术任务结束'))
        tc.update(self.root, dict(stage='S01', task='next_plan', status='doing',
                                 inputs=self.inputs, checks=self.f.checks('next_plan')))
        tc.update(self.root, dict(stage='S01', task='next_plan', status='done',
                                 verification=self.f.verification))
        self.assertEqual(self.task('next_plan')['outputs'], [])
        self.reopen_scope()
        self.assertTrue(self.task()['dependency_restart_required'])
        self.assertTrue(self.task('next_plan')['dependency_restart_required'])
        self.assertEqual(self.task('next_plan')['history'][-1]['dependency_invalidated_by'], 'S01.scope')
        self.replace_scope()
        tc.update(self.root, dict(stage='S01', task='organize', status='doing',
            inputs=[self.scope_evidence, self.f.evidence(self.f.authorization)],
            reason='重新核对新版范围', checks=self.f.checks('organize')))
        self.submit_old_output()
        tc.update(self.root, dict(stage='S01', task='organize', status='done',
                                 verification=self.f.verification))
        with self.assertRaisesRegex(ValueError, 'restart task with current inputs'):
            tc.update(self.root, dict(stage='S01', task='next_plan', status='done',
                verification=self.f.verification))
        tc.update(self.root, dict(stage='S01', task='next_plan', status='doing',
            inputs=self.inputs, reason='按新版交付重新核对本轮终点',
            checks=self.f.checks('next_plan')))
        tc.update(self.root, dict(stage='S01', task='next_plan', status='done',
                                 verification=self.f.verification))
        self.assertTrue(tc.close_stage(self.root, 'S01')['ok'])

    def test_exemption_is_invalidated_and_rechecked_after_parent_changes(self):
        self.accept_old_output()
        data = tc.load_plan(self.root)
        optional = copy.deepcopy(self.task())
        optional.update(id='S01.optional', kind='research', status='todo',
            depends_on=['S01.scope', 'S01.organize'], inputs=[], outputs=[], checks={},
            history=[], verification=None, reason='')
        data['stages'][0]['tasks'].insert(-1, optional)
        data['current_task'] = optional['id']
        tc.save(self.root, data)
        tc.update(self.root, dict(stage='S01', task='S01.optional', status='not_applicable',
            reason='旧范围无需额外技术核查', verification=self.f.verification))
        self.reopen_scope()
        optional = self.task('S01.optional')
        self.assertTrue(optional['dependency_restart_required'])
        self.assertEqual(optional['history'][-1]['status'], 'not_applicable')
        self.replace_scope()
        with self.assertRaisesRegex(ValueError, 'restart task with current inputs'):
            tc.update(self.root, dict(stage='S01', task='S01.optional', status='not_applicable',
                recovery_only=True, reason='沿用旧范围豁免', verification=self.f.verification))
        tc.update(self.root, dict(stage='S01', task='organize', status='doing',
            inputs=[self.scope_evidence, self.f.evidence(self.f.authorization)],
            reason='在新版范围下重新核对整理产物', checks=self.f.checks('organize')))
        self.submit_old_output()
        tc.update(self.root, dict(stage='S01', task='organize', status='done',
                                 verification=self.f.verification))
        tc.update(self.root, dict(stage='S01', task='S01.optional', status='doing',
            inputs=[], reason='重新判断新版范围内是否适用额外核查'))
        tc.update(self.root, dict(stage='S01', task='S01.optional', status='not_applicable',
            reason='新版范围重新核对后仍不涉及该技术核查', verification=self.f.verification))
        self.assertNotIn('dependency_restart_required', self.task('S01.optional'))
        self.assertTrue(tc.check(self.root)['ok'])

    def test_previously_executed_blocked_child_cannot_escape_restart(self):
        old = self.submit_old_output()
        tc.update(self.root, dict(stage='S01', task='organize', status='blocked',
                                 reason='需要先修改范围'))
        self.reopen_scope()
        self.assertTrue(self.task()['dependency_restart_required'])
        self.assertEqual(self.task()['history'][-1]['status'], 'blocked')
        self.assertEqual(self.task()['history'][-1]['retained_outputs'], [old])
        self.replace_scope()
        self.assertTrue(tc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'restart task with current inputs'):
            tc.update(self.root, dict(stage='S01', task='organize', status='submitted',
                                     outputs=[old], checks=self.f.checks('organize', self.report)))

    def test_submitted_child_is_invalidated_without_blocking_parent_rework(self):
        old = self.submit_old_output()
        self.reopen_scope()
        task = self.task()
        self.assertEqual(task['status'], 'todo')
        self.assertTrue(task['dependency_restart_required'])
        self.assertEqual(task['outputs'], [])
        self.assertEqual(task['inputs'], [])
        self.assertEqual(task['checks'], {})
        record = task['history'][-1]
        self.assertEqual(record['status'], 'submitted')
        self.assertEqual(record['outputs'], [old])
        self.assertEqual(record['retained_outputs'], [old])
        self.assertTrue(tc.check(self.root)['ok'])
        self.replace_scope()
        self.assertEqual(self.f.evidence(self.report), old)
        # Exact retained bytes permit administrative parent repair, not reuse.
        self.assertNotIn((old['path'], old['sha256']), tc.known_artifact_pairs(self.root, tc.load_plan(self.root)))
        for status in ('submitted', 'done'):
            with self.subTest(status=status):
                before = nc.inventory(self.root)
                with self.assertRaisesRegex(ValueError, 'restart task with current inputs'):
                    tc.update(self.root, dict(stage='S01', task='organize', status=status,
                        outputs=[old], checks=self.f.checks('organize', self.report),
                        verification=self.f.verification))
                self.assertEqual(nc.inventory(self.root), before)
        with self.assertRaisesRegex(ValueError, 'restart task with current inputs'):
            tc.update(self.root, dict(stage='S01', task='organize', status='done', recovery_only=True,
                reason='不得用行政恢复跳过重新执行', outputs=[old], verification=self.f.verification))

    def test_doing_child_requires_explicit_new_start_and_current_inputs(self):
        self.reopen_scope()
        self.replace_scope()
        self.assertEqual(self.task()['status'], 'todo')
        with self.assertRaisesRegex(ValueError, 'explicit current inputs and reason'):
            tc.update(self.root, dict(stage='S01', task='organize', status='doing'))
        inputs = [self.scope_evidence, self.f.evidence(self.f.authorization)]
        tc.update(self.root, dict(stage='S01', task='organize', status='doing', inputs=inputs,
            reason='已重新核对新版授权及输入，重新执行本轮整理', checks=self.f.checks('organize')))
        self.assertNotIn('dependency_restart_required', self.task())
        self.assertTrue(tc.gate(self.root, 'S01', 'organize')['ok'])
        self.submit_old_output()
        tc.update(self.root, dict(stage='S01', task='organize', status='done',
                                 verification=self.f.verification))
        self.assertTrue(tc.check(self.root)['ok'])

    def test_retention_is_exact_and_does_not_exempt_new_or_changed_files(self):
        old = self.submit_old_output()
        self.reopen_scope()
        (self.root / self.report).write_text('失效后出现的新版本，尚未登记。', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
            tc.update(self.root, dict(stage='S01', task='scope', status='doing'))
        (self.root / self.report).write_text('旧范围的技术交付，不是小说内容。', encoding='utf-8')
        self.assertEqual(self.f.evidence(self.report), old)
        unknown = self.root / '审稿与修订/无关新产物.md'
        unknown.write_text('不能由保留旧产物的范围覆盖。', encoding='utf-8')
        self.assertTrue(any('无关新产物' in issue for issue in tc.check(self.root)['artifact_blockers']))

    def test_replacing_accepted_parent_invalidates_active_children(self):
        self.submit_old_output()
        old_scope = self.task('scope')
        tc.update(self.root, dict(stage='S01', task='scope', status='done',
            reason='新验收边界', verification=self.f.verification))
        self.assertNotEqual(self.task('scope')['reason'], old_scope['reason'])
        self.assertEqual(self.task()['status'], 'todo')
        self.assertTrue(self.task()['dependency_restart_required'])

    def test_transitive_active_child_is_invalidated_through_a_completed_task(self):
        self.submit_old_output()
        tc.update(self.root, dict(stage='S01', task='organize', status='done',
                                 verification=self.f.verification))
        tc.plan(self.root, dict(id='S01', end_reason='技术夹具本轮结束'))
        tc.update(self.root, dict(stage='S01', task='next_plan', status='doing',
            inputs=self.inputs, checks=self.f.checks('next_plan')))
        self.reopen_scope()
        self.assertEqual(self.task('organize')['status'], 'todo')
        self.assertEqual(self.task('next_plan')['status'], 'todo')
        self.assertTrue(self.task('next_plan')['dependency_restart_required'])
        self.assertEqual(self.task('next_plan')['history'][-1]['dependency_invalidated_by'], 'S01.scope')


class HistoricalAdoptionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = editorial.EditorialGateTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.f.chapter_ready()
        self.operation = cc.adopt(self.root, self.f.req)
        self.f.synchronize(self.operation)
        tc.plan(self.root, dict(id='S02', end_reason='技术夹具授权到此结束'))
        self.f.done('S02', 'next_plan')
        tc.close_stage(self.root, 'S02')
        self.old_path = self.f.req['target']
        self.original_ledger = copy.deepcopy(tc.load_plan(self.root))

    def rename(self):
        name = '第0001章-新技术标题.md'
        (self.root / self.old_path).rename(self.root / name)
        state = nc.load_state(self.root)
        state['chapters'][0]['path'] = name
        self.f.save(nc.STATE, state)
        nc.index_book(self.root)
        return name

    def test_closed_adoption_survives_pure_rename_without_rewriting_task_history(self):
        self.rename()
        self.assertEqual(nc.check_book(self.root), [])
        self.assertTrue(cc.check(self.root)['ok'])
        self.assertTrue(tc.check(self.root)['ok'], tc.check(self.root))
        self.assertEqual(tc.load_plan(self.root), self.original_ledger)

    def test_closed_adoption_uses_original_bytes_after_a_new_version_is_adopted(self):
        original_sha = self.f.draft['sha256']
        tc.plan(self.root, dict(id='S03', title='技术章节新版', workflow='chapter', chapter=1))
        draft_name = '草稿与备选/第01章-技术新版.md'
        self.f.write(draft_name, '机械测试文本的新版本。\n')
        self.f.draft = self.f.ev(draft_name)
        self.f.review_path = '审稿与修订/新版评审.json'
        self.f.prose_path = '审稿与修订/新版机械检查.json'
        self.f.review.update(writer_task='S03.draft',
            reviewer=dict(task_id='S03.recheck', agent_id='fixture-new-reader'),
            draft_sha256=self.f.draft['sha256'], reviewed_draft=dict(self.f.draft),
            current_draft=dict(self.f.draft))
        self.f.save(self.f.review_path, self.f.review)
        self.f.save_prose()
        self.f.req.update(draft=draft_name, expected_sha256=original_sha,
            writer=dict(task_id='S03.draft', agent_id='fixture-new-writer'),
            reviews=[self.f.review_path], prose_check=self.f.ev(self.f.prose_path),
            authorization=dict(mode='trial', source='技术夹具新版显式授权', approved_sha256=self.f.draft['sha256']),
            change_record='连贯性记录/新版技术变化.json', task_stage='S03')
        self.f.register_fixture_artifacts()
        prepare_outputs = [self.f.ev('审稿与修订/技术依据.md'), self.f.ev(self.f.policy_path)]
        prepare_checks = self.f.checks('prepare', prepare_outputs)
        prepare_checks['prose_policy'] = dict(self.f.ev(self.f.policy_path), summary='沿用技术夹具机械约定')
        self.f.done('S03', 'prepare', prepare_outputs, checks=prepare_checks)
        self.f.done('S03', 'draft', [self.f.draft])
        self.f.done('S03', 'first_read')
        self.f.done('S03', 'review', [self.f.ev(self.f.review_path)])
        self.f.done('S03', 'revise')
        self.f.done('S03', 'recheck', [self.f.ev(self.f.review_path), self.f.ev(self.f.prose_path)],
                    inputs=[self.f.draft])
        tc.update(self.root, dict(stage='S03', task='adopt', status='doing'))
        cc.adopt(self.root, self.f.req)
        self.assertNotEqual(nc.digest(self.root / self.old_path), original_sha)
        checked = tc.check(self.root, stage='S02')
        self.assertTrue(checked['ok'], checked)
        old = next(stage for stage in tc.load_plan(self.root)['stages'] if stage['id'] == 'S02')
        self.assertEqual(old, self.original_ledger['stages'][1])

    def test_missing_or_corrupt_current_chapter_is_not_hidden_by_its_old_archive(self):
        name = self.rename()
        (self.root / name).write_text('同一采用操作所指的活动正文被手改。', encoding='utf-8')
        self.assertFalse(tc.check(self.root)['ok'])

    def test_archive_corruption_is_not_accepted_as_historical_proof(self):
        self.rename()
        (self.root / self.operation['backup'] / 'draft.md').write_text('损坏的技术档案。', encoding='utf-8')
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertTrue(any('archived draft hash mismatch' in issue for issue in checked['issues']))

    def test_only_exact_adopted_output_is_resolved_not_other_inputs_or_outputs(self):
        for location in ('inputs', 'outputs'):
            with self.subTest(location=location):
                data = copy.deepcopy(self.original_ledger)
                adopted = tc.selected_task(data['stages'][1], 'adopt')
                name = '审稿与修订/并非采用正文的证据.md'
                adopted[location].append(dict(path=name, sha256='0' * 64))
                tc.save(self.root, data)
                checked = tc.check(self.root)
                self.assertFalse(checked['ok'])
                self.assertTrue(any('stale evidence: ' + name in issue for issue in checked['issues']))
        tc.save(self.root, self.original_ledger)

    def test_archived_output_does_not_exempt_an_input_at_the_same_old_path(self):
        self.rename()
        data = copy.deepcopy(self.original_ledger)
        adopted = tc.selected_task(data['stages'][1], 'adopt')
        adopted['inputs'].append(copy.deepcopy(adopted['outputs'][0]))
        tc.save(self.root, data)
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertTrue(any('stale evidence: ' + self.old_path in issue for issue in checked['issues']))


if __name__ == '__main__':
    unittest.main()
