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
                       '故事线', '曲线与节奏', '摘要', '关系与成长', '事件', '资源', '自定义资料'):
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
