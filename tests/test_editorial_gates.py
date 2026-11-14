"""Editorial gate mechanics with technical fixtures, never novel generation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'novel-studio/scripts'))
import chapterctl as cc
import bookctl as bc
import novelctl as nc
import prosecheck
import taskctl as tc


class EditorialGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = nc.init_book(Path(self.temp.name), '编辑门槛技术夹具')
        self.write('审稿与修订/技术依据.md', '仅用于机械流程检查，不是阅读或创作证据。')
        self.write('草稿与备选/第01章-草稿-v1.md', '机械测试文本。\n')
        self.draft = self.ev('草稿与备选/第01章-草稿-v1.md')
        self.policy_path = '创作约定/机械约定.json'
        self.save(self.policy_path, {'quote_style': 'chinese_double', 'quote_confirmed': True})
        self.write('审稿与修订/当前阅读报告.md', '技术夹具：当前版本完整阅读声明。')
        self.review = dict(
            schema_version=2, writer_task='S02.draft',
            reviewer=dict(task_id='S02.recheck', agent_id='fixture-reader'),
            draft_sha256=self.draft['sha256'], coverage=['literary', 'continuity'],
            verdict='pass', issues=[], reviewed_draft=dict(self.draft),
            current_draft=dict(self.draft), reading=dict(mode='full', scope='技术夹具全部正文'),
            report=self.ev('审稿与修订/当前阅读报告.md'))
        self.review_path = '审稿与修订/当前评审.json'
        self.save(self.review_path, self.review)
        self.prose_path = '审稿与修订/机械检查.json'
        self.save_prose()
        self.req = dict(
            number=1, target='第0001章-技术夹具.md', draft=self.draft['path'],
            expected_sha256=None, writer=dict(task_id='S02.draft', agent_id='fixture-writer'),
            reviews=[self.review_path], prose_check=self.ev(self.prose_path),
            authorization=dict(mode='trial', source='技术夹具显式授权', approved_sha256=self.draft['sha256']),
            change_record='连贯性记录/技术变化.json')
        self.recovery_source = '创作约定/固定技术夹具来源.md'
        self.recovery_quote = '本测试中的预制文本和报告均是显式技术输入，仅用于验证机械流程，不证明实际创作或阅读。'
        self.write(self.recovery_source, self.recovery_quote)
        self.register_fixture_artifacts()

    def write(self, path, text):
        (self.root / path).write_text(text, encoding='utf-8')

    def save(self, path, value):
        self.write(path, json.dumps(value, ensure_ascii=False, indent=2))

    def ev(self, path):
        return dict(path=path, sha256=nc.digest(self.root / path))

    def register_fixture_artifacts(self):
        artifacts = [dict(path=path, sha256=sha, classification='author_input', source=self.ev(self.recovery_source),
                          source_quote=self.recovery_quote, scope='预制技术夹具输入')
                     for path, sha in tc.artifact_candidates(self.root)]
        tc.recover(self.root, dict(artifacts=artifacts,
                                  verification=dict(agent_id='fixture-main', source='显式核对预制技术输入')))

    def save_prose(self):
        policy = json.loads((self.root / self.policy_path).read_text(encoding='utf-8'))
        self.save(self.prose_path, dict(
            draft_sha256=self.draft['sha256'], policy=self.ev(self.policy_path),
            result=prosecheck.check_text((self.root / self.draft['path']).read_text(encoding='utf-8'), policy)))

    def checks(self, kind, outputs):
        checks = {name: dict(outputs[0], summary='技术夹具覆盖 ' + name)
                  for name in tc.EDITORIAL_CHECKS.get(kind, ())}
        if kind == 'direction':
            decision = dict(resolution='provided', value='已提供的技术约定',
                            source_quote='技术夹具作者：本项按已列选择执行。', scope='本次技术检查')
            for category, topics in tc.DIRECTION_DECISIONS.items():
                checks[category]['decisions'] = {topic: dict(decision) for topic in topics}
            checks['startup_mode']['decision'] = dict(decision)
            checks['author_preferences']['decisions']['hidden_plot'] = dict(
                decision, value=getattr(self, 'hidden_plot_choice', 'disabled'))
            checks['author_preferences']['decisions']['title'] = dict(
                decision, value=nc.load_state(self.root)['title'], prior_status='proposed')
        if kind in tc.CREATIVE_DECISION_TASKS:
            checks['creative_decisions'] = dict(
                outputs[0], summary='机械夹具已列本轮决定', items=[], none_reason='本轮只做既有技术约定的验证，无新增创作选择')
        return checks

    def done(self, stage, kind, outputs=None, **fields):
        outputs = outputs or [self.ev('审稿与修订/技术依据.md')]
        request = dict(stage=stage, task=kind, status='done', outputs=outputs,
                       checks=self.checks(kind, outputs),
                       verification=dict(agent_id='fixture-main', source='技术夹具显式验收'))
        request.update(fields)
        if kind in tc.CREATIVE_DECISION_TASKS:
            author_input = self.ev('审稿与修订/技术依据.md')
            preflight = dict(author_input, summary='机械夹具执行前确认', items=[], none_reason='验证既有技术决定，无新增创作选择')
            inputs = list(request.get('inputs', []))
            if not any(item['path'] == author_input['path'] for item in inputs):
                inputs.append(author_input)
            request['inputs'] = inputs
            tc.update(self.root, dict(stage=stage, task=kind, status='todo', inputs=inputs,
                                     checks=dict(creative_decisions=preflight)))
            tc.gate(self.root, stage, kind)
        return tc.update(self.root, request)

    def chapter_ready(self, policy=None):
        if policy is not None:
            self.save(self.policy_path, policy)
            self.save_prose()
            self.req['prose_check'] = self.ev(self.prose_path)
        self.register_fixture_artifacts()
        tc.plan(self.root, dict(id='S02', title='逐章技术夹具', workflow='chapter', chapter=1))
        self.done('S01', 'direction')
        self.done('S01', 'concept')
        tc.plan(self.root, dict(id='S01', next_stage='S02'))
        self.done('S01', 'next_plan')
        tc.close_stage(self.root, 'S01')
        outputs = [self.ev('审稿与修订/技术依据.md'), self.ev(self.policy_path)]
        checks = self.checks('prepare', outputs)
        checks['prose_policy'] = dict(self.ev(self.policy_path), summary='由技术夹具作者约定提取')
        self.done('S02', 'prepare', outputs, checks=checks)
        self.done('S02', 'draft', [self.draft])
        self.done('S02', 'first_read')
        self.done('S02', 'review', [self.ev(self.review_path)])
        self.done('S02', 'revise')
        self.accept_recheck()

    def accept_recheck(self):
        self.done('S02', 'recheck', [self.ev(path) for path in self.req['reviews']] + [self.ev(self.prose_path)],
                  inputs=[self.draft])
        tc.update(self.root, dict(stage='S02', task='adopt', status='doing',
            inputs=[self.draft, *[self.ev(path) for path in self.req['reviews']], self.ev(self.prose_path)],
            reason='已核对当前技术稿、复核报告及执行政策，重新启动采用。'))

    def prepare_sync(self, operation):
        self.done('S02', 'adopt', [self.ev(self.req['target'])], operation_id=operation['operation_id'])
        tc.update(self.root, dict(stage='S02', task='sync', status='doing'))
        self.write('创作约定/交接.md', '技术夹具：第0001章已采用；待验收同步与后续计划。')
        self.write('摘要/技术摘要.md', '机械测试摘要。')
        self.write('历史版本/采用记录.md', '机械测试采用记录。')
        nc.index_book(self.root)
        categories = {'summaries': ['摘要/技术摘要.md'], 'history': ['历史版本/采用记录.md'],
                      'indexes': ['索引.md'], 'handoff': ['创作约定/交接.md']}
        self.save(self.req['change_record'], dict(
            draft_sha256=self.draft['sha256'],
            categories={name: 'updated' if name in categories else 'unchanged' for name in cc.CATEGORIES},
            checked_sources=[self.req['target']], updated_files=[self.ev(paths[0]) for paths in categories.values()],
            category_files=categories, handoff=dict(path='创作约定/交接.md', chapter=1)))
        nc.index_book(self.root)

    def synchronize(self, operation):
        self.prepare_sync(operation)
        cc.finish(self.root, operation)
        self.done('S02', 'sync', [self.ev(self.req['change_record'])])

    def test_default_v2_complete_chain_including_sync_next_plan_and_close(self):
        self.assertEqual(tc.load_plan(self.root)['stages'][0]['workflow_version'], 2)
        self.chapter_ready()
        operation = cc.adopt(self.root, self.req)
        self.synchronize(operation)
        tc.plan(self.root, dict(id='S02', end_reason='技术夹具授权到此结束'))
        self.done('S02', 'next_plan')
        tc.close_stage(self.root, 'S02')
        self.assertTrue(cc.check(self.root)['ok'])
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertEqual(nc.load_state(self.root)['pending_operations'], [])

    def test_v2_missing_editorial_checks_cannot_accept_or_submit(self):
        evidence = self.ev('审稿与修订/技术依据.md')
        before = nc.inventory(self.root)
        request = dict(stage='S01', task='direction', outputs=[evidence])
        with self.assertRaisesRegex(ValueError, 'author_preferences'):
            tc.submit(self.root, request)
        with self.assertRaisesRegex(ValueError, 'author_preferences'):
            tc.update(self.root, dict(request, status='done', verification=dict(agent_id='fixture', source='test')))
        self.assertEqual(before, nc.inventory(self.root))

    def test_submit_registers_outputs_without_claiming_completion(self):
        evidence = self.ev('审稿与修订/技术依据.md')
        result = tc.submit(self.root, dict(stage='S01', task='direction', outputs=[evidence],
                                        checks=self.checks('direction', [evidence])))
        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(tc.load_plan(self.root)['stages'][0]['tasks'][0]['status'], 'submitted')
        with self.assertRaises(ValueError):
            tc.gate(self.root, 'S01', 'concept')
        with self.assertRaises(ValueError):
            tc.submit(self.root, dict(stage='S01', task='direction', status='done'))
        self.write(evidence['path'], '提交后的技术依据已经改变。')
        checked = tc.check(self.root)
        self.assertFalse(checked['ok'])
        self.assertTrue(any('stale submitted evidence' in issue for issue in checked['issues']))

    def test_unregistered_artifact_blocks_and_does_not_move_cursor(self):
        self.write('草稿与备选/未登记技术产物.md', '合成未登记文本。')
        before = nc.inventory(self.root)
        result = tc.check(self.root)
        self.assertFalse(result['ok'])
        self.assertTrue(any('未登记技术产物.md' in issue for issue in result['artifact_blockers']))
        self.assertEqual(result['resume']['task'], 'S01.direction')
        self.assertEqual(before, nc.inventory(self.root))

    def test_missing_workflow_version_is_legacy_and_not_retroactively_rewritten(self):
        data = tc.load_plan(self.root)
        del data['stages'][0]['workflow_version']
        del data['stages'][0]['direction_gate_version']
        data['stages'][0].pop('hidden_plot_gate_version', None)
        del data['stages'][0]['decision_gate_version']
        del data['stages'][0]['execution_gate_version']
        data['stages'][0].pop('result_gate_version', None)
        tc.save(self.root, data)
        self.assertTrue(tc.check(self.root)['ok'])
        self.done('S01', 'direction', checks={})
        with self.assertRaisesRegex(ValueError, 'historical workflow_version'):
            tc.plan(self.root, dict(id='S01', workflow_version=2))
        tc.plan(self.root, dict(id='S02', title='新阶段', workflow='chapter', chapter=1))
        self.assertEqual(tc.load_plan(self.root)['stages'][1]['workflow_version'], 2)

    def test_new_v2_book_cannot_downgrade_successor(self):
        for reason in ('', '试图自行填写兼容理由'):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    tc.plan(self.root, dict(id='S02', title='不允许降级', workflow='chapter', chapter=1,
                                           workflow_version=1, compatibility_reason=reason))

    def test_finished_v2_prose_evidence_stays_bound_to_adopted_policy(self):
        self.chapter_ready()
        operation = cc.adopt(self.root, self.req)
        self.synchronize(operation)
        self.assertTrue(cc.check(self.root)['ok'])
        original = (self.root / self.prose_path).read_bytes()
        self.save(self.prose_path, {'tampered': True})
        self.assertFalse(cc.check(self.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'chapterctl check failed'):
            bc.manifest(self.root)
        (self.root / self.prose_path).write_bytes(original)
        self.save(self.policy_path, {'chapter_length': {'hard_max': 0}})
        self.assertFalse(cc.check(self.root)['ok'])

    def test_later_policy_version_does_not_change_adopted_chapter(self):
        self.chapter_ready()
        operation = cc.adopt(self.root, self.req)
        self.synchronize(operation)
        self.save('创作约定/机械约定-v2.json', {'chapter_length': {'hard_max': 0}})
        nc.index_book(self.root)
        self.assertTrue(cc.check(self.root)['ok'])

    def test_current_v2_stage_rejects_legacy_review_even_if_hash_matches(self):
        self.chapter_ready()
        legacy = {key: self.review[key] for key in
                  ('writer_task', 'reviewer', 'draft_sha256', 'coverage', 'verdict', 'issues')}
        self.save('审稿与修订/旧格式评审.json', dict(legacy, schema_version=1))
        self.register_fixture_artifacts()
        self.req['reviews'] = ['审稿与修订/旧格式评审.json']
        self.accept_recheck()
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'requires review schema_version 2'):
            cc.adopt(self.root, self.req)
        self.assertEqual(before, nc.inventory(self.root))

    def test_explicit_legacy_version_contradiction_is_rejected(self):
        legacy = dict(self.review, schema_version=1,
                      version_scope=dict(verdict_version='v2（旧稿）', adopted_version='v3（新稿）'))
        self.save(self.review_path, legacy)
        with self.assertRaisesRegex(ValueError, 'version_scope contradicts'):
            cc.validate_reviews(self.root, [self.review_path], self.req['writer'], self.draft['sha256'])

    def test_current_review_cannot_claim_it_reviewed_another_draft(self):
        self.write('草稿与备选/旧稿.md', '机械测试旧文本。')
        self.review['reviewed_draft'] = self.ev('草稿与备选/旧稿.md')
        self.save(self.review_path, self.review)
        with self.assertRaisesRegex(ValueError, 'identify the current version'):
            cc.validate_reviews(self.root, [self.review_path], self.req['writer'], self.draft['sha256'])

    def test_major_reading_issue_needs_resolution_or_evidenced_authorized_retention(self):
        issue = dict(id='P1', severity='soft', priority='major', description='技术夹具主要问题',
                     evidence='技术定位', status='open')
        self.review['issues'] = [issue]
        self.save(self.review_path, self.review)
        with self.assertRaisesRegex(ValueError, 'unresolved major'):
            cc.validate_reviews(self.root, [self.review_path], self.req['writer'], self.draft['sha256'])
        issue.update(status='waived', retention_authorization=dict(source='技术夹具作者决定', scope='本段'))
        self.save(self.review_path, self.review)
        with self.assertRaisesRegex(ValueError, 'reading-impact reason'):
            cc.validate_reviews(self.root, [self.review_path], self.req['writer'], self.draft['sha256'])
        issue.update(retention_reason='技术夹具：已说明保留的阅读代价',
                     retention_evidence=self.ev('审稿与修订/技术依据.md'))
        self.save(self.review_path, self.review)
        self.assertTrue(cc.validate_reviews(self.root, [self.review_path], self.req['writer'], self.draft['sha256']))
        issue.update(priority='optional', status='open')
        self.save(self.review_path, self.review)
        self.assertTrue(cc.validate_reviews(self.root, [self.review_path], self.req['writer'], self.draft['sha256']))

    def test_prose_report_is_recomputed_and_hard_errors_block(self):
        self.chapter_ready({'chapter_length': {'hard_max': 0}})
        before = nc.inventory(self.root)
        with self.assertRaisesRegex(ValueError, 'violates confirmed mechanical policy'):
            cc.adopt(self.root, self.req)
        self.assertEqual(before, nc.inventory(self.root))
        forged = json.loads((self.root / self.prose_path).read_text(encoding='utf-8'))
        forged['result']['ok'] = True
        self.save(self.prose_path, forged)
        self.req['prose_check'] = self.ev(self.prose_path)
        self.register_fixture_artifacts()
        self.accept_recheck()
        with self.assertRaisesRegex(ValueError, 'recomputed result'):
            cc.adopt(self.root, self.req)

    def test_policy_cannot_be_relaxed_by_adoption_request(self):
        self.chapter_ready({'chapter_length': {'hard_max': 0}})
        self.save('创作约定/临时宽松.json', {})
        report = dict(draft_sha256=self.draft['sha256'], policy=self.ev('创作约定/临时宽松.json'),
                      result=prosecheck.check_text((self.root / self.draft['path']).read_text(encoding='utf-8'), {}))
        self.save(self.prose_path, report)
        self.req['prose_check'] = self.ev(self.prose_path)
        self.register_fixture_artifacts()
        self.accept_recheck()
        with self.assertRaisesRegex(ValueError, 'current draft/policy/recomputed'):
            cc.adopt(self.root, self.req)

    def test_incremental_review_requires_full_baseline_and_new_report(self):
        self.save('审稿与修订/完整旧评审.json', self.review)
        baseline_review = self.ev('审稿与修订/完整旧评审.json')
        old_draft = dict(self.draft)
        self.write('草稿与备选/第01章-草稿-v2.md', '机械测试文本二。\n')
        current = self.ev('草稿与备选/第01章-草稿-v2.md')
        self.write('审稿与修订/增量阅读报告.md', '技术夹具：实际增量及相关范围阅读的新记录。')
        self.save('审稿与修订/改动映射.json', dict(old_draft=old_draft, new_draft=current, summary='技术字符变更',
                                                diff=cc.draft_diff(self.root, old_draft, current)))
        incremental = dict(self.review, draft_sha256=current['sha256'], current_draft=current,
                           reviewed_draft=current, report=self.ev('审稿与修订/增量阅读报告.md'),
                           reading=dict(mode='incremental', scope='变更所在段落', related_scope='相关上下文与前后承接'),
                           base_review=baseline_review, base_draft=old_draft,
                           change_map=self.ev('审稿与修订/改动映射.json'))
        self.save(self.review_path, incremental)
        self.assertTrue(cc.validate_reviews(self.root, [self.review_path], self.req['writer'], current['sha256']))
        incremental['report'] = self.review['report']
        self.save(self.review_path, incremental)
        with self.assertRaisesRegex(ValueError, 'new reviewer report'):
            cc.validate_reviews(self.root, [self.review_path], self.req['writer'], current['sha256'])
        incremental['report'] = self.ev('审稿与修订/增量阅读报告.md')
        wrong = dict(old_draft=current, new_draft=current, summary='错误映射')
        self.save('审稿与修订/改动映射.json', wrong)
        incremental['change_map'] = self.ev('审稿与修订/改动映射.json')
        self.save(self.review_path, incremental)
        with self.assertRaisesRegex(ValueError, 'bind old and new'):
            cc.validate_reviews(self.root, [self.review_path], self.req['writer'], current['sha256'])

    def test_submit_cli_and_checks_preserve_unknown_notes(self):
        evidence = self.ev('审稿与修订/技术依据.md')
        request = dict(stage='S01', task='direction', outputs=[evidence], checks=self.checks('direction', [evidence]))
        proc = subprocess.run([sys.executable, str(Path(tc.__file__)), 'submit', '--book', str(self.root),
                               '--request', json.dumps(request, ensure_ascii=False)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['status'], 'submitted')

    def test_diff_cli_generates_exact_binding_without_writing(self):
        self.write('草稿与备选/第二技术版本.md', '机械测试二。\r\n')
        before = nc.inventory(self.root)
        proc = subprocess.run([sys.executable, str(Path(cc.__file__)), 'diff', '--book', str(self.root),
                               '--old', self.draft['path'], '--new', '草稿与备选/第二技术版本.md', '--summary', '技术差异'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result, cc.change_map(self.root, self.draft['path'], '草稿与备选/第二技术版本.md', '技术差异'))
        self.assertIn('diff', result)
        self.assertEqual(before, nc.inventory(self.root))

    def test_cli_prose_envelope_with_bom_crlf_is_accepted_without_normalization(self):
        raw = '\ufeff# 技术标题\r\n他说：「技术夹具。」\r\n'.encode('utf-8')
        (self.root / self.draft['path']).write_bytes(raw)
        self.draft = self.ev(self.draft['path'])
        self.review.update(draft_sha256=self.draft['sha256'], reviewed_draft=dict(self.draft),
                           current_draft=dict(self.draft))
        self.save(self.review_path, self.review)
        self.req['authorization']['approved_sha256'] = self.draft['sha256']
        self.save(self.policy_path, {'quote_style': 'chinese_double', 'quote_confirmed': False})
        proc = subprocess.run([sys.executable, str(Path(prosecheck.__file__)), 'check', '--book', str(self.root),
                               '--file', self.draft['path'], '--policy', self.policy_path],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.save(self.prose_path, json.loads(proc.stdout))
        self.req['prose_check'] = self.ev(self.prose_path)
        self.chapter_ready()
        self.assertTrue(cc.adopt(self.root, self.req)['ok'])
        self.assertEqual((self.root / self.req['target']).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
