#!/usr/bin/env python3
"""Run unittest discovery and preserve platform, source hashes and JUnit XML.

No third-party test dependencies. This runner never labels Linux simulations
as Windows runs; Wine is reported separately when its registry is available.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import unittest
import xml.etree.ElementTree as ET


class ReportingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.monotonic()
        self.records[test.id()] = dict(id=test.id(), status='passed', details='')
        super().startTest(test)

    def stopTest(self, test):
        self.records[test.id()]['seconds'] = time.monotonic() - self.started[test.id()]
        super().stopTest(test)

    def addError(self, test, err):
        super().addError(test, err)
        self.records[test.id()].update(status='error', details=self.errors[-1][1])

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.records[test.id()].update(status='failed', details=self.failures[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.records[test.id()].update(status='skipped', details=reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.records[test.id()].update(status='expected_failure', details=self.expectedFailures[-1][1])

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.records[test.id()].update(status='unexpected_success', details='Expected failure passed unexpectedly')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.records[test.id()].update(status='failed' if issubclass(err[0], test.failureException) else 'error',
                                          details=self._exc_info_to_string(err, test))


def runtime_kind():
    if os.name != 'nt':
        return platform.system().lower()
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Wine'):
            return 'wine'
    except FileNotFoundError:
        return 'windows'


def source_manifest(root):
    files = list((root / 'novel-studio').rglob('*.py'))
    files += list((root / 'tests').glob('*.py'))
    files += list((root / '.github/workflows').glob('*.yml'))
    return {file.relative_to(root).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(files)}


def write_reports(folder, result, elapsed, manifest):
    folder.mkdir(parents=True, exist_ok=True)
    environment = dict(runtime=runtime_kind(), os_name=os.name, platform=platform.platform(),
                       python=sys.version, executable=sys.executable,
                       architecture=platform.machine(), windows_version=platform.win32_ver())
    report = dict(ok=result.wasSuccessful(), tests=result.testsRun, failures=len(result.failures),
                  errors=len(result.errors), skipped=len(result.skipped), seconds=elapsed,
                  environment=environment, source_sha256=manifest,
                  cases=list(result.records.values()))
    (folder / 'test-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    suite = ET.Element('testsuite', name='novel-studio', tests=str(result.testsRun),
                       failures=str(len(result.failures)), errors=str(len(result.errors)),
                       skipped=str(len(result.skipped)), time=f'{elapsed:.6f}')
    properties = ET.SubElement(suite, 'properties')
    for key, value in environment.items():
        ET.SubElement(properties, 'property', name=key, value=str(value))
    for record in result.records.values():
        module, _, name = record['id'].rpartition('.')
        case = ET.SubElement(suite, 'testcase', classname=module, name=name,
                             time=f"{record.get('seconds', 0):.6f}")
        status = record['status']
        if status in ('skipped', 'expected_failure'):
            ET.SubElement(case, 'skipped', message=record['details'])
        elif status in ('failed', 'unexpected_success', 'error'):
            ET.SubElement(case, 'error' if status == 'error' else 'failure').text = record['details']
    ET.ElementTree(suite).write(folder / 'test-results.xml', encoding='utf-8', xml_declaration=True)
    print(json.dumps({key: report[key] for key in ('ok', 'tests', 'failures', 'errors', 'skipped', 'seconds')}, ensure_ascii=False))
    print(json.dumps(environment, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--pattern', default='test*.py')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    manifest = source_manifest(root)
    suite = unittest.defaultTestLoader.discover(str(root / 'tests'), pattern=args.pattern)
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReportingResult).run(suite)
    write_reports(args.report_dir.resolve(), result, time.monotonic() - started, manifest)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
