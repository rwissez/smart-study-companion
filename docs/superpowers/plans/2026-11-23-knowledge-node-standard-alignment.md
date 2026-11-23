# Knowledge Node Standard Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有知识图谱模块与已确认的节点粒度标准对齐，使概念节点和程序性能力节点都能被提取、审核、限制数量并安全投影到现有 `Catalog`。

**Architecture:** 保持 `contracts`、HTTP、B/C 固定签名不变，只修改 `study_companion.knowledge` 内部模型和处理流程。自动抽取只产生候选；粒度检查输出审核问题而不静默删除；只有人工确认的节点和关系进入正式目录投影。

**Tech Stack:** Python 3.12、Pydantic v2、pytest、现有 `study_companion.knowledge` 包。

**Spec:** `docs/superpowers/specs/2026-11-23-knowledge-node-granularity-design.md`；现有增量方案见 `docs/team/18_知识点图谱与自动入库设计.md`。

## Global Constraints

- 现有 `Node`/`node_id` 继续表示测验阶段，知识点使用 `kp_id`。
- 不修改 `Catalog`、`Indicator`、`Material`、`CatalogLink` 契约和 B/C 函数签名。
- 每主题建议 3–8 个节点、每指标 1–3 个、每章原则上不超过 10 个。
- 程序性能力节点计入数量预算；单个机械动作默认是 `procedure_steps`。
- 自动结果只能是 `unverified`/`candidate`，不能自动升级为 `verified`。
- 真实教材文本和题干留在被忽略的 `data/`；测试只使用自拟短文本。
- RapidOCR 是否进入默认依赖不在本计划内。

## Review Focus

- “解方程流程”必须能成为 `procedure`，而“移项”默认不能被单独升级为节点。
- 超过章节节点上限时必须产生可读审核问题，不能静默截断或删除节点。
- 旧版 `kind=property|method|theorem` 文件必须能够迁移或给出明确错误，不能无提示损坏。
- 同义表述和重复定义应合并到一个节点，后续片段只增加证据或别名。
- 未核对节点和关系不得进入正式 `Catalog.links`，也不得被标成 `verified` 材料。

---

### Task 1: 对齐内部节点模型

**Files:**
- Modify: `src/study_companion/knowledge/models.py`
- Create: `src/study_companion/knowledge/migrate.py`
- Modify: `tests/knowledge/test_knowledge.py`
- Create: `tests/knowledge/test_model_migration.py`

**Interfaces:**
- Consumes: existing `KnowledgeBase` JSON using `concept|property|theorem|method|unknown`.
- Produces: node types `concept|definition|principle|procedure|strategy` and a deterministic migration function.

- [ ] **Step 1: Write failing model tests**

Assert that a `procedure` node accepts ordered `procedure_steps`; concept/principle nodes may carry `rules`、`misconceptions`、`application_tags`; all fields default to empty lists and remain `extra="forbid"`.

- [ ] **Step 2: Implement the approved node model**

Change `KpKind` to the five approved types and add:

```python
procedure_steps: list[str]
rules: list[str]
misconceptions: list[str]
application_tags: list[str]
```

Do not add these fields to shared contracts.

- [ ] **Step 3: Write failing migration tests**

Map `property -> principle`、`method -> procedure`、`theorem -> principle`、`concept -> concept`; require a reviewer decision for `unknown`. Preserve IDs、locators、aliases、relations and annotations exactly.

- [ ] **Step 4: Implement migration**

Implement:

```python
def migrate_knowledge_base(payload: dict) -> KnowledgeBase: ...
```

The function must return a new object and never rewrite the source file automatically.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/knowledge/test_knowledge.py tests/knowledge/test_model_migration.py -q`

Commit: `git commit -m "对齐知识节点类型与程序性能力模型"`

### Task 2: 增加节点准入与粒度审核

**Files:**
- Create: `src/study_companion/knowledge/granularity.py`
- Create: `tests/knowledge/test_granularity.py`

**Interfaces:**
- Consumes: `KnowledgeBase` plus optional indicator/topic ownership maps.
- Produces: `GranularityReport` with structured issues; does not mutate the graph.

- [ ] **Step 1: Write failing eligibility tests**

Test a regular node against the four approved criteria and a procedure against all six procedure criteria. “解一元一次方程流程” with goal、ordered steps、assessment evidence and prerequisites passes; “移项” with one step fails as a standalone procedure.

- [ ] **Step 2: Implement eligibility checks**

Implement:

```python
def review_node_eligibility(point: KnowledgePoint, context: ReviewContext) -> list[GranularityIssue]: ...
```

The report recommends `keep|merge|attribute|reject`; it never changes `verification_status`.

- [ ] **Step 3: Write failing budget tests**

Cover topic 3–8、indicator 1–3、chapter maximum 10. Exceeding a soft range is a warning; exceeding the chapter cap is an error requiring review. Procedure nodes count exactly like other nodes.

- [ ] **Step 4: Implement budget review**

Implement:

```python
def review_graph_granularity(kb: KnowledgeBase) -> GranularityReport: ...
```

Return counts by chapter、indicator and kind plus exact offending IDs.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/knowledge/test_granularity.py -q`

Commit: `git commit -m "增加知识节点准入与数量限制"`

### Task 3: 调整规则抽取的节点分类

**Files:**
- Modify: `src/study_companion/knowledge/terms.py`
- Modify: `tests/knowledge/test_knowledge.py`
- Create: `tests/knowledge/test_term_classification.py`

**Interfaces:**
- Consumes: page text and locators.
- Produces: candidate nodes using the approved five types; all remain `unverified`.

- [ ] **Step 1: Write failing classification tests**

Use synthetic sentences for “叫作/称为” definitions、性质/定理、完整解题步骤 and application strategies. Assert that a multi-step “解某类方程的一般步骤” becomes `procedure`, while isolated “移项” remains a rule/step candidate attached to its parent or an unresolved candidate rather than a verified node.

- [ ] **Step 2: Update deterministic classification**

Replace `property` with `principle`; classify explicit definition statements as `definition`; classify only explicit multi-step headings/paragraphs as `procedure`; classify modelling/解题策略 headings as `strategy`. Ambiguous terms remain candidates requiring review, not `unknown` persisted nodes.

- [ ] **Step 3: Preserve first-seen and alias behavior**

Add regression tests proving repeated terms do not create new nodes and later aliases are appended without changing the first locator.

- [ ] **Step 4: Run noise regression cases**

Add synthetic analogues of the known false positives (“一个圆”“单元格”“什么数”) and assert they are rejected or reported for review rather than accepted as nodes.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/knowledge/test_term_classification.py tests/knowledge/test_knowledge.py -q`

Commit: `git commit -m "按确认标准分类概念与程序性能力"`

### Task 4: 建立人工金标准格式和审核流程

**Files:**
- Create: `src/study_companion/knowledge/review.py`
- Create: `tests/fixtures/knowledge/gold_unit_synthetic.json`
- Create: `tests/knowledge/test_review.py`
- Modify: `src/study_companion/cli.py`

**Interfaces:**
- Consumes: extracted draft plus an explicit review decision file.
- Produces: reviewed `KnowledgeBase`; only human decisions may set `verified`.

- [ ] **Step 1: Define a synthetic gold-unit fixture**

Include a concept、principle、procedure and strategy; a procedure with internal steps; one merged alias; one item downgraded to `attribute`; and verified/candidate relations. Do not use competition text.

- [ ] **Step 2: Write failing review-application tests**

Assert that decisions reference existing IDs, preserve the original draft, reject duplicate decisions and require reviewer/time/note when setting `verified`.

- [ ] **Step 3: Implement review application**

Implement:

```python
def apply_review(kb: KnowledgeBase, decisions: ReviewDecisionSet) -> KnowledgeBase: ...
```

Support `verified|merged|attribute|rejected`; merged/attribute items must record their destination parent.

- [ ] **Step 4: Add CLI commands**

Add commands that do not change existing CLI behavior:

```text
study-companion knowledge-review --draft ... --decisions ... --out ...
study-companion knowledge-check --kb ...
```

`knowledge-check` prints granularity counts and issues and exits non-zero on errors.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/knowledge/test_review.py tests/test_cli.py -q`

Commit: `git commit -m "增加知识点人工审核与金标准流程"`

### Task 5: 为智能体候选保留稳定接口

**Files:**
- Create: `src/study_companion/knowledge/proposer.py`
- Create: `tests/knowledge/test_proposer.py`
- Create after the real gold unit exists: `prompts/kp_relation.md`
- Create after the real gold unit exists: `prompts/kp_annotation.md`

**Interfaces:**
- Consumes: reviewed examples、segment text and existing knowledge base.
- Produces: candidate nodes、relations and annotations only.

- [ ] **Step 1: Define provider-neutral candidate types**

Create a protocol whose output is parsed through Pydantic models and cannot contain `verification_status="verified"`.

```python
class KnowledgeCandidateProposer(Protocol):
    def propose(self, segment: SegmentContext, kb: KnowledgeBase) -> CandidateBatch: ...
```

- [ ] **Step 2: Write a scripted proposer test**

Use a local scripted fake to prove node candidates pass granularity review and relation candidates pass `validate_candidates`; invalid evidence、cycles and over-fine nodes are rejected with codes.

- [ ] **Step 3: Implement orchestration without a remote model**

Implement candidate validation and audit metadata only. Do not add a provider or API key. Unconfigured mode returns a clear result saying manual review is required.

- [ ] **Step 4: Delay real prompts until A supplies one reviewed unit**

Once A completes a real unit, derive examples from that reviewed JSON and write the two prompt files. Do not invent a gold standard from current rule output.

- [ ] **Step 5: Verify and commit**

Run: `uv run pytest tests/knowledge/test_proposer.py tests/knowledge -q`

Commit: `git commit -m "定义知识候选智能体与程序核对边界"`

### Task 6: 投影、兼容性和全量验收

**Files:**
- Modify: `src/study_companion/knowledge/project.py`
- Modify: `tests/knowledge/test_knowledge.py`
- Create: `tests/knowledge/test_standard_end_to_end.py`
- Modify: `docs/team/18_知识点图谱与自动入库设计.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: reviewed graph conforming to the approved standard.
- Produces: unchanged `Catalog` contract and a detailed projection report.

- [ ] **Step 1: Write the end-to-end standard test**

Synthetic PDF-page text → extraction → review → granularity check → relation validation → `Catalog` projection. Assert that procedure nodes become textbook materials, only verified cross-indicator relations become links, and steps/aliases do not become extra materials.

- [ ] **Step 2: Extend projection reporting**

Report skipped nodes for `merged|attribute|rejected` decisions and include granularity warnings. Do not emit internal kinds or fields into the shared contract.

- [ ] **Step 3: Verify backward compatibility**

Run the existing A/B/C tests and confirm generated `Catalog` still validates with current contracts and C recommendation tests.

- [ ] **Step 4: Update documentation**

Replace the old `concept/property/theorem/method/unknown` wording in document 18 and README. Document that procedure steps are attributes, procedures count against node budgets, and real gold prompts are still blocked on A's reviewed unit.

- [ ] **Step 5: Run final verification**

Run:

```text
uv run pytest tests/knowledge -q
uv run pytest -q
uv run study-companion export-contracts
git diff --check
```

Expected: all tests pass; shared contract files remain unchanged unless OpenAPI output changes for an unrelated reason, which must be investigated rather than committed.

- [ ] **Step 6: Commit**

Commit: `git commit -m "完成知识节点标准对齐与验收"`

## Dependency Order

```text
Task 1 模型对齐
  ├─ Task 2 粒度审核
  └─ Task 3 规则分类
       └─ Task 4 人工金标准
            ├─ Task 5 智能体候选接口
            └─ Task 6 投影与全量验收
```

Task 1–4 可以在没有远程模型的情况下完成。Task 5 的接口和程序核对可以完成，但真实提示词必须等待 A 提供一个审核完成的真实单元。Task 6 是合并前门槛。

## Current Baseline

- Branch: `feat/knowledge-graph-ingestion`
- Existing implementation: extraction、keyword lookup、relation validation、Catalog projection.
- Fresh verification on 2026-11-23: `tests/knowledge` 10 passed；full suite 123 passed；one existing FastAPI TestClient deprecation warning.
