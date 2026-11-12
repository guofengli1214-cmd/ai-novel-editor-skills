#!/usr/bin/env python3
"""Read-only mechanical prose checks; never judge or rewrite literary content.

API: check_text(text, policy=None) -> JSON-compatible dict. Locations are
1-based positions in the original input, including excluded metadata/headings.
HTML comments and Markdown/Chinese ordinal headings do not enter counts or
punctuation checks. Counts describe characters, not a literary quality score.

Policy (all fields optional)::

    {"quote_style": "chinese_double", "quote_confirmed": false,
     "chapter_length": {"count_by": "han", "target_min": 2000,
                        "target_max": 4000, "hard_max": 5000}}

quote_style also accepts "corner" or "unspecified". The default recommends
Chinese double quotation marks, without claiming author confirmation. A style
mismatch becomes an error only when quote_confirmed is true AND a conservative
speech-tag heuristic identifies direct dialogue. Other style suspicions remain
warnings. Legal nested quotations are left alone. Unicode quote pairing errors
and explicit hard_max violations are errors; length targets are only warnings.
An ongoing outer quotation may repeat its opening mark at the start of a later
paragraph, with a single closing mark at the end of the quoted passage.
Explicit speech tags near a repeated opener require human review: they may
identify another speaker whose preceding quotation needs its own closing mark.
No heuristic can identify all dialogue; review warnings in their actual context.

CLI: prosecheck.py check --file PATH [--policy JSONFILE] [--book BOOK]
With --book, both paths must be canonical relative paths inside the work and
--policy is required. Output is {draft_sha256, policy: {path, sha256}, result};
hashes cover the exact bytes read, including comments and line endings. This
envelope can be saved externally as adoption evidence; the script saves nothing.
Exit status: 0 (no errors), 1 (text errors), 2 (configuration/read errors).
The command writes JSON to stdout and never writes the source or policy file.
"""
import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re
import sys


QUOTE_PAIRS = {'“': '”', '‘': '’', '「': '」', '『': '』'}
QUOTE_CLOSE = {close: opening for opening, close in QUOTE_PAIRS.items()}
ORDINAL = '0-9０-９〇零一二三四五六七八九十百千万两'
ATX_HEADING = re.compile(r'^ {0,3}#{1,6}(?:[ \t]+|$)')
PLAIN_HEADING = re.compile(
    r'^ {0,3}(?:第[' + ORDINAL + r']+[章节回卷部](?:[^\r\n。！？!?；;]{0,80})'
    r'|[〇零一二三四五六七八九十百千万两]+(?:更)?[ \t\u3000]+[^\r\n。！？!?；;]{1,80})[ \t]*$')
SETEXT = re.compile(r'^ {0,3}(?:=+|-+)[ \t]*$')
SPEECH_BEFORE = re.compile(
    r'(?:说|说道|问|问道|答|答道|回答|喊|喊道|叫道|低语|回应|补充)[：:][ \t]*$')
SPEECH_AFTER = re.compile(
    r'^[，,]?[ \t]*[^，。！？!?：:“”「」『』‘’\r\n]{0,12}'
    r'(?:说|说道|问|问道|答|答道|回答|喊|喊道|叫道)[，。！？!?:： \t]')


def _policy(policy):
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ValueError('policy must be a JSON object')
    unknown = set(policy) - {'quote_style', 'quote_confirmed', 'chapter_length'}
    if unknown:
        raise ValueError('unknown policy fields: ' + ', '.join(sorted(unknown)))
    style = policy.get('quote_style', 'chinese_double')
    if style not in ('chinese_double', 'corner', 'unspecified'):
        raise ValueError('quote_style must be chinese_double, corner or unspecified')
    confirmed = policy.get('quote_confirmed', False)
    if not isinstance(confirmed, bool):
        raise ValueError('quote_confirmed must be a boolean')
    length = policy.get('chapter_length', {})
    if not isinstance(length, dict):
        raise ValueError('chapter_length must be an object')
    unknown = set(length) - {'count_by', 'target_min', 'target_max', 'hard_max'}
    if unknown:
        raise ValueError('unknown chapter_length fields: ' + ', '.join(sorted(unknown)))
    count_by = length.get('count_by', 'han')
    if count_by not in ('han', 'non_whitespace'):
        raise ValueError('chapter_length.count_by must be han or non_whitespace')
    normalized = {'count_by': count_by}
    for key in ('target_min', 'target_max', 'hard_max'):
        if key in length:
            value = length[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError('chapter_length.' + key + ' must be a non-negative integer')
            normalized[key] = value
    low, high, hard = (normalized.get(k) for k in ('target_min', 'target_max', 'hard_max'))
    if low is not None and high is not None and low > high:
        raise ValueError('target_min must not exceed target_max')
    if hard is not None and any(v is not None and v > hard for v in (low, high)):
        raise ValueError('length targets must not exceed hard_max')
    return {'quote_style': style, 'quote_confirmed': confirmed, 'chapter_length': normalized}


def _masked_body(text):
    """Use spaces for excluded characters so original offsets never move."""
    chars = list(text)

    def exclude(start, end):
        for index in range(start, end):
            if chars[index] not in '\r\n':
                chars[index] = ' '

    for match in re.finditer(r'<!--.*?-->', text, re.DOTALL):
        exclude(match.start(), match.end())
    if chars and chars[0] == '\ufeff':
        chars[0] = ' '
    lines = ''.join(chars).splitlines(keepends=True)
    offset = 0
    for index, line in enumerate(lines):
        content = line.rstrip('\r\n')
        is_setext = (content.strip() and index + 1 < len(lines)
                     and SETEXT.fullmatch(lines[index + 1].rstrip('\r\n')))
        if ATX_HEADING.match(content) or PLAIN_HEADING.fullmatch(content) or is_setext:
            exclude(offset, offset + len(line))
            if is_setext:
                exclude(offset + len(line), offset + len(line) + len(lines[index + 1]))
        offset += len(line)
    return ''.join(chars)


def _is_han(character):
    return ('\u3400' <= character <= '\u4dbf' or '\u4e00' <= character <= '\u9fff'
            or '\uf900' <= character <= '\ufaff' or '\U00020000' <= character <= '\U000323af')


def _direct_dialogue(body, start, end):
    before = re.split(r'[\r\n]', body[max(0, start - 64):start])[-1]
    after = re.split(r'[\r\n]', body[end + 1:end + 65])[0]
    return bool(SPEECH_BEFORE.search(before) or SPEECH_AFTER.match(after))


def check_text(text, policy=None):
    """Return counts and localized findings without changing inputs or files.

    Invalid text/policy types or contradictory bounds raise ValueError. A true
    ``ok`` means no mechanical errors were found, not that prose is publishable.
    ``quote_confirmed`` is a supplied assertion, not proof of author approval.
    """
    if not isinstance(text, str):
        raise ValueError('text must be a string')
    settings = _policy(policy)
    body = _masked_body(text)
    line_starts = [0] + [match.end() for match in re.finditer(r'\r\n|\r|\n', text)]
    findings = []

    def report(severity, code, position, message):
        line = bisect_right(line_starts, position)
        findings.append({'severity': severity, 'code': code, 'line': line,
                         'column': position - line_starts[line - 1] + 1,
                         'message': message})

    han_positions = [index for index, char in enumerate(body) if _is_han(char)]
    visible_positions = [index for index, char in enumerate(body) if not char.isspace()]
    count_by = settings['chapter_length']['count_by']
    positions = han_positions if count_by == 'han' else visible_positions
    counts = {'han': len(han_positions), 'non_whitespace': len(visible_positions),
              'selected': len(positions), 'count_by': count_by}

    # Only completed outer quotations can receive style findings. A nested
    # citation may legitimately use a different pair from the outer dialogue.
    stack = []
    spans = []
    continuations = {}
    for index, char in enumerate(body):
        if char == '’' and index and index + 1 < len(body):
            if body[index - 1].isascii() and body[index - 1].isalnum() \
                    and body[index + 1].isascii() and body[index + 1].isalnum():
                continue  # English typographic apostrophe, e.g. tool’s.
        if char == '"':
            slashes = 0
            cursor = index - 1
            while cursor >= 0 and body[cursor] == '\\':
                slashes += 1
                cursor -= 1
            if slashes % 2:
                continue
            if stack and stack[-1][0] == '"':
                opening, start, outer = stack.pop()
                spans.append((opening, start, index, outer))
            else:
                stack.append((char, index, not stack))
        elif char in QUOTE_PAIRS:
            # A multi-paragraph quotation repeats the opening mark on each
            # paragraph and closes only the last paragraph. Keep one span so a
            # legal continuation cannot become an adoption-blocking false error.
            line_start = max(body.rfind('\n', 0, index), body.rfind('\r', 0, index)) + 1
            if (len(stack) == 1 and stack[-1][0] == char
                    and line_start > stack[-1][1]
                    and not body[line_start:index].strip()):
                continuations.setdefault(stack[-1][1], []).append(index)
                continue
            if any(item[0] == char for item in stack):
                report('warning', 'QUOTE_NESTING', index,
                       '同类引号重复嵌套，请人工核对是否应改用内层引号。')
            stack.append((char, index, not stack))
        elif char in QUOTE_CLOSE:
            expected = QUOTE_CLOSE[char]
            if stack and stack[-1][0] == expected:
                opening, start, outer = stack.pop()
                spans.append((opening, start, index, outer))
            elif any(item[0] == expected for item in stack):
                report('error', 'QUOTE_CROSSED', index, '引号闭合次序交叉，请核对外层和内层引号。')
                while stack:
                    if stack.pop()[0] == expected:
                        break
            else:
                report('error', 'QUOTE_UNEXPECTED_CLOSE', index, '闭引号没有匹配的开引号。')
    for opening, start, _outer in stack:
        report('warning' if opening == '"' else 'error', 'QUOTE_UNCLOSED', start,
               '开引号未闭合；请结合上下文核对。')

    for _opening, start, end, outer in spans:
        repeated = continuations.get(start, []) if outer else []
        if not repeated:
            continue
        suspicious = set()
        for position in repeated:
            preceding = body[max(start, position - 128):position].rstrip().splitlines()
            if preceding and SPEECH_BEFORE.search(preceding[-1]):
                suspicious.add(position)
        following = re.split(r'[\r\n]', body[end + 1:end + 65])[0]
        if SPEECH_AFTER.match(following):
            suspicious.add(repeated[-1])
        for position in sorted(suspicious):
            report('warning', 'QUOTE_CONTINUATION', position,
                   '跨段续开附近存在说话标签，可能属于新发言；请核对说话人及前段是否缺闭引号。')

    wanted = {'chinese_double': '“', 'corner': '「'}.get(settings['quote_style'])
    if wanted:
        for opening, start, end, outer in spans:
            if not outer or opening == wanted:
                continue
            dialogue = _direct_dialogue(body, start, end)
            severity = 'error' if settings['quote_confirmed'] and dialogue else 'warning'
            report(severity, 'QUOTE_STYLE', start,
                   '外层引号与%s不一致；%s。' % (
                       '已提供的作者约定' if settings['quote_confirmed'] else '默认建议',
                       '检测到直接对白结构' if dialogue else '未确证为对白，请人工判断引用用途'))

    for match in re.finditer(r'\.{2,}|…+|⋯+|。{2,}', body):
        if match.group() != '……':
            report('warning', 'ELLIPSIS_STYLE', match.start(),
                   '疑似省略号写法不统一；常规中文省略号为“……”，请按作者约定核对。')
    for match in re.finditer(r'-{2,}|—+|–+|─+|－{2,}', body):
        if match.group() != '——':
            report('warning', 'DASH_STYLE', match.start(),
                   '疑似破折号写法不统一；常规中文破折号为“——”，请人工核对用途。')

    bounds = settings['chapter_length']
    selected = counts['selected']
    if 'target_min' in bounds and selected < bounds['target_min']:
        report('warning', 'CHAPTER_TARGET_SHORT', 0,
               '当前%s计数%d低于软目标%d；请核对篇幅意图，不自动补写。' % (
                   count_by, selected, bounds['target_min']))
    if 'target_max' in bounds and selected > bounds['target_max']:
        report('warning', 'CHAPTER_TARGET_LONG', positions[bounds['target_max']],
               '当前%s计数%d高于软目标%d；请人工决定压缩或调整约定。' % (
                   count_by, selected, bounds['target_max']))
    if 'hard_max' in bounds and selected > bounds['hard_max']:
        report('error', 'CHAPTER_HARD_MAX', positions[bounds['hard_max']],
               '当前%s计数%d超过明确硬上限%d。' % (count_by, selected, bounds['hard_max']))
    findings.sort(key=lambda item: (item['line'], item['column'], item['code']))
    return {'ok': not any(item['severity'] == 'error' for item in findings),
            'counts': counts, 'findings': findings, 'policy': settings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='只读检查正文，输出 JSON')
    check.add_argument('--file', required=True)
    check.add_argument('--policy')
    check.add_argument('--book', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.book:
            if not args.policy:
                raise ValueError('--policy is required when --book is supplied')
            # Reuse the file protocol's traversal/symlink/reparse checks. Disable
            # bytecode caches so even this optional import causes no file write.
            sys.dont_write_bytecode = True
            import novelctl as nc
            root = nc.root_path(args.book)
            source_file = nc.relative_path(root, args.file)
            policy_file = nc.relative_path(root, args.policy)
        else:
            source_file = Path(args.file)
            policy_file = Path(args.policy) if args.policy else None
        policy_bytes = policy_file.read_bytes() if policy_file else None
        policy = json.loads(policy_bytes.decode('utf-8-sig')) if policy_bytes is not None else None
        if policy_file and not isinstance(policy, dict):
            raise ValueError('policy file must contain a JSON object')
        source_bytes = source_file.read_bytes()
        checked = check_text(source_bytes.decode('utf-8'), policy)
        if args.book:
            result = {'draft_sha256': hashlib.sha256(source_bytes).hexdigest(),
                      'policy': {'path': args.policy,
                                 'sha256': hashlib.sha256(policy_bytes).hexdigest()},
                      'result': checked}
        else:
            result = dict(checked, file=args.file)
    except (OSError, UnicodeError, ValueError) as exc:
        result = {'ok': False, 'counts': {}, 'findings': [
            {'severity': 'error', 'code': 'INPUT_ERROR', 'line': 1, 'column': 1,
             'message': str(exc)}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if checked['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
