# 阶段任务与验收

每部作品使用 `创作约定/任务计划与验收.md` 保存阶段、任务、依赖、证据及验收状态。主 agent 制定计划、派工并核验；资料子 agent 写入任务记录。任务文件是执行入口，正文、评审、采用及中断状态仍由原有文件承担，不能用勾选覆盖其检查结果。

## 单文件约定

同一 Markdown 文件包含可读的阶段/任务复选框及一个带标记的 JSON 数据区。JSON 是唯一任务数据，复选框由 `taskctl` 从它生成；不另存一份 JSON 账本。只改复选框不能形成有效完成记录，`check` 会发现显示与数据不一致。通过 `plan`、`update` 维护任务；确需手工补充数据时，先保留历史，再修改数据区并运行 `render`，不能删掉必做项来修复告警。权威 JSON 只位于 `<!-- taskctl:begin -->` 与 `<!-- taskctl:end -->` 受管标记内，区外可保留说明和其他 JSON 示例；`render` / `update` 保留区外内容。重复、缺失或乱序的受管标记须先核对，不能选取任意 JSON 代替权威数据。

每项包含稳定的阶段/任务标识、任务种类、负责角色、执行状态、前置任务、输入版本、验收条件、产物路径与哈希、真实验收依据及历史。阶段内任务 ID 如 `S02.prepare`，宿主实际任务 ID 另存于派工和产物记录中，换工具时不改变作品内 ID。新阶段默认 `workflow_version: 2`；旧阶段缺此字段按 v1 读取，账本外层仍为 `schema_version: 1`，不能混淆这两种版本。

状态为 `todo`（待执行）、`doing`（进行中）、`submitted`（已提交待验收）、`done`（已验收）、`blocked`（受阻）、`not_applicable`（不适用）。只有 `done` 显示已完成勾选。子 agent 报完成先提交产物；主 agent 核实内容、版本、依赖、真实派工及授权后，再由资料子 agent 记录 `verification.agent_id` 与可核对的 `verification.source`。身份和验收文字只是申报，脚本不认证其真实性。

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
| `direction` | `author_preferences`, `startup_mode`, `authorization` | 阅读目标、偏好来源与范围；三种资料建设方式的选择或明确暂定；真实授权 |
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

总编的章前判断、写后判断和阶段连读由 `prepare/review/next_plan` 承接。已触发而未完成的必要连读须登记并处理，再继续依赖它的章节；不能只把总编写在角色名单里。触发与角色独立性见[协作流程](orchestration.md)。

只详细安排当前阶段和下一阶段。阶段结束前，把 `next_stage` 指向已经生成完整必做任务的下一阶段，再验收 `next_plan`。本次授权仅到试写、作者要求结束或整书工作结束时，可用真实 `end_reason` 表明本轮终点，不为满足计划要求擅自扩写。关闭阶段只移动接续入口，不执行下一阶段，也不扩大作者授权。

改变 `next_stage` 或 `end_reason` 会重新打开已验收的 `next_plan`，须核验新的安排后才能关闭阶段。本轮结束后获得新的继续授权时，可用 `plan` 新建或选择已规划阶段；只有此前当前阶段已关闭，命令才会把游标移过去，保留旧结束理由而不自动执行新任务。

已关闭阶段保留历史状态和证据入口，不把旧版本当作当前新稿的完成证明。修改此前已关闭阶段的章节时，新建对应章节修订阶段，引用旧成果并重新核查当前版本；当前阶段内返工则重新打开受影响任务。

## 输入、证据与返工

输入与产物使用作品内规范相对路径及实际 SHA-256。完成任务必须有当前有效的产物证据；`next_plan` 检查账本内的下一阶段或结束理由，不要求创建重复计划文件。不要把账本自身或不断变化的 `创作约定/作品状态.json` 作为任务哈希输入，否则更新记录本身就会令任务失效。历史稿、首读原件和采用前快照按现有版本规则保留。

`check` 检查当前阶段的固定步骤、依赖、已验收证据和显示一致性，正常的未开始任务不等于检查失败。`gate` 检查目标步骤的前置依赖闭包，未验收、证据缺失或哈希过期时不能继续。当前阶段未关闭，不得跳到已规划的未来阶段执行。

输入或产物变化时，检查报告失效任务及受影响的下游任务；资料子 agent 将对应任务重新打开，保留旧状态、旧证据和失效原因，再更新实际新输入、修订及复核证据。重新打开已完成任务会连带重新打开其下游完成项。只复核影响范围；原首读保留为旧稿阅读证据，不伪称读过意图的评审者又完成了盲读。

直接更新已验收任务的证据、操作号或处理理由也必须提供新的主 agent 验收依据，并会重新打开下游完成项；不能用旧验收或只替换哈希保留后续勾选。

首读评审只能收到中性任务标识、必要正文与前文、哈希、读取规则和已经核验的依赖结论，不能收到整个任务账本或包含未来秘密的阶段计划。资料核查阶段才能提供相关计划与意图。

## 命令与执行顺序

命令中的 `SKILL`、`BOOK` 使用真实绝对路径，请求文件使用实际路径。主 agent 可运行 `check`、`gate` 等只读命令；`init`、`plan`、`update`、`submit`、`render`、`close` 由资料子 agent 执行。请求 JSON 优先保存为临时文件，避免 shell 转义；这些请求不构成第二份任务账本。下列是 Bash 变量示例；Windows PowerShell 请用带引号的实际路径。`python` 须为 Python 3.9+，Linux/macOS 可替换为 `python3`，Windows 可替换为 `py -3`。

```bash
python "$SKILL/scripts/taskctl.py" init --book "$BOOK"
python "$SKILL/scripts/taskctl.py" plan --book "$BOOK" --request /绝对路径/阶段请求.json
python "$SKILL/scripts/taskctl.py" update --book "$BOOK" --request /绝对路径/任务请求.json
python "$SKILL/scripts/taskctl.py" submit --book "$BOOK" --request /绝对路径/提交请求.json
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

`taskctl check` 的 `warnings` 按文件位置和章稿命名提示未登记的候选／评审产物。这只是定位线索，不证明任务执行过，更不会自动改状态。发现“已有后续版本、游标仍停在准备”，先对照产物、输入与宿主记录恢复真实待办：已有草稿不要重复起草，已读 v2 不冒充已读 v3，无证据的验收保持待完成。普通未采用备选在接续说明注明用途，不假装成为流程产物。

当时实际使用的资料与当前继续执行的约束分别保留。需要长期引用的输入先保存不可变版本或指向已验证历史副本，再登记哈希；后续资料改变时，保留旧证据并为当前约束重新核查。不要为消除告警删除依赖，也不能仅更新哈希沿用原验收。历史阶段不补造新事实，当前修订用新任务承接。
