# 长篇小说创作 Skill · ai-novel-editor-skills

一个面向长篇小说创作的 **Agent Skill**：主 agent 只负责沟通、规划、派工与验收，**正文写作、资料维护和逐章评审全部交给真实子 agent**；作品记忆以普通文件保存，换会话、换工具后可以接着写。

> A skill for writing book-length fiction with real sub-agents. The orchestrating agent never writes prose: drafting, worldbuilding records and per-chapter review are delegated to separate sub-agents, and all story memory lives in plain files, so you can resume the novel in any other agent tool.

- 实现入口：[`novel-studio/SKILL.md`](novel-studio/SKILL.md)
- 需求基线：[`需求文档.md`](需求文档.md)
- 使用说明：[`使用说明.md`](使用说明.md)
- 验证记录：[`验证记录.md`](验证记录.md)

---

## 一、它解决什么问题

让 AI 写几千字不难，写一本八十章的长篇很难，难点不在文笔而在三件事：

| 难点 | 本 Skill 的做法 |
| --- | --- |
| **记忆漂移**：人物伤势、物品、秘密、伏笔过几章就自相矛盾 | 每部作品是一棵普通文件夹，重要事实带稳定对象 ID、故事时点、章节原文出处与核对哈希；摘要只做定位，不替代原文 |
| **自己评自己**：同一个上下文写出来的稿子看不出问题 | 逐章交给**未参与写作**的另一真实子 agent 独立评审，先隔离上下文首读、保存原始反馈，再核对作者意图与设定 |
| **跨会话断裂**：换会话/换工具就接不上 | 索引、交接说明、状态 JSON、任务账本都是文件；新工具加载同一 Skill 即可续写，不依赖旧会话还活着 |

先确认作者希望的题材、目标读者与阅读体验，支持文学表达、类型叙事、情节追读或混合目标；不以单一审美处理所有作品，**不用自评分代替阅读证据**。

## 二、核心约束：主 agent 不写正文

这是本 Skill 与"让 AI 直接写小说"最大的区别，也是它不肯降级的地方。

- **主 agent**：与作者沟通、制定阶段计划、派工、跟踪、只读检查、组织返工、向作者汇报。
- **创作子 agent**：构思、试写、正文、改写。
- **资料子 agent**：人物/世界/故事线/摘要/索引/历史/交接等作品文件。
- **评审子 agent**：当前稿的文学与连贯性评审，且必须与写作者不同。
- **全局总编子 agent**：独立于当前稿写作者，持续检查主线、人物变化、详略、章际重复与承诺兑现；按触发条件连读一组章节，不替代隔离首读。

不能靠换一个角色名、在主 agent 回复里模拟对话、或"顺手修复"绕过分工。宿主如果提供不了真实子 agent，会明确说明执行条件不足并保留现场与迁移指引，**不会退化成主 agent 代写代评**。

## 三、能力一览

| 模块 | 内容 | 位置 |
| --- | --- | --- |
| 编排与分工 | 进入现场、规划、派工、独立评审、采用同步、验收汇报的完整闭环 | [`SKILL.md`](novel-studio/SKILL.md)、[`orchestration.md`](novel-studio/references/orchestration.md) |
| 文学技法 | 构思验证、人物与场景、主题、多线结构、情感积累、节奏曲线、语言辨识度与修订 | [`literary-craft.md`](novel-studio/references/literary-craft.md) |
| 故事资料 | 人物成长、阶段关系、世界与历史、事件、线索、角色知情、资源、意图、摘要与增量更新模板 | [`story-records.md`](novel-studio/references/story-records.md) |
| 任务账本 | 单文件阶段任务、固定必做步骤、前置依赖、验收证据、返工历史、关键节点 gate | [`task-management.md`](novel-studio/references/task-management.md)、`scripts/taskctl.py` |
| 文件协议 | 建书、索引、手改检测、快照/恢复、TXT/Markdown 导入导出、换工具接续 | [`file-protocol.md`](novel-studio/references/file-protocol.md)、`scripts/novelctl.py` |
| 章节采用 | 独立评审、授权与版本门槛、采用后资料同步待办与完成检查 | `scripts/chapterctl.py` |
| 全书定稿 | 完整章节清单、整体阅读与结构修订报告、版本只读核验（与"工作稿导出"分开） | `scripts/bookctl.py` |

## 四、默认执行闭环

```
进入现场 → 理解与规划 → 派准备任务 → 派创作任务
   → 派独立评审 → 采用与同步资料 → 验收与汇报
```

1. **进入现场**：读顶层 `索引.md`、`创作约定/写作进度与接续说明.md`、`作品状态.json`、`任务计划与验收.md`；检查未完成操作、任务依赖、手工修改、正文版本与评审状态。
2. **理解与规划**：恢复作者目标、章长、详略、语言、阅读期待及授权，补问会改变交付的未知项；区分先建设定、边写边补、混合三种开书方式。需要时做**同场景两种叙述方案对照试写**，记录选择的适用范围，不把单章声音自动推广全书。
3. **派准备任务**：资料子 agent 按时点组装依据；总编给本章全局约束，创作子 agent 明确人物诉求、本章变化和详写／概述／跳过的安排。
4. **派创作任务**：创作子 agent 写入草稿区，提交正文、依据与候选变化，**不改正式事实**。
5. **派独立评审**：另一位真实子 agent 首读隔离、保存原始反馈后再核对意图；记录输入版本、阅读范围与证据。
6. **采用与同步**：满足授权、无未解决硬冲突、主要阅读问题已处理或按授权有理由保留，且当前稿独立复核及适用的机械检查通过后，资料子 agent 保存快照 → 登记待完成操作 → 纳入正式版本 → 按变化更新所有受影响资料与索引。主 agent 不写文件。
7. **验收与汇报**：核对实际产物、版本、主要问题处理与未完项，合格才算 `done`。主动向作者说明重要阅读问题、建议及取舍，不以档案数量或检查通过代替作品效果。

每次写章都走完整闭环。草稿可以保存、讨论，但**缺独立评审或资料未同步的章节不算完成**。

新阶段默认使用 `workflow_version: 2`：在现有任务中绑定作者约定、总编、章节设计、阅读体验及资料同步的验收证据；最终稿使用版本明确的 v2 评审和可重算的正文检查报告。小改可由独立评审增量复核，主 agent 看 diff 不能代替。旧阶段缺版本视为 v1，保留历史且不补造过去的验收。

## 五、安装

Skill 本体就是 `novel-studio/` 这个文件夹（含 `SKILL.md`、`references/`、`scripts/`、`agents/`），把它放进宿主支持的 Skill 目录即可。`agents/openai.yaml` 仅供 Codex 界面显示名称与默认提示，其他宿主直接读取 `SKILL.md`，不会因该文件自动获得交互入口或子 agent 能力。例如（Bash）：

```bash
git clone https://github.com/guofengli1214-cmd/ai-novel-editor-skills.git

# 示例：Claude Code 的技能目录
mkdir -p ~/.claude/skills
cp -r ai-novel-editor-skills/novel-studio ~/.claude/skills/

# 也可直接散放到工具约定的 skills 目录，或在支持路径读取的工具里指向 SKILL.md
```

要求：

- 宿主 **必须提供真实子 agent**（派生子任务、传递资料、取回结果）和普通文件读写能力；顺序派工即可，不要求并发。
- 脚本仅用 **Python 3.9+ 标准库**，不需要数据库、向量库、MCP 服务或任何第三方包。

命令示例使用 `python`，先用 `python --version` 确认是 Python 3.9+。Linux/macOS 若只有 `python3`，将命令中的 `python` 替换为 `python3`；Windows 可用 `py -3` 替换，不依赖 `python3` 的应用商店占位符。示例里的路径和变量也须按实际 shell 替换，不能把 Bash 的 `NAME=value` 直接粘贴到 PowerShell。

## 六、快速上手

开新书：

> 使用 $novel-studio，在 `/我的作品库` 新建《雾港来信》。先讨论方向，再试写第一章。由子 agent 写作和维护资料，每章交另一位子 agent 独立评审。

接手续写（不需要旧聊天记录）：

> 使用 $novel-studio，接着写 `/我的作品库/雾港来信`。先检查交接和未完成更新。

其他常用请求：

- "查一下主角到现在知道了哪些秘密。"（走资料索引，不重新通读全稿）
- "第三章的伤势会不会和第八章打架？"（查连贯性记录 + 原文出处）
- "把主角妹妹改成姐姐，列出所有要跟着改的地方。"
- "从编辑角度看看这五章为什么不好看。"
- "导出当前工作稿到 `/导出/雾港来信.md`。"

### 四组顺序派工示例

普通一章可按下列四组组织，不要求每个账本子任务都新开一个 agent：

1. **准备与草稿**：资料子 agent 组装依据；主 agent 验收 `prepare` 后，创作子 agent 提交草稿与候选变化。
2. **首读与核查**：另一真实评审 agent 先隔离首读并封存原始反馈，主 agent 验收后才补充意图与资料完成核查；宿主不支持结束后续聊时，新派真实 agent 并保留前一份反馈和输入哈希。
3. **意见处理与复核**：创作子 agent 处理意见；主 agent 验收修订，再由未参与写作的评审 agent 复核当前稿。无需改正文也记录理由，确需返工时重复这一组。
4. **采用与同步**：资料子 agent 采用、更新资料和索引、关闭待办；主 agent 在关键节点验收，再记录任务完成与下一阶段。

这是产物分组，通常包含多次实际派工；不是把四组当成四次工具调用，也不能跳过任务依赖、主 agent 验收、独立评审或修订后复核。

## 七、作品目录结构

每部小说是一个独立书名目录，**正式章节在顶层**，资料进子文件夹，活动目录都有 `索引.md`；工具生成的快照和采用日志归档由历史父索引链接，不补写或改动其内部文件：

```text
书名/
  索引.md
  第0001章-有意义的标题.md        当前采用的正式正文，全书连续章号
  创作约定/                       意图、作者决定、语言标准、作品状态、交接与任务计划
  大纲/                           全书、阶段、章节与场景计划
  人物/                           基础人物档案
  世界与地点/                     地点、规则、组织、世界历史
  连贯性记录/                     故事时间、角色状态与知情
  摘要/                           章、阶段、全书摘要
  审稿与修订/                     评审 JSON 及可读意见、修订与变化记录、全书定稿记录
  草稿与备选/                     未采用正文、候选资料、独立备选方案
  历史版本/                       快照及操作备份
```

按实际内容再增 `故事线/`、`关系与成长/`、`事件/`、`线索与悬念/`、`资源/`、`曲线与节奏/`，**不预建空表**。复制整个作品文件夹即可迁移。

## 八、脚本命令参考

`SKILL` = Skill 文件夹绝对路径，`BOOK` = 作品绝对路径。所有脚本输出 JSON（`novelctl check` 输出 `OK`/问题列表）。

| 命令 | 用途 |
| --- | --- |
| `novelctl.py init` | 建新书（缺失作品库自动创建；校验跨平台书名；不覆盖已有作品） |
| `novelctl.py index` | 刷新索引自动链接块（保留手写说明） |
| `novelctl.py check` | 检查路径、索引、版本与哈希 |
| `novelctl.py snapshot / restore` | 整书快照与恢复 |
| `novelctl.py export` | 按章号合并导出正式章节到作品目录外 |
| `chapterctl.py adopt` | 按请求采用当前稿（校验评审、授权、版本） |
| `chapterctl.py finish` | 关闭该次采用的资料同步待办 |
| `chapterctl.py unlock` | 确认无写入者、核对 pending 后解除残留锁，不删除操作记录 |
| `chapterctl.py check` | 检查待同步操作与章节状态 |
| `chapterctl.py diff` | 只读生成基准稿到当前稿的精确差异与版本映射 |
| `bookctl.py manifest` | 输出完整章节清单（章号、路径、哈希） |
| `bookctl.py check` | 只读核验全书定稿记录与报告版本 |
| `taskctl.py init / plan / update` | 初始化、规划阶段、更新任务 |
| `taskctl.py submit` | 一次登记产物、版本和检查证据，提交待验收，不自动完成 |
| `taskctl.py gate` | 派工前核验前置任务已验收且证据有效 |
| `taskctl.py close` | 关闭阶段（须有后续计划或真实终点） |
| `taskctl.py render / check` | 重新渲染勾选视图 / 只读检查账本 |
| `prosecheck.py check` | 只读统计正文并定位引号、标点和约定篇幅疑点；v2 采用时重算硬约束 |

```bash
SKILL=/绝对路径/ai-novel-editor-skills/novel-studio
BOOK=/绝对路径/作品库/雾港来信

python "$SKILL/scripts/novelctl.py" init --parent /绝对路径/作品库 --title 雾港来信
python "$SKILL/scripts/novelctl.py" check --book "$BOOK"
python "$SKILL/scripts/chapterctl.py" check --book "$BOOK"
python "$SKILL/scripts/taskctl.py" check --book "$BOOK"
python "$SKILL/scripts/taskctl.py" gate --book "$BOOK" --stage S02 --before review
python "$SKILL/scripts/bookctl.py" manifest --book "$BOOK"
python "$SKILL/scripts/bookctl.py" check --book "$BOOK"
```

日常只读检查可对 `novelctl check`、`chapterctl check` 或 `bookctl manifest` 加 `--skip-snapshots`，跳过历史快照 payload 的完整校验以降低耗时；默认仍完整检查。快速模式不代表归档完好，恢复、`finish` 和全书定稿检查继续验证快照。

PowerShell 示例（把路径换成实际值；无需 Bash 变量语法）：

```powershell
py -3 "C:/skills/novel-studio/scripts/novelctl.py" init --parent "D:/作品库" --title 雾港来信
py -3 "C:/skills/novel-studio/scripts/novelctl.py" check --book "D:/作品库/雾港来信"
```

写命令（`init/index/adopt/finish/unlock/snapshot/restore/export/plan/update/submit/render/close`）由资料子 agent 执行；主 agent 只跑 `check`、`manifest`、`gate`、`diff`、`prosecheck check` 这类只读/门槛命令。请求体字段以脚本 `--help` 为准，不要猜。

## 九、测试

```bash
python tests/run_suite.py --report-dir test-artifacts
```

测试覆盖文件、章节采用、快照恢复、全书定稿、任务依赖与证据失效，以及本次缺陷修复回归。运行器在 `test-artifacts/` 保存实际平台、Python 版本、源码哈希、每个用例与跳过原因的 JSON / JUnit 报告。

已配置 Linux/Windows × Python 3.9/3.14 的 GitHub Actions；配置本身不是运行通过的证据。用户提供的 `b2bf15e` Windows 复测报告记录 109 项：106 通过、3 项因 WinError 1314 跳过，两个 junction 用例实际通过，并报告四个 CI 作业通过；这些为用户提供的版本结果，本轮未独立重跑 Windows 或核验远端 CI。详情及本地回归见 [`验证记录.md`](验证记录.md)。

## 十、已知边界

- 脚本只能验证**机械约束**（路径、哈希、版本、门槛、依赖），**无法认证**子 agent 的真实身份、作者确实批准，也无法判断文学质量——这些必须由主 agent 对照宿主任务记录与真实对话核验。
- `check` 通过 ≠ 小说好；自评分、档案数量、曲线形状都不代表文学水平。
- 拖沓、人物吸引力和钩子效果由独立阅读与总编判断。主要阅读问题须处理或按授权明确保留，纯审美建议不机械阻断；不设普遍适用的章长、对白比例或反转数量。
- 尚未完成百章级长篇的持续创作验证，也没有独立外部读者对完整长篇的评价，因此不宣称"保证获奖"。
- 辅助脚本不支持自动改章名、重排、软删除、DOCX 导出；这些按文件协议由资料子 agent 维护普通文件处理。

## 十一、仓库结构

```text
ai-novel-editor-skills/
├── README.md
├── LICENSE                      MIT
├── 使用说明.md                  面向使用者的简明说明
├── 需求文档.md                  需求基线（v1.4）
├── 验证记录.md                  首版实现与验证记录
├── novel-studio/                ← Skill 本体，复制这个目录即可安装
│   ├── SKILL.md
│   ├── agents/openai.yaml
│   ├── references/              编排 / 任务 / 文件协议 / 故事资料 / 文学技法
│   └── scripts/                 novelctl · chapterctl · bookctl · taskctl · prosecheck（仅标准库）
└── tests/                       unittest 及跨平台结果运行器
```

## 十二、许可

本项目采用 [MIT License](LICENSE) © 2026 guofengli1214-cmd。

你可以自由使用、修改、商用和再发布，只需保留版权声明与许可证原文；软件按"现状"提供，不附带担保。如果要引用本 Skill 的方法论或改进它，欢迎提 Issue / PR。
