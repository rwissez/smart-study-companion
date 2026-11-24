# C 模块 AI 决策架构方案（v1 提案）

> 状态：待 A 评审。当前代码（bab0825）全部保留为兜底实现，本方案不推翻现有规则引擎，而是在其上叠加 AI 决策层。

## 0. 核心原则：AI 提案，规则裁决（LLM proposes, rules dispose）

现状是纯规则链：启发式分类 → 规则排序 → 线性归并 → 模板文案。缺点是 A 指出的机械：面对边界样本（比如学生既卡步骤又答对结果）只会按固定优先级切，不会像有经验的老师那样权衡。

升级思路不是"用 AI 替换规则"，而是分两层：

1. **AI 层负责权衡**：面对边界和组合情形，由模型给出判定提案和归并方案，带理由。
2. **规则层负责裁决**：每一份 AI 输出都必须通过既有的红线校验（leak_check 体系、负荷约束、DUPLICATE_GOAL、类别合法性）；任何一份不通过就丢弃，最终永远回退到纯规则结果。

这样保证：模型不可用、输出乱码、幻觉越界时，系统行为与今天完全一致（147 个测试就是兜底行为的规格说明）。

## 1. 接入点一：判定增强（FindingClassifier）

**现状**：`classify_finding` 启发式——description 含过程/步骤信号且 limitations 未声明缺过程 → 1a，其余按阶梯。边界样本只能二选一。

**升级**：把 finding + 活动证据交给模型，`response_schema` 约束输出：

```json
{
  "kind": "guided_inquiry | scaffolded_practice | observe | migrate",
  "confidence": 0.0-1.0,
  "rationale": "引用证据的判定理由（≤80字）"
}
```

**裁决规则**：
- kind 必须在合法枚举内，否则回退启发式；
- rationale 不得引入 B 未输出的判断（不得出现会/不会、学习风格诊断、超出 evidence 的归因）——复用 leak_check 的检查精神做输出过滤；
- confidence < 0.6 时回退启发式；
- rationale 作为 teacher_fallback_note 的补充素材，标注来源为模型判定。

## 2. 接入点二：策略归并（StrategyAdvisor）——多重建议决策

这是本方案的核心，也是指的多重建议机制。

**现状**：`choose_strategy` 按固定优先级线性归并，一个输入组合永远只有一个输出。

**升级**：模型拿到全部候选策略 + 负荷约束 + 学生上下文，产出 **2-3 套完整的归并方案**（哪些活动合并进同一建议、哪些降级暂缓、教师处理顺序如何排），每套带一段选择理由。程序逐套执行：

```
方案1 → validate_recommendations（红线 + 负荷 + DUPLICATE_GOAL）→ 全过 → 采纳
方案2 → 同上
方案3 → 同上
全部不过 → 回退 RuleBasedAdvisor 的纯规则结果
```

要点：
- **纯规则结果永远先生成**，作为保底和对照——AI 只是可能替换它，不是依赖它；
- 每套方案的 validate 全走现有校验器，AI 无法绕过任何红线；
- 采纳的方案在 Recommendation 上记录 `advice_source: "llm" | "rule"` 与采纳理由，教师端可追溯（对齐全链路可追溯要求）。

## 3. 接入点三（可选，低优先级）：文案润色

builder 的 reason / student_intro 现在是模板变体。可让模型基于结构化槽位（指标、证据状态、扶助卡有无）生成自然语言文案，输出全文过 leak_check + 语气规范校验，不合格用模板兜底。

**暂缓理由**：1b/1a 文案是教育行为规范敏感区，递减干预、接住即停这些约束在模板里是硬编码分支，模型自由发挥反而增加越界风险。建议演示后再评估。

## 4. 端口隔离：C 不直接依赖 ModelProvider

C 自定义自己的窄接口，不 import agent 模块：

```python
class StrategyAdvisor(Protocol):
    name: str
    def advise(self, findings, candidates, constraints) -> StrategyAdvice: ...
```

两个实现：
- **RuleBasedAdvisor**：现有全部规则行为的包装，零外部依赖，默认实现；
- **LLMStrategyAdvisor**：内部持有 B 的 `ModelProvider`，unconfigured provider 时自动降级返回 Rule 版结果。

组装处按 `settings` 决定注入哪个。收益：
- 147 个测试不依赖任何模型，全绿不变；
- B 的 provider 接好后，C 只改一处组装配置；
- Advisor 是纯端口，未来换模型/加缓存不动业务代码。

## 5. 红线复核（AI 不改变的事）

| 红线 | AI 介入后 |
| --- | --- |
| 不产生 B 未输出的判断 | rationale/方案理由全文过输出过滤 |
| 建议 ≤ 20% 教师负荷 | validate 的负荷校验对 AI 方案同样生效 |
| 递减干预、接住即停 | kind 枚举 + hint_levels 结构约束，模型只能选不能造 |
| 学生端无排名/无术语 | student_intro 过滤器对 AI 文案全文校验 |
| 全链路可追溯 | advice_source + rationale 落库 |

## 6. 落地切分

| 阶段 | 内容 | 依赖 |
| --- | --- | --- |
| P0（已具备） | RuleBasedAdvisor 包装现有行为，行为零变化 | 无 |
| P1 | StrategyAdvisor 端口 + 校验裁决循环 + 回退 | 无（fake provider 测试） |
| P2 | FindingClassifier LLM 实现 | B 真实 provider |
| P3 | 文案润色（可选） | P2 稳定后评估 |

P1 不依赖 B 的真实模型——用 scripted fake provider 就能测完整裁决循环（合法提案采纳/越界提案拒绝/全部拒绝回退规则），可以在 B 交付前完成。

## 7. 风险

- **延迟**：每次判定多 1-2 次模型调用（同步协议），课堂实时场景需评估延迟预算——可在 Advisor 层做超时熔断（超 2 秒直接用规则结果）；
- **成本**：多重方案生成消耗 token，建议 P1 先只在 bundle 级（策略归并）启用，判定级 P2 再开；
- **一致性**：同一输入两次调用可能出不同方案——对无状态判可加温度 0 约定，写入 Advisor 契约。
