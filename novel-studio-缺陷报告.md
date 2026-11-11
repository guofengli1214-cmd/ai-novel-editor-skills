# novel-studio（ai-novel-editor-skills）缺陷报告

- 上游仓库：https://github.com/guofengli1214-cmd/ai-novel-editor-skills
- 审查版本：commit `3f8f41f`（2026-11-11）
- 审查范围：`novel-studio/SKILL.md`、5 个 `references/*.md`、4 个脚本共 1391 行、`tests/` 73 项
- 审查日期：2026-11-11
- 复现环境：Windows 11 中文版；Python 3.14.9（python.org 官方包）；git-bash(MSYS) 终端；书名/目录名均为中文；工作目录 `%TEMP%\ns-probe*`
- 说明：本报告所有"实际输出"均为真机执行所得，不是推断。凡是没能复现的只标为"未验证"。

## 一、总览

| 编号 | 级别 | 问题一句话 |
| --- | --- | --- |
| D1 | P0 阻断 | Windows 下 `chapterctl.save()` 用 `os.open(目录, O_RDONLY)` 做 fsync，抛 PermissionError，**所有写命令全废** |
| D2 | P1 | `adopt` 生成的 `历史版本/chapter-<id>/` 没有 `索引.md`，紧接着的 `finish` 被自己的 `check` 拒绝 |
| D3 | P1 | `finish` 的 handoff 校验只认「第N章」，不认技能自己规定的零填充章名「第0001章」 |
| D4 | P1 | 台账里出现第二个 `json` 代码块，`check` 与 `render` 同时失效，无法自愈 |
| D5 | P1 | Windows 目录联接（junction）不算符号链接，防护被绕过：会读作品外文件，`index` 会往作品外写文件 |
| D6 | P1 | 进程被抓杀后锁目录残留，所有写命令报错，无自动回收、无强制解锁参数 |
| D7 | P1 | `init` 不校验 Windows 尾随空格/非法字符，失败后留下半个作品目录，重试撞"已存在" |
| D8 | P2 | `check` 把代码块/行内代码里的 Markdown 链接当成失效链接，而 `finish` 要求 check 零问题 |
| D9 | P3 | pending 期间 `check` 把整份 adopt 请求原样打进 issues，报错信息被淹没 |
| D10 | P2 | 每次 `check` 全量校验所有快照，耗时 O(章数 × 快照数)，80 章 + 20 快照 = 8.7s，manifest 13.2s |
| D11 | P2 | `restore` 指向非快照目录时报原始 `FileNotFoundError`，不提示"这不是快照" |
| D12 | P3 | 全仓无 Windows 验证痕迹；README「73 项测试全部通过」在 Windows 不成立（3 项因 `os.symlink` 权限失败） |
| D13 | P3 | README 示例 `init --parent /我的作品库` 要求该目录已存在，否则 `book directory missing` |
| D14 | P3 | 文档统一用 `python3`；Windows 上 `python3` 常是微软商店占位符或根本不存在 |
| D15 | P3 | 仓库无 LICENSE 文件（README 自己承认） |
| D16 | P3 | `SKILL.md` frontmatter description 108 字，超出 Hermes 自建 skill 的 60 字惯例（仅影响索引显示） |
| D17 | P3 | SKILL.md + references 合计约 76KB，逐章按文档读取的上下文成本很高 |
| D18 | P3 | `agents/openai.yaml` 是 Codex 专用元数据，其他宿主不读 |

---

## 二、P0

### D1. Windows 下所有写命令必然失败（`chapterctl.save()` 目录 fsync）

**位置**：`novel-studio/scripts/chapterctl.py` 的 `save()`（上游 93 行；本机打补丁后为 97 行）

```python
def save(path, value):
    nc.atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    fd = os.open(path.parent, os.O_RDONLY)   # ← Windows 不允许这样打开目录
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
```

**影响面**：`save()` 被 `locked()` 调用（写锁的 `owner.json`），因此**所有走锁的写命令全部失败**：`taskctl init/plan/update/render/close`、`chapterctl adopt/finish`，以及经它们调用的 `novelctl init`（新书默认启用任务管理，内部会 `taskctl.initialize`）。实际表现是建书第一步就报错。

**复现**：
```bash
python novel-studio/scripts/novelctl.py init --parent D:/作品库 --title 雾港来信
python -m unittest discover -s tests     # 上游自带的 73 项
```

**实际输出**（Python 3.14.9 / Windows 11）：
```
PermissionError: [Errno 13] Permission denied:
'C:\\Users\\...\\tmp0hgo6dfj\\.chapterctl-33354a6f....lock'
```
测试结果：`Ran 73 tests — FAILED (failures=1, errors=50)`。

**根因**：POSIX 允许 `os.open(dir, O_RDONLY)` 拿到目录句柄再 `fsync`，Windows 上打开目录句柄需要 `FILE_FLAG_BACKUP_SEMANTICS`，Python 的 `os.open` 不提供，直接拒绝。

**建议修法**（任选）：
1. 目录 fsync 前判断平台：`if os.name == 'nt': return`（文件本体已由 `os.replace` 落盘，Windows 上目录 fsync 本来也没有对应语义）；
2. 或 `try/except OSError: pass` 包住目录 fsync，保留 POSIX 行为；
3. 顺带补一条 Windows CI 或至少 `os.name` 分支的单元测试。

**备注**：本机为了跑通审查，临时打了上面第 1 种补丁（详见文末附录），仓库本身未改动。补丁后同样 73 项测试为 `70 通过 / 3 error`，剩余 3 项见 D12。

---

## 三、P1

### D2. adopt 之后立刻 `check`/`finish` 会被"缺索引"顶回来

**位置**：
- `chapterctl.py` `_adopt()` 202 行：`backup.mkdir(parents=True)`，随后只写入 `state.json / draft.md / original.md / journal.json`，**没有 `索引.md`**；
- `novelctl.py` `check_book()` 174–189 行：要求每个非归档目录都有 `索引.md`，且索引里列出每个子项；
- `novelctl.py` `archived()` 129–132 行：只把含 `manifest.json` 的 `历史版本/<id>` 视为归档——`chapter-<uuid>` 备份目录**不满足**，所以按普通目录检查；
- `chapterctl.py` `finish()` 282–283 行：`finish` 要求 `check_book` **零问题**才能通过。

**复现**：
```bash
python chapterctl.py adopt  --book BOOK --request adopt.json
python novelctl.py  check  --book BOOK
python chapterctl.py finish --book BOOK --request finish.json
```

**实际输出**：
```
index missing entry: .: 第1章.md
index missing entry: 创作约定: 交接.md
index missing entry: 历史版本: chapter-7c46948806904c65b1efbe3c006cce37
missing index: 历史版本/chapter-7c46948806904c65b1efbe3c006cce37
...
```
只有先跑 `novelctl index` 再 `finish` 才能过。实际流程里只要资料子 agent 漏跑一次 `index`，`finish` 必然失败；报错虽然会点名 `历史版本/chapter-<id>`，但不会说明这是**adopt 自己刚建的备份目录**，容易被误判成"用户漏建索引"。

**另一个副作用**（实测）：随后跑 `novelctl index`，该备份目录里会多出 `索引.md`——实测内容为 `['draft.md', 'journal.json', 'state.json', '索引.md']`，即写入了一个本应只读、作为采用日志保留的目录。

**建议修法**（任选）：
1. `_adopt` 建备份目录时顺手写一个最小 `索引.md`（说明"本次采用的只读日志，勿编辑"）；
2. 或把 `archived()` 的判断从"有 manifest.json"扩展为"`历史版本/` 下的任何子目录都视为归档，不参与索引检查"，并在注释里写明理由；
3. 或 `finish` 调用 `check_book` 时额外允许 `历史版本/chapter-*` 这类目录暂缺索引。

---

### D3. `finish` 不认零填充章号，文档里的命名规范与校验规则打架

**位置**：`chapterctl.py` 278 行
```python
require(('第' + str(p['number']) + '章') in text or p['target'] in text, 'handoff does not name chapter')
```

**冲突点**：skill 规定正式章文件名是 `第0001章-章名.md`（四位补零，`file-protocol.md` §1、`story-records.md` §使用方式），而这里检查的是 `第1章`。写 `第0001章-雾港来信.md` 能过（因为 `p['target']` 命中），只写 `第0001章` 就被拒。

**复现**：交接文件写 `当前正式：第0001章。`，target 为 `第0001章-雾港来信.md`，执行 `finish`。

**实际输出**：
```
{"ok": false, "error": "handoff does not name chapter"}
```

**建议修法**：把匹配改成"接受 `第N章` 或 `第000N章` 或 `p['target']` 三者任一"，例如用正则 `第0*{n}章` 或同时检查 `p['target']` 的文件名主干；并在文档里写明交接必须出现哪一种写法。

---

### D4. 台账里出现第二个 `json` 代码块，`check` 与 `render` 同时失效

**位置**：`taskctl.py` 105–113 行
```python
blocks = re.findall(r'^```json\s*\n(.*?)\n```\s*$', text, re.M | re.S)
require(len(blocks) == 1, 'expected one authoritative fenced JSON block')
```

**冲突点**：`task-management.md` 第 7 行明确允许"生成区外可保留说明"。但只要这段说明里贴一个 JSON 示例（` ```json ` 语言标记），全局正则就会数到 2 个块，直接失败；而文档给的补救办法 `render` **走同一个 `extract()`**，同样失败，于是除了手工编辑文件没有出路。

**复现**：在新书台账 `创作约定/任务计划与验收.md` 末尾追加

    ```json
    {"示例": 1}
    ```

然后：
```bash
python taskctl.py render --book BOOK
python taskctl.py check  --book BOOK
```

**实际输出**：
```
{"ok": false, "error": "expected one authoritative fenced JSON block"}
{"ok": false, "enabled": true, "issues": ["expected one authoritative fenced JSON block"], "stale_tasks": []}
```

**建议修法**：只在 `<-- taskctl:begin -->` 与 `<-- taskctl:end -->` 之间搜索那个代码块（先切出受管区再匹配），而不是全文匹配；或要求受管块的围栏带唯一标记（如 ```` ```json taskctl ````）。

---

### D5. Windows junction 绕过符号链接防护，会读写作品目录以外的文件

**位置**：
- `novelctl.py` `root_path()` 46–52 行、`relative_path()` 55–64 行、`inventory()` 67–83 行：全部用 `Path.is_symlink()` 判软链；
- `index_book()` 135–158 行：对 `inventory()` 返回的每个目录写 `索引.md`。

**问题**：Windows 上目录联接（`mklink /J`，无需管理员，同步盘/目录重定向也常用）在 Python 里 `os.path.islink()` 返回 **False**，因此既不会被拦，也不会被 `os.walk(followlinks=False)` 跳过。

**复现**：
```cmd
mklink /J "BOOK\人物\外链" "D:\外部目录"
python novelctl.py index --book BOOK
```

**实际结果**（本机实测）：
- `inventory()` 读到 `人物/外链/秘密.md`（作品外文件参与哈希）；
- `D:\外部目录` 下多出 `索引.md`——**工具往作品目录外写了文件**；
- `snapshot` 会把外部内容复制进快照（本次未单独验证，按同一代码路径推断）。

**建议修法**：
1. Windows 上判软链用 `os.lstat(p).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT`（能覆盖 junction），再决定是否拒绝；
2. 或者在 `os.walk` 里对每个目录额外检查 reparse point，命中即报错；
3. 无论哪种，`index_book` 写文件前应确认目标路径仍在 `root` 之内（`Path.resolve().is_relative_to(root.resolve())`），这是纵深防御。

**补充**：以 junction 作为**作品根路径**本身是可用的（本机用 junction 打开作品目录，`check` 返回 `OK`），所以只需拦"作品内部指向外面"的情况。

---

### D6. 进程被抓杀后锁残留，没有自动回收

**位置**：`chapterctl.py` `locked()` 111–124 行

```python
lock = root.parent / ('.chapterctl-' + key + '.lock')
try:
    lock.mkdir()
except FileExistsError:
    raise ValueError('writer lock exists: ...；explicitly remove only after ...')
try:
    save(lock / 'owner.json', dict(pid=os.getpid(), book=str(root)))
    yield
finally:
    (lock / 'owner.json').unlink(missing_ok=True)
    lock.rmdir()
```

**问题**：锁是目录，靠 `finally` 释放。进程被 `kill -9`、宿主超时终止、断电时 `finally` 不执行，锁永久残留；之后**所有写命令**都被拒，且没有 `--force` / `--take-over` 参数。`owner.json` 里虽然写了 pid，脚本却从不读取它来判断持锁进程是否还活着。

**复现**：手工 `mkdir .chapterctl-<sha256(book.resolve())>.lock` 后执行任意写命令。

**实际输出**：
```
{"ok": false, "error": "writer lock exists: ...\\ns-probe\\.chapterctl-1738....lock; explicitly remove only after verifying no live writer ..."}
```

**建议修法**：
1. 启动时读 `owner.json` 的 pid，用 `os.kill(pid, 0)`（Windows 用 `OpenProcess`）判断是否仍存活，已死则自动接管并记日志；
2. 或提供 `--break-lock` 显式解锁命令（要求同时给出当前 `pending_operations` 状态）；
3. 至少在 `check` 输出里把"锁存在且 pid 已死"标成可自动修复的警告。

**为什么重要**：agent 宿主普遍会有执行超时，子 agent 被杀是常态，不是边缘情况。

---

### D7. `init` 不校验 Windows 非法/尾随空格标题，失败后留下半个作品目录

**位置**：`novelctl.py` `init_book()` 230–251 行
```python
if not title.strip() or title in ('.', '..') or '/' in title or '\\' in title:
    raise ValueError('title must be a single directory name')
...
book.mkdir()          # 237 行
for directory in DIRECTORIES:
    (book / directory).mkdir()   # 239 行
```

**问题一**：只拦 `/ \ . ..`，不拦 Windows 保留字符（`< > : " | ? *`）、保留名（`CON`、`NUL`、`PRN`…）、尾随空格与尾随点。Windows 会**悄悄去掉**尾随空格，于是 `book.mkdir()` 建出 `测试`，下一行的 `(book / '创作约定').mkdir()` 用的是带空格的路径，报 `WinError 3`。

**复现**：
```bash
python novelctl.py init --parent D:/作品库 --title "测试 "
```

**实际输出**：
```
error: [WinError 3] 系统找不到指定的路径。: 'D:\\作品库\\测试 \\创作约定'
```
**副作用**：磁盘上留下一个空的 `测试` 目录。再次用同名建书会撞 `WinError 183`（已存在），且没有任何提示告诉用户"上次失败留下的半成品"。

**问题二**：`parent = root_path(parent)`（235 行）要求作品库目录**已存在**，否则报 `book directory missing`；README 的示例 `init --parent /我的作品库` 没有说明要先建目录。

**建议修法**：
1. 标题校验加 Windows 保留字符/保留名/尾随空格与点（跨平台可用 `pathlib.PureWindowsPath` 校验，或直接拒绝 `[:*?"<>|]` 与 `[ .]$`）；
2. 建目录失败时清理已创建的半成品（或用临时目录 + 原子改名）；
3. `parent` 不存在时可选择自动创建，或在文档里写明"必须先建库目录"。

---

## 四、P2

### D8. `check` 把代码块里的 Markdown 链接当成失效链接，会卡住 `finish`

**位置**：`novelctl.py` `check_book()` 190–211 行——对每个 `.md` 文件全文跑两条链接正则，不区分代码块/行内代码。

**复现**：某资料文件里写
````
```
[示例](不存在的文件.md)
```
````
然后 `python novelctl.py check --book BOOK`。

**实际输出**：
```
invalid local md link/file link: 摘要/备注.md: 不存在的文件.md
```
因为 `finish` 要求 `check_book` 零问题（`chapterctl.py` 282 行），这条误报会直接阻断采用收尾。

**建议修法**：扫描前先剥离围栏代码块与行内代码（`re.sub(r'```.*?```', '', text, flags=re.S)` 与反引号片段），或只在"链接目标是本书内相对路径且确实像文件"时才报错。

---

### D10. 每次 `check` 全量校验所有快照，规模上会拖垮日常操作

**位置**：`novelctl.py` `check_book()` 167–173 行——对每个快照调用 `verify_snapshot()`，后者又跑三次 `inventory()`（快照目录、payload、根目录）。

**实测**（本机，机械盘 + Defender 常开）：
| 场景 | `novelctl check` | `bookctl manifest` |
| --- | --- | --- |
| 80 章、无快照 | 0.90s | 1.30s |
| 80 章 + 20 个快照（9.5MB） | 8.70s | 13.16s |

耗时随快照数线性增长（每个快照约 +0.4s），而 `finish` 每次都会跑 `check_book`。若每章采用都留快照，100 章时单次检查会在分钟级。

**建议修法**：给快照校验加缓存（快照目录 + `manifest.json` 的 mtime/size → 结论），或提供 `--skip-snapshots` 只读开关，或把"快照完好性"独立成 `novelctl verify-snapshots` 命令。

---

### D11. `restore` 指向非快照目录时报原始异常

**位置**：`novelctl.py` `verify_snapshot()` 276–289 行

**复现**：`python novelctl.py restore --book BOOK --snapshot BOOK/摘要`

**实际输出**：
```
error: [Errno 2] No such file or directory: '...\\BOOK\\摘要\\manifest.json'
```
建议先判 `manifest.json` 是否存在、`schema_version` 是否可识别，给出"这不是快照目录"的说明。

---

## 五、P3（文档、平台、工程）

### D9. pending 期间 `check` 把整份请求塞进报错信息

**位置**：`novelctl.py` 223–224 行
```python
issues.append('pending_operations: ' + json.dumps(state['pending_operations'], ensure_ascii=False))
```
`pending_operations` 里存的是**整份 adopt 请求**（request 全字段 + 全部评审哈希 + 备份路径）。本机实测：一个最小采用请求（1 份评审）在 `check` 输出里占 **832 / 1128 字节**（约 74%），且随评审数量与路径长度增长。结果是一条待恢复提示压掉其余检查结果，读起来费劲。

**建议修法**：只打印 `operation_id`、`number`、`target`、`phase` 和一个"详见 journal"的路径；完整内容留给 `历史版本/chapter-<id>/journal.json`。

### D12. 全仓没有 Windows 验证痕迹，README 的测试结论在 Windows 不成立

- README 第 83 行称"脚本仅用 Python 3.9+ 标准库"——语法层面成立（四脚本 `ast.parse(feature_version=(3,9))` 全部通过），但从未在 Windows 上跑过：全仓库 `.md` 里没有 "Windows" 字样。
- README 第 166 行「73 项测试全部通过（Python 3.12）」在 Windows 上不成立：打完 D1 补丁后仍是 `70 通过 / 3 error`，三项都因为测试自己用 `os.symlink` 造链接，普通权限下抛 `WinError 1314`：
  - `tests/test_chapterctl.py:79`
  - `tests/test_novelctl.py:133`
  - `tests/test_novelctl.py:178`
- 后果：**软链防护这条安全逻辑在 Windows 上一次都没被真实验证过**（与 D5 互相印证）。

**建议**：这三个用例在 `os.name == 'nt'` 且无建链权限时 `skipUnless`，同时新增 junction 用例（`mklink /J` 不需要管理员，可覆盖 D5）。

### D13. README 建书示例要求库目录已存在

`init --parent /我的作品库` 在库目录不存在时报 `error: book directory missing`（`novelctl.py` 235 行）。README 第 149 行的示例没有说明这一点，新用户第一次就会撞。

### D14. 文档统一写 `python3`，Windows 上是坑

`README.md`、`使用说明.md`、`file-protocol.md`、`task-management.md` 全部用 `python3 "..."`。Windows 上：
- `where python3` 第一条常是 `...\WindowsApps\python3.exe`（微软商店占位符，运行只会弹商店）；
- 很多机器只有 `python.exe`。

本机能跑通只是因为 Hermes 自带的 PATH 恰好把真实 `python3.exe` 排在前面。建议文档写成 `python`（3.9+ 均适用）或在每个示例前注明平台差异。

### D15. 无 LICENSE

README 第十二节自述"仓库暂未附开源许可证文件"。对外分发或二次修改前需要补（MIT/Apache-2.0 均可）。

### D16. description 超出 Hermes 的 60 字惯例

`SKILL.md` frontmatter 的 description 为 108 个汉字，Hermes 自建 skill 的约定是 ≤60 字；硬装能正常加载（本机已确认可被 `skills_list`/`skill_view` 识别），只是索引里显示很长。若希望兼顾两边，可压到 60 字内并把细节放进 SKILL.md 正文。

### D17. 文档体积

`SKILL.md` 7.5KB + 5 个 references 约 69KB（总 76KB）。按 SKILL.md 的"每次执行先读 orchestration.md"加上 task-management.md，单章光读规范就要几万 token。建议把"每次必读"压到一页以内，其余改为按任务查阅。

### D18. `agents/openai.yaml`

Codex 专用界面元数据（`display_name`/`default_prompt`），Hermes 与其他宿主不读，无功能影响；保留无害，只是容易让人误以为装了就有交互入口。

---

## 六、集成层面的注意事项（不算 bug，但会摩擦）

| 编号 | 内容 |
| --- | --- |
| I1 | `references/orchestration.md` 第 5 行假设宿主提供 "spawn、send input、wait/result"。Hermes 的 `delegate_task` 子 agent **结束后不可续聊**（只有运行中的 `steer`），因此"同一评审者复核新稿"只能新开子 agent，独立性只能按"新子 agent + 全新上下文"记录，不能声称是同一评审者复核。 |
| I2 | 每章闭环需要 8–10 次派工（准备、起草、首读、评审、修订、复核、采用、同步…），且必须串行；Hermes 并发上限 3，一章的墙钟时间会很长。文档允许"一次派工承接多个产物"，建议在快速上手处显式给出压缩后的最小派工组合。 |
| I3 | 身份与授权只是字符串，脚本只比较字符串（`chapterctl.validate_reviews`、`taskctl` 的 `verification.agent_id`）。文档已如实声明"文件不能认证身份"，但这意味着"主 agent 不写正文"完全靠流程纪律，没有机械保障——审核时容易被自报的 `agent_id` 骗过。 |

---

## 七、已验证正常的部分（供开发者参考，避免重复排查）

| 项 | 结果 |
| --- | --- |
| `init` / `index` / `check` / `snapshot` / `restore` / `export` | Windows 真机全部通过；`restore` 能从快照回滚被改坏的正文，并保留 recovery 副本 |
| `chapterctl adopt` → `finish` 全链路（中文路径、`第0001章-xxx.md` 命名、`continuous` 授权） | 跑通；`finish` 前后 `novelctl check` 均 `OK` |
| 手改正文检测 | `chapter sha256 mismatch: 第1章.md` |
| 快照被篡改检测 | `invalid snapshot: .../manifest.json: snapshot manifest checksum/directory mismatch` |
| 导出保护 | 目标在作品内被拒（`export must be outside book`）；有 pending 时被拒（`export blocked until synchronization completes`） |
| `expected_sha256` 校验 | 已采用章哈希不符时 `old chapter changed or stale expected_sha256` |
| 锁冲突提示 | 明确报 `writer lock exists`，附路径与处置说明 |
| 中文书名/目录/正文 | 无乱码，索引链接正确（含 `[草案]` 这类带方括号的文件名） |
| 80 章规模 | `check` 通过；`chapterctl check` 1.1s（无快照时） |
| Python 3.9 语法兼容 | 四脚本 `ast.parse(feature_version=(3,9))` 通过 |
| 以 junction 作为作品根路径 | 可用（`check` 返回 `OK`） |

## 八、未验证范围

1. `bookctl.py check` 的**全书定稿链路**端到端（需要完整 finalization 阶段任务证据 + 定稿记录），本报告只跑到 `manifest`；
2. 两个进程同时 `adopt` 的并发行为（只验了锁冲突报错）；
3. 超过 260 字符的长路径（Windows MAX_PATH）；
4. 断网/磁盘满/只读目录等 I/O 故障下的恢复行为；
5. macOS/Linux 下 `merged`、NFD 文件名等跨平台差异（本次未在对应平台执行）。

---

## 附录：审查期间在本机做的唯一一处临时改动

审查开始时为了能跑通写命令，我在**本机安装副本**（`%LOCALAPPDATA%\hermes\skills\creative\novel-studio\`）里给 `chapterctl.py` 的 `save()` 加了 4 行平台判断：

```diff
 def save(path, value):
     nc.atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')
+    if os.name == 'nt':
+        # Windows cannot open a directory handle for fsync; os.replace already
+        # flushed the temp file, so skip the POSIX directory fsync there.
+        return
     fd = os.open(path.parent, os.O_RDONLY)
```

- 上游仓库文件**未改动**；GitHub 上的 `3f8f41f` 仍是原始状态。
- 该改动只影响本机副本；如果开发者按 D1 的建议正式修，这里应回退成上游原样再重装。
- 其余全部问题都只是**复现与记录**，没有动代码、没有动文档。

---

## 九、本次修复对照（2026-11-11）

以上内容保留为原始审查记录。本节对应当前工作区的修复状态，不将原报告的 Windows 复现结果或旧版验证记录当作本次验收结果。按用户要求，本次不执行 Windows 真机或 Wine 测试，以本地自动化回归交付；不要求另行真机测试。本地验证结果另记于[验证记录](验证记录.md)。

| 报告项 | 当前处理 |
| --- | --- |
| D1 | Windows 写入保留文件 fsync，跳过不支持的目录 fsync；另有平台分支回归 |
| D2 | 工具采用日志列为只读归档，历史父索引指向 `journal.json`，不再向日志目录补写索引 |
| D3 | 交接同时接受 `第1章`、`第0001章` 或完整正式章文件名 |
| D4 | 权威 JSON 只在任务受管标记内提取；标记外的 JSON 示例由 check/render/update 保留 |
| D5 | 路径遍历与写入检查内部 symlink/reparse point 及路径包含关系；Windows 根 junction 与内部 junction 分别有用例 |
| D6 | 提供显式 `chapterctl unlock`，核对当前 pending、确认无写入者；Windows 新锁结合 PID 与进程创建时间识别写入者，旧锁缺创建时间时保守拒绝活 PID；不静默删除残留锁或日志 |
| D7、D13 | 创建前拒绝 Windows 非法、保留及尾随空格/点书名；自动创建缺失作品库，初始化失败清理本次半成品 |
| D8 | 扫描 Markdown 本地链接前屏蔽围栏和行内代码；正确处理转义反引号，正文中的坏链接仍须修复 |
| D9 | pending 告警仅输出操作摘要与日志入口，完整请求留在日志 |
| D10 | 默认 `check` 每个快照 payload 文件只哈希一次；`novelctl check`、`chapterctl check`、`bookctl manifest` 支持显式 `--skip-snapshots`，快速模式跳过快照 payload；默认完整检查、恢复、`finish` 及全书定稿核验保留归档验证 |
| D11 | 无 manifest 或不支持的快照版本提供明确错误，验证失败不恢复作品 |
| D12 | 配置 Linux/Windows × Python 3.9/3.14 CI；Windows symlink 权限不足时仅跳过对应测试，junction 单独覆盖；配置不视为执行证据 |
| D14 | 用户命令示例统一用 `python`，明确 Linux/macOS 的 `python3` 和 Windows 的 `py -3` 替换方式及 shell 差异 |
| D15 | 核对当前仓库已有的 MIT `LICENSE` 与 README 许可说明，未另行更改许可证 |
| D16 | frontmatter description 为 53 字符，细节保留在正文 |
| D17 | 部分改善：Skill 入口精简，按步骤和小节读取参考文档，不再要求每章通读全部规范；参考文档总体体量仍需维护 |
| D18 | 说明 `agents/openai.yaml` 仅供 Codex 界面使用，其他宿主直接读取 `SKILL.md` |
| I1、I2 | 已结束任务不可续聊时新派真实 agent；快速上手给出四组顺序派工，并保留逐项验收与必要返工 |
| I3 | 保留身份/授权仅为申报的边界，由主 agent 对照实际宿主记录和作者决定核验 |

### 本次本地验收

2026-11-11，Linux x86_64 / Python 3.12.3 执行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/run_suite.py --report-dir test-artifacts
```

结果：109 项，107 通过、2 跳过、0 失败、0 错误。仅两项原生 Windows junction 用例因平台跳过；Windows fsync 与 reparse point 分支采用本地模拟回归，不冒称 Windows 实测。`test-artifacts/test-results.json` 保存环境、源码 SHA-256 和逐项结果，`test-results.xml` 保存 JUnit 结果。四个脚本的 Python 3.9 语法兼容检查、Skill 格式校验与 `git diff --check` 均通过。

### 可选的平台复验方式

本节仅保留将来的平台复验命令，不作为本次交付的前置条件。在 Windows 的仓库根目录使用已安装的 Python 3.9+，例如 PowerShell：

```powershell
py -3 --version
py -3 tests/run_suite.py --report-dir test-artifacts
```

若没有 `py` 启动器，可使用 `python` 替换；先确认版本与执行路径。`test-artifacts/test-results.json` 记录环境、源码哈希、每个用例及跳过原因，`test-results.xml` 保存 JUnit 结果。普通权限下 symlink 测试可因权限跳过，junction 用例应独立执行；不能把这类跳过视为 junction 防护已通过。

若将来复验，使用临时作品副本，避免覆盖真实作品或删除未经确认的锁内残留。本次未执行该平台复验，不能把 CI 配置或本地模拟结果视为原生 Windows 通过。

## 十、用户提供的复测反馈（`b2bf15e`）

2026-11-11 晚收到用户 Windows 真机复测报告：Windows 11 22631 / Python 3.14.9 / git-bash，109 项中 106 通过、3 项因 WinError 1314 跳过、0 失败；两个 junction 用例实际通过，用户报告四个 CI 作业通过。该报告确认原有修复可用，D6 为受控解锁设计，D17 为部分改善。三项符号链接测试未在该用户环境执行，不能用 junction 结果替代。

复测另指出 PID 复用可能误阻止 `unlock`，后续修复采用 Windows 进程创建时间区分同 PID 的不同进程，同时保留旧锁保守检查、显式确认和 pending 核对；未引入强制绕过或额外模块。报告来源及未覆盖范围见[验证记录](验证记录.md)；本轮未重复 Windows 真机测试或独立核验远端 CI。
