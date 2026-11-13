# 阶段任务与验收

每部作品使用 `创作约定/任务计划与验收.md` 保存阶段、任务、依赖、证据及验收状态。主 agent 制定计划、派工并核验；资料子 agent 写入任务记录。任务文件是执行入口，正文、评审、采用及中断状态仍由原有文件承担，不能用勾选覆盖其检查结果。

## 单文件约定

同一 Markdown 文件包含可读的阶段/任务复选框及一个带标记的 JSON 数据区。JSON 是唯一任务数据，复选框由 `taskctl` 从它生成；不另存一份 JSON 账本。只改复选框不能形成有效完成记录，`check` 会发现显示与数据不一致。通过 `plan`、`update` 维护任务；确需手工补充数据时，先保留历史，再修改数据区并运行 `render`，不能删掉必做项来修复告警。权威 JSON 只位于 `<!-- taskctl:begin -->` 与 `<!-- taskctl:end -->` 受管标记内，区外可保留说明和其他 JSON 示例；`render` / `update` 保留区外内容。重复、缺失或乱序的受管标记须先核对，不能选取任意 JSON 代替权威数据。

每项包含稳定的阶段/任务标识、任务种类、负责角色、执行状态、前置任务、输入版本、验收条件、产物路径与哈希、真实验收依据及历史。阶段内任务 ID 如 `S02.prepare`，宿主实际任务 ID 另存于派工和产物记录中，换工具时不改变作品内 ID。新阶段默认 `workflow_version: 2`；旧阶段缺此字段按 v1 读取，账本外层仍为 `schema_version: 1`，不能混淆这两种版本。

状态为 `todo`（待执行）、`doing`（进行中）、`submitted`（已提交待验收）、`done`（已验收）、`blocked`（受阻）、`not_applicable`（不适用）。只有 `done` 显示已完成勾选。子 agent 报完成先提交产物；主 agent 核实内容、版本、依赖、真实派工及授权后，再由资料子 agent 记录 `verification.agent_id` 与可核对的 `verification.source`。身份和验收文字只是申报，脚本不认证其真实性。

任务维护是每次交付的一部分：先确认前置已验收并登记 `doing`，执行者写前自行核验 gate；产物一返回就登记 `submitted`、实际路径及哈希，主 agent 核验后再记 `done`，然后才能派依赖它的下一项。不得把整个写章流程打成一个先产出后补账的任务包，也不把 `submitted` 当作已完成。中断或部分产出立即保留真实状态、已写文件和缺项，不能为保持勾选完整而补造过去的批准。

核心任务必须执行；如不需要改稿，`revise` 或 `structural_revision` 记录无需修改的依据，仍须由相应评审复核。额外条件任务可用 `not_applicable`，但须有原因及验收依据，不能用它跳过独立评审、作者授权或资料同步。阶段没有适用素材缺口时无需强行查资料，缺外部读者不构成停止创作的条件。

## 必做步骤与滚动计划

`plan` 根据工作流生成必做项和依赖，不能遗漏、重复、取消或解除其模板前置关系：

| 工作流 | 必做步骤，按依赖顺序 |
| --- | --- |
| `planning`：方向与构思 | `direction` 方向与授权 → `concept` 构思验证 → `next_plan` 后续计划 |
| `chapter`：写章或改章 | `prepare` 写前准备 → `draft` 草稿 → `first_read` 首轮阅读 → `review` 资料与连贯性核查 → `revise` 意见处理 → `recheck` 当前稿复核 → `adopt` 正式采用 → `sync` 资料同步 → `next_plan` 后续计划 |
| `trial`：开书或风格实验 | 在 `chapter` 的 `prepare` 与 `draft` 之间增加 `voice_trial` 同场景叙述方案比较；遵守已有视角及授权 |
| `finalization`：全书定稿 | `whole_read` 整体阅读 → `structural_revision` 结构意见处理 → `whole_recheck` 全稿复核 → `finalize` 定稿验收 → `next_plan` 后续安排 |

任务可合并承接一次实际派工的多个产物，执行记录分别引用证据，不要求每个任务新开一个 agent。任务依赖表示交付物已被验收，不能只凭派工已发出就进入下一步。`depends_on` 只引用同阶段任务；跨阶段通过关闭阶段、`next_stage` 和输入版本衔接，避免当前任务等待尚不可执行的未来阶段。额外条件或并行任务可增加到同一数据区，列清依赖；不得反向影响首读隔离。

### v2：在原任务中验收具体编辑责任

核心任务顺序不变，不为每条要求再建账本。v2 使用 `checks` 引用已读输入或已交付产物；每个值为 `{"path":"作品内相对路径","sha256":"文件实际哈希","summary":"本项判断、取舍或待办的简要依据"}`。指针必须出现在同任务的 `inputs` 或 `outputs` 中。几项检查可以引用同一份准备、评审或交接报告，但不能只复制“已检查”。

| 任务 | 必需 `checks` 键 | 验收内容 |
| --- | --- | --- |
| `direction` | `author_preferences`, `startup_mode`, `authorization` | 阅读目标、偏好来源与范围；资料方式的实际选择或有效免问记录；合作方式与本轮授权 |
| `concept` | `story_engine` | 持续选择、主故事承诺及长篇支撑风险 |
| `prepare` | `author_preferences`, `chapter_design`, `global_direction`, `source_status`, `prose_policy` | 本章约定；人物诉求、变化和详略；总编意见；关键设定支持范围；从约定提取的执行规则 JSON |
| `voice_trial` | `style_scope` | 实际对照、采用依据及仅本章／视角／全书的生效范围 |
| `first_read` | `reader_experience` | 封存的自由阅读体验、阅读范围及隔离限制 |
| `review` | `editorial_findings`, `global_direction` | 连贯性与阅读问题的证据；跨章推进、重复、承诺与成长节点 |
| `revise` | `issue_disposition` | 重要意见如何修改、拒绝或按授权保留，以及代价 |
| `recheck` | `current_version`, `issue_disposition` | 当前稿的独立全文／增量复核与问题处理结论 |
| `sync` | `source_status`, `handoff` | 正文事实、推测与过期指令处理；当前进度、限制和待办 |
| `next_plan` | `global_direction`, `handoff` | 后续推进和连读安排；接续入口或授权终点 |
| `whole_read` | `reader_experience`, `global_direction` | 完整范围的实际阅读、结构及承诺兑现 |
| `structural_revision` | `issue_disposition` | 整体问题的实际处理或有依据的保留 |
| `whole_recheck` | `current_version`, `issue_disposition` | 当前完整范围的复核与重大问题处理 |

`draft`、`adopt`、`finalize` 沿用原有产物与机械门槛，不另堆检查键。`submitted` 和 `done` 都要有相应证据；脚本只核验结构、引用和版本，主 agent 仍须核对内容及真实派工。填写“总编已读”或 `summary` 不能证明实际阅读和问题已解决。

### 开书停点：先收到答复，再开始实质工作

新建 v2 `planning` 阶段还带 `direction_gate_version: 1`。这是开书了解与授权的门槛版本，与 `workflow_version`、账本外层 schema 分开；其他工作流不增加此字段。它复用 `direction` 的三项检查和作者约定原件，不另建问卷或授权账本。

| 现有检查 | 新增决定位置 | 记录范围 |
| --- | --- | --- |
| `author_preferences` | `decisions.experience`、`decisions.style`、`decisions.length`；新决策门槛另需 `decisions.title` | 阅读体验；语言、节奏与详略；本次篇幅；作者选择的书名与历史状态 |
| `startup_mode` | `decision` | 先建资料、边写边补或混合；明确选择仅试写也可记录其有限路径 |
| `authorization` | `decisions.collaboration`、`decisions.work_scope` | 作者参与方式；本轮可以做到哪里、在哪里停 |

每项决定使用相同结构，例如以下内容须有对应的真实作者答复，而不是 agent 预设：

```json
{
  "resolution": "selected",
  "value": "先建设支撑开篇的世界与人物资料，本轮不写正文",
  "question_quote": "先整理必要设定，还是边写边补资料？",
  "source_quote": "先把必要设定整理好，正文先别写",
  "scope": "本书开篇准备阶段"
}
```

`resolution` 为 `provided`（作者已主动给出具体决定）、`selected`（回答了实际选项）、`delegated`（问过后明确选择全部按推荐且不用询问）或 `unresolved`。前三者必须有非空 `value`、`source_quote` 和 `scope`；新决策门槛对 `selected/delegated` 还检查下节问答依据。任何必需项仍为 `unresolved`、缺字段或仅有 `summary`，都不能提交／验收 `direction` 或进入依赖它的构思任务。待答期间保持待执行或 `blocked`，不先完成大纲再补账。

这些位置用于核对关键决定，不要求逐字段分开询问。已有具体答案直接引用；只有真实提问后，作者明确选择“全部按推荐且不用询问”，才可在该范围按推荐决定。泛泛“你定”“帮我写”“AI 主导”、只回答语言密度或本批全选 A，不能自动覆盖其他项目或未来选择。有限试写本身也不授权 agent 选择人物命运、冲突结局等决定性内容；先让作者选择，或取得上述唯一免问授权。单纯资料整理和不改变已选内容的机械维护无需全套开书问卷，但整理中发现的创作矛盾不能擅自定案。

旧规划阶段不因缺此字段被追溯判为从未完成。对于旧空骨架或 `direction` 尚未验收的活动阶段，继续实质工作前由资料子 agent 用 `plan` 显式补上开书门槛，再补真实答复：

```json
{"id":"S01","direction_gate_version":1}
```

该操作保留已有任务、历史和 `workflow_version`，不补造作者批准；门槛不能降级。已关闭阶段或已经验收的方向不能借此重写历史，须在当前合法任务范围重新核对实际约定，必要时另建规划阶段；原有草稿保持原状。未通过验收的旧方向仍不能因新版章节阶段已建好而被跳过。

### 决定性选择：作者选定后才执行

新建 v2 阶段带 `decision_gate_version: 1`，涵盖全部工作流；它与仅用于规划的 `direction_gate_version` 分开。旧阶段无此字段仍保留历史校验，不能原地补此标记追认旧批准；后续新阶段启用新门槛，旧活动任务继续时也须遵守当前作者决策规则，不得沿用已撤销的泛委托。脚本不提供关闭或降级新门槛的请求。

决定性选择包括且不限于书名及恢复弃用名称、作品方向与结构、视角与风格、承重世界规则、人物成长/命运/关系、冲突解决方式及代价、关键转折、秘密揭示、伏笔兑现和结局。推荐不是决定；待选内容只能作为供选择的提案，不能先写成执行大纲、有效设定、场景安排或正文。已有有效具体选择无需重问；照案执行和不改变创作选择的技术修错不逐词请示。

新门槛下，`selected` 决定另需非空 `question_quote`，与 `source_quote` 中的实际答复对应；`provided` 保留作者主动给定的具体选择。`delegated` 必须另含以下记录，两项布尔值都为真实的 `true`：

```json
{
  "question_quote": "实际问过的全部推荐免问选项，及其所指范围",
  "answer_quote": "作者实际选择全部按推荐且不用询问的答复",
  "scope": "明确生效的作品、阶段或推荐批次及例外",
  "all_recommendations": true,
  "no_further_questions": true
}
```

此对象放在该决定的 `opt_out` 中，不另造授权账本。主 agent 核对实际问题、选项映射与答复：选择“全部推荐”未同时免后续询问，不能申报此模式；对本批的许可只对本批生效，不迁移到新作品或新范围。作者撤回或新反馈优先，后续任务不得引用已失效许可。字段与布尔值仅是可验证的申报，不会替代真实来源语义核对。

`concept/prepare/revise/next_plan/structural_revision` 的原任务增加 `checks.creative_decisions`。它仍用 `{path,sha256,summary}` 引用作者决定主记录或其不可变副本，附本轮 `items`；每项含 `id`、`topic`、`record_id` 及上述决定的 `value/resolution/source_quote/scope` 和适用的问答/`opt_out`。同一已选决定可引用原记录，无须再问；新增或改变内容必须有对应新选择或有效免问依据。无新增决定时使用 `items: []` 加非空 `none_reason`，主 agent 对照实际方案审查，不能用空列表掩盖新选择。

这些任务在写前运行 `gate` 时即检查本任务的 `creative_decisions` 和所引文件的当前哈希，而非等写完提交才查。资料子 agent 可先用 `update` 在尚未验收的任务登记真实作者输入及此检查；这只登记已有选择，不生成故事方案。完整输出和其他编辑检查仍按正常提交／验收阶段提供。门槛未过时，停止依赖该决定的工作并回到对话给作者选项；不能改为另一任务名先写出来。脚本无法识别是否漏列决定，执行者与主 agent 都须对照实际内容核验。

书名决定还需 `prior_status: proposed|active|retired|rejected|unknown`，并与作品状态中的当前书名完全一致。目录名、素材标题或历史候选均不是作者选名证据；`retired/rejected` 另需非空 `history_source` 和 `reuse_confirmation`，其中 `acknowledges_retired: true`、`answer_quote` 必填。作者主动明确恢复（`provided`）时，`source_quote` 与 `answer_quote` 保留具名恢复原话，不要求捏造提问或再次询问。`selected/delegated` 则须实际 `question_quote` 点明具体旧名及曾弃用事实；单项答复明确该名，或通过 `selected_title` 与真实问题的选项映射指向它（如作者只回 A，`source_quote` 与 `answer_quote` 均保留 A，不能改写成具名原话）。若对这批明确展示的推荐选“全部按推荐且不用询问”，`opt_out.question_quote` 也须点名该旧名，复用确认的答复须与 `opt_out.answer_quote` 一致，不能用未提旧名的泛许可复活它。

新建作品在创建目录前就要核对书名决定；CLI 及保存入口见[文件协议](file-protocol.md)。连续采用 `continuous` 只涉及何时采用稿件，不赋予决定人物走向或冲突解法的权限；稿件采用授权与创作选择须分别满足。

总编的章前判断、写后判断和阶段连读由 `prepare/review/next_plan` 承接。已触发而未完成的必要连读须登记并处理，再继续依赖它的章节；不能只把总编写在角色名单里。触发与角色独立性见[协作流程](orchestration.md)。

只详细安排当前阶段和下一阶段。阶段结束前，把 `next_stage` 指向已经生成完整必做任务的下一阶段，再验收 `next_plan`。本次授权仅到试写、作者要求结束或整书工作结束时，可用真实 `end_reason` 表明本轮终点，不为满足计划要求擅自扩写。关闭阶段只移动接续入口，不执行下一阶段，也不扩大作者授权。

改变 `next_stage` 或 `end_reason` 会重新打开已验收的 `next_plan`，须核验新的安排后才能关闭阶段。本轮结束后获得新的继续授权时，可用 `plan` 新建或选择已规划阶段；只有此前当前阶段已关闭，命令才会把游标移过去，保留旧结束理由而不自动执行新任务。

已关闭阶段保留历史状态和证据入口，不把旧版本当作当前新稿的完成证明。修改此前已关闭阶段的章节时，新建对应章节修订阶段，引用旧成果并重新核查当前版本；当前阶段内返工则重新打开受影响任务。

## 输入、证据与返工

输入与产物使用作品内规范相对路径及实际 SHA-256。完成任务必须有当前有效的产物证据；`next_plan` 检查账本内的下一阶段或结束理由，不要求创建重复计划文件。不要把账本自身或不断变化的 `创作约定/作品状态.json` 作为任务哈希输入，否则更新记录本身就会令任务失效。历史稿、首读原件和采用前快照按现有版本规则保留。

所有来源只限当前作者指定书根；不能把其他由本 Skill 创作的书登记成输入、候选、历史证据或恢复来源，复制到本书也不改变来源。恢复核对文件沿用本书作者约定、既有接续或实际异常说明，不新建另一套任务账本。任务证据可验证路径及版本，是否跨书须对照真实来源核验；单书边界见[文件协议](file-protocol.md)。

`check` 检查当前阶段的固定步骤、依赖、已验收证据和显示一致性，正常的未开始任务不等于检查失败。`gate` 检查目标步骤的前置依赖闭包，未验收、证据缺失或哈希过期时不能继续；执行门槛还检查实质产物是否与任务对应。主 agent 派工前运行，执行子 agent 在写入实质产物前也自行运行同一门槛；失败只返回缺项与原状态，不能先写后补。首读者只运行中性阶段／任务标识对应的 `gate --neutral`，不读取账本内的创作信息；该模式不回显原始异常或内部证据路径，主 agent 用普通 gate 或 check 查看详细诊断。当前阶段未关闭，不得跳到已规划的未来阶段执行。

输入或产物变化时，检查报告失效任务及受影响的下游任务；资料子 agent 将对应任务重新打开，保留旧状态、旧证据和失效原因，再更新实际新输入、修订及复核证据。重新打开已完成任务会连带重新打开其下游完成项。只复核影响范围；原首读保留为旧稿阅读证据，不伪称读过意图的评审者又完成了盲读。

直接更新已验收任务的证据、操作号或处理理由也必须提供新的主 agent 验收依据，并会重新打开下游完成项；不能用旧验收或只替换哈希保留后续勾选。

首读评审只能收到中性任务标识、必要正文与前文、哈希、读取规则和已经核验的依赖结论，不能收到整个任务账本或包含未来秘密的阶段计划。资料核查阶段才能提供相关计划与意图。

## 命令与执行顺序

命令中的 `SKILL`、`BOOK` 使用真实绝对路径，请求文件使用实际路径。主 agent 可运行 `check`、`gate` 等只读命令；`init`、`plan`、`update`、`submit`、`recover`、`render`、`close` 由资料子 agent 执行。请求 JSON 优先保存为临时文件，避免 shell 转义；这些请求不构成第二份任务账本。下列是 Bash 变量示例；Windows PowerShell 请用带引号的实际路径。`python` 须为 Python 3.9+，Linux/macOS 可替换为 `python3`，Windows 可替换为 `py -3`。

```bash
python "$SKILL/scripts/taskctl.py" init --book "$BOOK"
python "$SKILL/scripts/taskctl.py" plan --book "$BOOK" --request /绝对路径/阶段请求.json
python "$SKILL/scripts/taskctl.py" update --book "$BOOK" --request /绝对路径/任务请求.json
python "$SKILL/scripts/taskctl.py" submit --book "$BOOK" --request /绝对路径/提交请求.json
python "$SKILL/scripts/taskctl.py" recover --book "$BOOK" --request /绝对路径/恢复来源请求.json
python "$SKILL/scripts/taskctl.py" check --book "$BOOK"
python "$SKILL/scripts/taskctl.py" gate --book "$BOOK" --stage S02 --before review
python "$SKILL/scripts/taskctl.py" close --book "$BOOK" --stage S02
python "$SKILL/scripts/taskctl.py" render --book "$BOOK"
```

阶段请求示例：

```json
{"id": "S02", "title": "第一章试写", "workflow": "trial", "workflow_version": 2, "chapter": 1}
```

新增后先为当前阶段指定下一阶段，例如用 `plan` 提交 `{"id":"S01","next_stage":"S02"}`，再验收当前阶段的 `next_plan`。已有阶段的工作流和目标章号不能改名换意，应新建阶段并说明调整。

任务提交示例，路径和哈希须换成真实产物：

```json
{
  "stage": "S02",
  "task": "first_read",
  "status": "submitted",
  "inputs": [{"path": "草稿与备选/第一章-v1.md", "sha256": "实际正文SHA-256"}],
  "outputs": [{"path": "审稿与修订/第一章-首读.md", "sha256": "实际反馈SHA-256"}]
}
```

主 agent 验收后由资料子 agent 再提交该任务的 `status: done`，附 `verification: {"agent_id":"实际主agent标识","source":"真实验收记录或上下文出处"}`。不需要返工时，意见处理任务也需有可追踪的处理依据，不直接跳到采用。

上例只展示通用字段；v2 首读提交还须有 `checks.reader_experience`，引用同一首读报告及真实哈希，并写实际反馈摘要。其他任务按上表填写。`submit` 请求与 `update` 相同，但省略 `status`（或只允许 `submitted`），一次登记输入、产物和检查，不自动验收为 `done`。收到产物应及时提交并更新接续说明，不留到多轮写作后补账。

三个关键节点：

1. **采用正文**：`chapterctl adopt` 自动检查对应 `trial` / `chapter` 阶段在 `adopt` 前的任务；`recheck` 的输入须包含实际采用草稿及哈希，产物须包含当前评审文件及哈希。采用请求可用 `task_stage` 指定阶段，未提供时只能匹配唯一适用阶段。
2. **关闭资料同步**：采用命令成功后，验收 `adopt` 任务，记录返回的 `operation_id` 及正式章节路径、哈希；再运行 `chapterctl finish`，它检查 `sync` 的前置已验收。`finish` 成功且资料核验完成后，才把 `sync` 验收为完成，避免要求同步前先勾同步完成。
3. **全书定稿**：`bookctl check` 检查 `finalize` 前置任务及当前完整正式正文的版本覆盖；通过并核验语义及授权后，才验收 `finalize`。`bookctl manifest` 获取清单时无需先完成定稿任务。

`close` 要求阶段必做项全部验收、条件项正确处理、下一阶段计划或本轮终点有依据，且没有未完成的机械操作。任务文件更新后按文件协议刷新索引；它不替代 `chapterctl finish`、`bookctl check` 或作者决定。

## 新书、旧书与交接

新建作品默认生成 S01 规划阶段及任务文件，作品状态中的 `task_management: true` 表示任务门槛已启用，丢失任务文件不能绕过检查。旧作品没有该字段和任务文件时保留机械读取、恢复和工作稿导出能力；继续创作前由资料子 agent 用 `taskctl init` 迁入，任务文件一旦存在就启用相应门槛。

迁入时从真实已有成果恢复任务及证据，不补造过去不存在的派工和验收。无法证明的步骤保留为待完成；已采用旧章保持原文，按任务需要安排补查。旧阶段缺版本按 v1 校验，不直接升级它来追写历史；新阶段默认 v2。阶段版本不能就地更换，修订或升级后的工作用新阶段承接。仅当账本所有已有阶段都是 v1，且提供真实的 `compatibility_reason` 时，`plan` 才允许显式创建兼容 v1 阶段；已有 v2 阶段的项目不能降级。不得用兼容入口为新创作逃避 v2。`pending_operations` 仍是采用/同步中断的恢复依据，优先恢复实际操作，再核对任务状态，不能反复采用同一章。

旧活动阶段尚未关闭时，继续按原版本恢复，利用已有产物补做真实缺失的检查；可提交 schema 2 当前稿独立评审，并按新规范人工核对正文机械报告，但不能声称已自动获得 v2 门槛。当前阶段依真实结果完成并关闭后，下一阶段默认 v2。不能跳到未来阶段、补造历史验收，或为迁移而重写已有稿件。

接续说明只链接当前阶段、当前任务、受阻事项和下一步，避免复制整份任务状态。新 agent 每次开始、派工、采用及阶段收尾前读取任务入口，对照实际产物和脚本检查恢复，不把聊天计划当成唯一进度。

### 产物与任务脱节时的执行阻断及恢复

新建 v2 阶段默认 `execution_gate_version: 1`，不能关闭或降级。旧当前阶段尚未关闭时，继续实质工作前由资料子 agent 用 `plan` 请求 `{"id":"实际当前阶段","execution_gate_version":1}` 显式开启；保留原工作流版本、历史和待办，不追改已关闭阶段。开启执行门槛不表示任何任务或旧稿已经验收，也不能手移游标越过未关闭的前序阶段。

原 `warnings` 仍帮助定位，但启用执行门槛后，未登记、来源未核实或越序产物是 `check` 的失败项，也阻断实质任务 `gate` 及开工／提交／验收。脚本识别 `草稿与备选/`、`审稿与修订/`、`大纲/` 三类目录中除索引外的 Markdown、TXT、JSON，不只按章号文件名识别；改名或挂进任意任务 `inputs` 不能洗成合法进度。该机械扫描不自动识别其他目录的全部语义产物，主 agent 仍须核对实际派工与文件。正常产出须先合法登记 `doing`，返回立即提交当前路径与哈希；`todo/blocked` 不能提前挂新产物来替代开工。前置未验收时，后续依然不能开始。

发现“已有第一章或后续版本，游标仍在方向／准备”时，先停创作，保留全部原文件。只读核对真实作者输入、宿主派工与批准、版本及操作记录，再由资料子 agent 用 `recover` 保存来源与处置；命令只向同一本任务账本追加 `artifact_recoveries`，不改稿、不改任务状态、不补造历史开工或批准。每项结构为：

```json
{
  "artifacts": [{
    "path": "草稿与备选/已有稿.md",
    "sha256": "已有文件当前哈希",
    "classification": "held_output",
    "task_id": "S02.draft",
    "source": {"path": "创作约定/已有来源核对记录.md", "sha256": "核对记录当前哈希"},
    "source_quote": "核对记录中实际存在的来源或处置原话",
    "scope": "当前书已有候选稿的恢复核对，不授权续写或采用"
  }],
  "verification": {"agent_id": "实际主agent标识", "source": "真实核对依据"}
}
```

`source` 是另一份本书现存文件，原话须能逐字找到，不能拿稿件自证来源；引用真实作者约定、接续或异常记录的不可变版本，避免后续正常扩充资料使恢复证据过期。三种分类只描述事实与处置：`author_input` 是经核实的作者原有输入，不赋予新增创作权限；`held_output` 是已经产生、待按真实前置顺序核验的候选产物，须关联实际任务；`excluded` 是有真实处置依据的封存不用文件，不删除原稿，也不得再作为活动任务的输入或产物。分类及引用语义仍由主 agent 对照真实记录核验，不能自写一句“作者输入”来通过。

恢复期间只做行政登记和真实补查。确需在阻断状态提交或验收已经存在的证据，可用 `update/submit` 的 `recovery_only: true`，附非空 `reason` 与真实 `verification`；它不允许 `doing`，只接收已知或已核实恢复的文件，仍检查前置依赖、作者决定和相应验收证据，不能凭此创作、吞入未知旧稿或跳过缺失步骤。没有真实已完成的前置，就保持待办：请作者补选择，或凭真实处置先封存不用越序候选，再通过正常门槛补做工作。对应候选提交／核验及缺项恢复后，重新检查才能继续；不会因文件存在自动 `done`。

旧阶段未开启门槛时的 `warnings` 仅是线索，不证明已经执行，也不允许绕过当前规范。无论版本，已有草稿不要重复起草，已读 v2 不冒充已读 v3，无证据的验收保持待完成；不能删除异常文件或依赖来消除问题。文件或恢复来源变动后重新核对，不能仅改哈希沿用原验收。

当时实际使用的资料与当前继续执行的约束分别保留。需要长期引用的输入先保存不可变版本或指向已验证历史副本，再登记哈希；后续资料改变时，保留旧证据并为当前约束重新核查。不要为消除告警删除依赖，也不能仅更新哈希沿用原验收。历史阶段不补造新事实，当前修订用新任务承接。
