"""Track story records without binding historical tasks to mutable live copies."""
import copy
from pathlib import Path
import unittest

import test_task_enforcement as enforcement
import test_editorial_gates as editorial
import chapterctl as cc
import novelctl as nc
import taskctl as tc


class MaterialArtifactTests(unittest.TestCase):
    def setUp(self):
        self.fixture = enforcement.TaskEnforcementTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return dict(path=name, sha256=nc.digest(path))

    def test_only_unchanged_initialization_skeletons_are_exempt(self):
        self.assertEqual(tc.artifact_candidates(self.root), [])
        name = next(iter(nc.MANAGEMENT_FILES))
        self.write(name, nc.MANAGEMENT_FILES[name] + '\n技术夹具新增内容。')
        self.assertIn(name, dict(tc.artifact_candidates(self.root)))
        self.assertFalse(tc.check(self.root)['ok'])

    def test_unregistered_records_in_every_story_directory_block_execution(self):
        for folder in ('人物', '世界与地点', '连贯性记录', '线索与悬念',
                       '故事线', '曲线与节奏', '摘要', '关系与成长', '事件', '资源', '参考资料', '自定义资料'):
            with self.subTest(folder=folder):
                entry = self.write(folder + '/未登记资料.md', '合成资料检查，不是小说。')
                before = nc.inventory(self.root)
                self.assertIn(entry['path'], dict(tc.artifact_candidates(self.root)))
                self.assertFalse(tc.check(self.root)['ok'])
                with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
                    tc.gate(self.root, 'S01', 'direction')
                self.assertEqual(tc.gate(self.root, 'S01', 'direction', neutral=True)['error'], 'gate_not_ready')
                self.assertEqual(nc.inventory(self.root), before)
                (self.root / entry['path']).unlink()

    def test_unregistered_reference_attachments_block_execution_for_any_format(self):
        for filename, content in (('原件.pdf', b'%PDF-technical-fixture'),
                                  ('原件.html', b'<p>technical fixture</p>'),
                                  ('图像.png', b'\x89PNG\r\n\x1a\n\x00fixture'),
                                  ('附件/无扩展名', b'\x00technical fixture')):
            with self.subTest(filename=filename):
                path = self.root / '参考资料' / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                before = nc.inventory(self.root)
                checked = tc.check(self.root)
                self.assertFalse(checked['ok'])
                self.assertTrue(any(path.relative_to(self.root).as_posix() in issue
                                    for issue in checked['artifact_blockers']))
                with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
                    tc.gate(self.root, 'S01', 'direction')
                self.assertEqual(nc.inventory(self.root), before)
                path.unlink()
        # Existing unrelated directories keep their previous attachment rules.
        unrelated = self.root / '自定义资料' / '附件.pdf'
        unrelated.parent.mkdir(exist_ok=True)
        unrelated.write_bytes(b'%PDF-unrelated-fixture')
        self.assertTrue(tc.gate(self.root, 'S01', 'direction')['ok'])

    def test_saved_reference_sources_can_be_reused_with_their_registered_versions(self):
        tc.update(self.root, dict(stage='S01', task='direction', status='doing'))
        note = self.write('参考资料/技术主题.md', '技术夹具：支持范围、原件入口及未核实项。')
        sources = [note]
        original_bytes = {}
        for filename, content in (('原件.pdf', b'%PDF-technical-fixture'),
                                  ('原件.html', b'<p>technical fixture</p>'),
                                  ('图像.png', b'\x89PNG\r\n\x1a\n\x00fixture')):
            name = '参考资料/原始附件/' + filename
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            sources.append(dict(path=name, sha256=nc.digest(path)))
            original_bytes[name] = content
        fixed = self.write('历史版本/资料依据/参考资料-v1.md', '固定技术依据；本轮保存的来源及核验版本。')
        fixed['material_files'] = sources
        tc.submit(self.root, dict(stage='S01', task='direction', outputs=[fixed]))
        tc.update(self.root, dict(stage='S01', task='direction', status='done',
                                  verification=self.fixture.verification))
        self.assertTrue(tc.check(self.root)['ok'])

        # A later task binds the persisted files directly, without recollecting them.
        tc.update(self.root, dict(stage='S01', task='concept', status='doing', inputs=sources))
        before = nc.inventory(self.root)
        self.assertTrue(tc.gate(self.root, 'S01', 'concept')['ok'])
        self.assertEqual(nc.inventory(self.root), before)
        reused = self.write('历史版本/资料依据/复用核对.md', '技术夹具：使用本书已保存资料的核对记录。')
        tc.submit(self.root, dict(stage='S01', task='concept', outputs=[reused]))
        tc.update(self.root, dict(stage='S01', task='concept', status='done',
                                  verification=self.fixture.verification))
        task = tc.selected_task(tc.load_plan(self.root)['stages'][0], 'concept')
        self.assertEqual(task['inputs'], sources)
        self.assertTrue(tc.gate(self.root, 'S01', 'next_plan')['ok'])

        for name, content in original_bytes.items():
            with self.subTest(changed=name):
                path = self.root / name
                path.write_bytes(content + b'changed')
                self.assertFalse(tc.check(self.root)['ok'])
                with self.assertRaises(ValueError):
                    tc.gate(self.root, 'S01', 'next_plan')
                path.write_bytes(content)
                self.assertTrue(tc.gate(self.root, 'S01', 'next_plan')['ok'])

    def test_reference_notes_can_evolve_while_later_tasks_reuse_a_fixed_copy(self):
        tc.update(self.root, dict(stage='S01', task='direction', status='doing'))
        first_text = '技术主题 v1：已收集的资料摘要及尚未核实的范围。'
        note = self.write('参考资料/活动主题.md', first_text)
        first = self.write('历史版本/资料依据/主题-v1.md', first_text)
        first['material_files'] = [note]
        tc.submit(self.root, dict(stage='S01', task='direction', outputs=[first]))
        tc.update(self.root, dict(stage='S01', task='direction', status='done',
                                  verification=self.fixture.verification))
        fixed_input = {key: first[key] for key in ('path', 'sha256')}
        original_bytes = (self.root / first['path']).read_bytes()

        tc.update(self.root, dict(stage='S01', task='concept', status='doing', inputs=[fixed_input]))
        second_text = first_text + '\n技术补充 v2：追加资料及本次补查结论。'
        updated = self.write(note['path'], second_text)
        second = self.write('历史版本/资料依据/主题-v2.md', second_text)
        second['material_files'] = [updated]
        tc.submit(self.root, dict(stage='S01', task='concept', outputs=[second]))
        tc.update(self.root, dict(stage='S01', task='concept', status='done',
                                  verification=self.fixture.verification))

        self.assertTrue(tc.check(self.root)['ok'])
        self.assertTrue(tc.gate(self.root, 'S01', 'next_plan')['ok'])
        task = tc.selected_task(tc.load_plan(self.root)['stages'][0], 'concept')
        self.assertEqual(task['inputs'], [fixed_input])
        self.assertEqual((self.root / first['path']).read_bytes(), original_bytes)
        self.assertEqual(nc.digest(self.root / first['path']), first['sha256'])
        self.assertEqual((self.root / updated['path']).read_bytes(),
                         (self.root / second['path']).read_bytes())
        self.assertNotEqual(updated['sha256'], note['sha256'])

    def test_author_input_recovery_applies_to_story_records(self):
        entry = self.write('人物/作者材料.md', '作者提供材料的技术占位。')
        self.assertFalse(tc.check(self.root)['ok'])
        tc.recover(self.root, self.fixture.recovery(path=entry['path']))
        self.assertTrue(tc.check(self.root)['ok'])
        self.assertTrue(tc.gate(self.root, 'S01', 'direction')['ok'])

    def test_current_record_versions_are_owned_by_immutable_task_outputs(self):
        for kind, version in (('direction', 'v1'), ('concept', 'v2')):
            tc.update(self.root, dict(stage='S01', task=kind, status='doing'))
            current = self.write('人物/当前资料.md', '合成资料' + version)
            snapshot = self.write('历史版本/资料依据/' + version + '.md', '合成资料' + version)
            snapshot['material_files'] = [current]
            tc.submit(self.root, dict(stage='S01', task=kind, outputs=[snapshot]))
            tc.update(self.root, dict(stage='S01', task=kind, status='done',
                                      verification=self.fixture.verification))
            self.assertTrue(tc.check(self.root)['ok'])
        self.assertTrue(tc.gate(self.root, 'S01', 'next_plan')['ok'])
        self.write('人物/当前资料.md', '未经登记的新修改。')
        self.assertFalse(tc.check(self.root)['ok'])

    def test_material_files_cannot_preregister_or_hide_other_task_outputs(self):
        current = self.write('人物/当前资料.md', '合成资料。')
        snapshot = self.write('历史版本/固定资料.md', '合成固定依据。')
        snapshot['material_files'] = [current]
        with self.assertRaisesRegex(ValueError, 'preregister outputs'):
            tc.update(self.root, dict(stage='S01', task='direction', status='doing', outputs=[snapshot]))
        snapshot['material_files'] = [dict(path='草稿与备选/旧稿.md', sha256=current['sha256'])]
        with self.assertRaisesRegex(ValueError, 'live story records|stale material file'):
            tc.submit(self.root, dict(stage='S01', task='direction', outputs=[snapshot]))

    def test_changed_material_file_is_rejected_at_submission(self):
        tc.update(self.root, dict(stage='S01', task='direction', status='doing'))
        current = self.write('人物/当前资料.md', '原始合成资料。')
        snapshot = self.write('历史版本/固定资料.md', '原始合成资料。')
        snapshot['material_files'] = [current]
        self.write(current['path'], '已经改变的资料。')
        with self.assertRaisesRegex(ValueError, 'stale material file'):
            tc.submit(self.root, dict(stage='S01', task='direction', outputs=[snapshot]))

    def test_sync_record_owns_current_updates_until_finish(self):
        f = editorial.EditorialGateTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.chapter_ready()
        operation = cc.adopt(f.root, f.req)
        f.prepare_sync(operation)
        self.assertTrue(tc.gate(f.root, 'S02', 'sync')['ok'])
        cc.finish(f.root, operation)
        f.done('S02', 'sync', [f.ev(f.req['change_record'])])
        self.assertTrue(tc.check(f.root)['ok'])


    def synchronized_fixture(self, fixed_output=False):
        f = editorial.EditorialGateTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.chapter_ready()
        operation = cc.adopt(f.root, f.req)
        f.prepare_sync(operation)
        cc.finish(f.root, operation)
        output = (cc.validate_sync_receipt(f.root, operation['operation_id'])['change_record']
                  if fixed_output else f.ev(f.req['change_record']))
        f.done('S02', 'sync', [output])
        self.assertTrue(tc.check(f.root)['ok'])
        return f, operation

    def test_reopened_sync_keeps_sealed_material_provenance_but_blocks_new_edits(self):
        f, operation = self.synchronized_fixture()
        summary = f.root / '摘要/技术摘要.md'
        original = summary.read_bytes()
        tc.update(f.root, dict(stage='S02', task='sync', status='todo',
                               reason='重新核对已完成同步的技术依据，不产生新资料。'))
        checked = tc.check(f.root)
        self.assertTrue(checked['ok'], checked)
        self.assertFalse(any('技术摘要.md' in warning for warning in checked['warnings']))
        self.assertTrue(tc.gate(f.root, 'S02', 'sync')['ok'])
        summary.write_bytes(original + '未经登记的新技术资料。'.encode('utf-8'))
        self.assertFalse(tc.check(f.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
            tc.gate(f.root, 'S02', 'sync')
        summary.write_bytes(original)
        self.assertTrue(tc.gate(f.root, 'S02', 'sync')['ok'])
        tc.update(f.root, dict(stage='S02', task='sync', status='doing'))
        fixed = cc.validate_sync_receipt(f.root, operation['operation_id'])['change_record']
        f.done('S02', 'sync', [fixed])
        self.assertTrue(tc.check(f.root)['ok'])

    def test_reopened_adopt_recovers_receipt_provenance_from_verified_history(self):
        f, operation = self.synchronized_fixture(fixed_output=True)
        summary = f.ev('摘要/技术摘要.md')
        tc.update(f.root, dict(stage='S02', task='adopt', status='todo',
                               operation_id='', outputs=[],
                               reason='重新核验采用职责，保留真实完成历史。'))
        self.assertTrue(tc.check(f.root)['ok'])
        self.assertTrue(tc.gate(f.root, 'S02', 'adopt')['ok'])
        tc.update(f.root, dict(stage='S02', task='adopt', status='todo',
                               operation_id='f' * 32))
        self.assertTrue(tc.gate(f.root, 'S02', 'adopt')['ok'])
        data = tc.load_plan(f.root)
        self.assertIn((summary['path'], summary['sha256']),
                      tc.historical_sync_artifact_pairs(f.root, data))
        # A verified checkbox alone cannot claim another receipt's material files.
        unrelated = copy.deepcopy(data)
        stage = next(stage for stage in unrelated['stages'] if stage['id'] == 'S02')
        sync = tc.selected_task(stage, 'sync')
        for record in [sync] + sync['history']:
            record['outputs'] = [f.ev('审稿与修订/技术依据.md')]
        self.assertNotIn((summary['path'], summary['sha256']),
                         tc.historical_sync_artifact_pairs(f.root, unrelated))
        (f.root / summary['path']).write_text('新版本技术资料，不属于旧完成凭据。', encoding='utf-8')
        self.assertFalse(tc.check(f.root)['ok'])
        with self.assertRaisesRegex(ValueError, 'unregistered artifact'):
            tc.gate(f.root, 'S02', 'adopt')


if __name__ == '__main__':
    unittest.main()
