import { useState, type ReactNode } from "react";
import type {
  LessonComponent,
  LessonContrastComponent,
  LessonDerivationComponent,
  LessonFormulaComponent,
  LessonNumberLineComponent,
  LessonTextComponent,
  MicroLessonView,
} from "../../api/types";
import { StatePanel } from "../feedback/StatePanel";
import { IconCheck } from "../icons";

interface Rational {
  n: number;
  d: number;
}

const MAX_COMPONENT_TEXT = 2000;

/**
 * 受限表达式的安全排版：上标、运算符、关系符分别成元素，不引入外部脚本，
 * 也不把表达式当 HTML。项目没有 KaTeX/MathJax 依赖，因此不做完整 LaTeX。
 */
function renderMathNodes(expr: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let buffer = "";
  let cursor = 0;
  const flush = () => {
    if (buffer) {
      nodes.push(buffer);
      buffer = "";
    }
  };
  while (cursor < expr.length) {
    const char = expr[cursor];
    if (char === "^") {
      flush();
      let power = "";
      let next = cursor + 1;
      if (expr[next] === "(") {
        const close = expr.indexOf(")", next);
        if (close > next) {
          power = expr.slice(next + 1, close);
          next = close + 1;
        }
      }
      if (!power) {
        const match = /^[+-]?\d+/.exec(expr.slice(next));
        if (match) {
          power = match[0];
          next += power.length;
        } else if (expr[next] !== undefined) {
          power = expr[next];
          next += 1;
        }
      }
      nodes.push(
        <span className="mathPower" key={`power-${cursor}`}>
          <sup>{power}</sup>
        </span>,
      );
      cursor = next;
      continue;
    }
    if (char === "*") {
      flush();
      nodes.push(
        <span className="mathOp" key={`op-${cursor}`} aria-label="乘">
          ×
        </span>,
      );
      cursor += 1;
      continue;
    }
    if (char === "/") {
      flush();
      nodes.push(
        <span className="mathOp" key={`slash-${cursor}`} aria-label="除以">
          /
        </span>,
      );
      cursor += 1;
      continue;
    }
    if (char === "<" || char === ">" || char === "=") {
      flush();
      let symbol = char;
      if (expr[cursor + 1] === "=") {
        symbol = char === "<" ? "≤" : "≥";
        cursor += 2;
      } else {
        cursor += 1;
      }
      nodes.push(
        <span className="mathRelation" key={`rel-${cursor}`} aria-label={`关系符 ${symbol}`}>
          {symbol}
        </span>,
      );
      continue;
    }
    buffer += char;
    cursor += 1;
  }
  flush();
  return nodes;
}

/** 只读取已授权来源的安全字段；即使来源里带 url 也不渲染成链接。 */
function sourceDetail(source: Record<string, unknown> | null): string | null {
  if (!source) return null;
  const base = source.source_label ?? source.label ?? source.file_name ?? source.title;
  if (typeof base !== "string" || !base.trim()) return null;
  const parts = [base.trim()];
  const chapter = source.chapter ?? source.section;
  if (typeof chapter === "string" && chapter.trim()) parts.push(chapter.trim());
  const page = source.printed_page ?? source.pdf_page ?? source.page;
  if (typeof page === "number" || (typeof page === "string" && page.trim())) {
    parts.push(`第 ${String(page).trim()} 页`);
  }
  return parts.join(" · ");
}

function parseRational(raw: string | null | undefined): Rational | null {
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!text) return null;
  const fraction = /^([+-]?\d+)\/(\d+)$/.exec(text);
  if (fraction) {
    const n = Number(fraction[1]);
    const d = Number(fraction[2]);
    if (!Number.isSafeInteger(n) || !Number.isSafeInteger(d)) return null;
    if (d < 1 || d > 1000 || Math.abs(n) > 10000) return null;
    return { n, d };
  }
  const decimal = /^([+-]?)(\d+)(?:\.(\d{1,3}))?$/.exec(text);
  if (!decimal) return null;
  const sign = decimal[1] === "-" ? -1 : 1;
  const whole = Number(decimal[2]);
  const decimals = decimal[3] ?? "";
  const denominator = 10 ** decimals.length;
  const numerator = sign * (whole * denominator + Number(decimals || "0"));
  if (!Number.isSafeInteger(numerator) || Math.abs(numerator) > 10000) return null;
  return { n: numerator, d: denominator };
}

function compare(left: Rational, right: Rational): number {
  return left.n * right.d - right.n * left.d;
}

function toNumber(value: Rational): number {
  return value.n / value.d;
}

function intervalText(lo: Rational | null, hi: Rational | null, loClosed: boolean, hiClosed: boolean): string {
  if (!lo && !hi) return "全体实数";
  if (!lo && hi) return `x ${hiClosed ? "≤" : "<"} ${hi.n}/${hi.d}`;
  if (lo && !hi) return `x ${loClosed ? "≥" : ">"} ${lo.n}/${lo.d}`;
  return `${loClosed ? "[" : "("}${lo?.n}/${lo?.d}, ${hi?.n}/${hi?.d}${hiClosed ? "]" : ")"}`;
}

interface ValidationResult {
  ok: boolean;
  reason: string;
  components: LessonComponent[];
}

function isShortText(value: unknown, max = MAX_COMPONENT_TEXT): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

function validateText(component: LessonTextComponent): string | null {
  if (!Array.isArray(component.paragraphs) || component.paragraphs.length < 1 || component.paragraphs.length > 4) {
    return "段落数量不在允许范围";
  }
  if (!component.paragraphs.every((item) => isShortText(item, 300))) return "段落文本为空或超长";
  if (component.heading !== undefined && component.heading !== null && !isShortText(component.heading, 120)) {
    return "标题无效";
  }
  return null;
}

function validateFormula(component: LessonFormulaComponent): string | null {
  if (!isShortText(component.expr, 160)) return "公式表达式为空或超长";
  if (component.caption !== undefined && component.caption !== null && !isShortText(component.caption, 200)) {
    return "公式说明无效";
  }
  return null;
}

function validateDerivation(component: LessonDerivationComponent): string | null {
  if (!["expression", "equation", "inequality"].includes(component.mode)) return "推导模式未知";
  if (!Array.isArray(component.steps) || component.steps.length < 2 || component.steps.length > 6) {
    return "推导步骤数量不在允许范围";
  }
  if (!component.steps.every((step) => isShortText(step.expr, 200) && isShortText(step.note, 300))) {
    return "推导步骤文本无效";
  }
  return null;
}

function validateContrast(component: LessonContrastComponent): string | null {
  for (const field of ["before", "incorrect", "correct"] as const) {
    if (!isShortText(component[field], 400)) return "对照内容不完整";
  }
  if (component.note !== undefined && component.note !== null && !isShortText(component.note, 300)) return "对照说明无效";
  return null;
}

function validateCitation(component: { labels: Array<{ label: string; source_index?: number | null; evidence_index?: number | null }> }): string | null {
  if (!Array.isArray(component.labels) || component.labels.length === 0) return "引用标签为空";
  for (const label of component.labels) {
    if (!isShortText(label.label, 160)) return "引用标签文本无效";
    const hasSource = label.source_index !== undefined && label.source_index !== null;
    const hasEvidence = label.evidence_index !== undefined && label.evidence_index !== null;
    if (hasSource && hasEvidence) return "引用不能同时指向两个索引";
    if (!hasSource && !hasEvidence) return "引用必须指向一个来源或证据";
    for (const index of [label.source_index, label.evidence_index]) {
      if (index !== undefined && index !== null && (!Number.isInteger(index) || index < 0)) return "引用索引非法";
    }
  }
  return null;
}

function validateNumberLine(component: LessonNumberLineComponent): string | null {
  const min = parseRational(component.min);
  const max = parseRational(component.max);
  const tick = parseRational(component.tick);
  if (!min || !max || !tick) return "数轴范围不是可解析的有理数";
  if (compare(min, max) >= 0) return "数轴最小值必须小于最大值";
  if (tick.n <= 0) return "刻度必须为正数";
  const span = toNumber(max) - toNumber(min);
  const step = toNumber(tick);
  if (step <= 0 || span / step > 40) return "刻度数量超出上限";
  if (!Array.isArray(component.sets) || component.sets.length < 1 || component.sets.length > 3) return "解集组数超出上限";
  for (const set of component.sets) {
    if (!isShortText(set.label, 80)) return "解集标签无效";
    if (!Array.isArray(set.intervals) || set.intervals.length < 1 || set.intervals.length > 3) return "区间数量超出上限";
    for (const interval of set.intervals) {
      const lo = interval.lo === null || interval.lo === "" ? null : parseRational(interval.lo);
      const hi = interval.hi === null || interval.hi === "" ? null : parseRational(interval.hi);
      if (interval.lo !== null && interval.lo !== "" && !lo) return "区间端点不是可解析的有理数";
      if (interval.hi !== null && interval.hi !== "" && !hi) return "区间端点不是可解析的有理数";
      if (!lo && !hi) return "区间不能两端都无界";
      if (lo && hi && compare(lo, hi) >= 0) return "区间下端点必须小于上端点";
      if (lo && compare(lo, min) < 0) return "区间超出数轴范围";
      if (hi && compare(hi, max) > 0) return "区间超出数轴范围";
      if (typeof interval.lo_closed !== "boolean" || typeof interval.hi_closed !== "boolean") return "区间开闭标记缺失";
    }
  }
  const operation = component.operation ?? null;
  if (operation !== null && !["intersection", "union"].includes(operation)) return "集合运算类型未知";
  if (operation === "intersection") {
    if (component.sets.length !== 3 || component.result_index !== 2) return "交集结果结构不符合审核规则";
  }
  return null;
}

/** 运行时再校验一次，防止浏览器兼容问题；校验失败只允许读 fallback，不允许完成。 */
export function validateLesson(lesson: MicroLessonView): ValidationResult {
  if (lesson.schema_version !== "1.0") {
    return { ok: false, reason: "微课堂内容版本不受当前渲染器支持。", components: [] };
  }
  if (!Array.isArray(lesson.components) || lesson.components.length === 0) {
    return { ok: false, reason: "微课堂没有可渲染的组件。", components: [] };
  }
  const ids = new Set<string>();
  const components: LessonComponent[] = [];
  for (const raw of lesson.components) {
    const component = raw as LessonComponent;
    if (!component || typeof component !== "object" || typeof component.type !== "string" || !isShortText(component.id, 80)) {
      return { ok: false, reason: "组件缺少合法编号。", components: [] };
    }
    if (ids.has(component.id)) return { ok: false, reason: "组件编号重复。", components: [] };
    ids.add(component.id);
    let reason: string | null = null;
    switch (component.type) {
      case "text":
        reason = validateText(component);
        break;
      case "formula":
        reason = validateFormula(component);
        break;
      case "derivation":
        reason = validateDerivation(component);
        break;
      case "contrast":
        reason = validateContrast(component);
        break;
      case "number_line":
        reason = validateNumberLine(component);
        break;
      case "citation":
        reason = validateCitation(component);
        break;
      default:
        reason = `暂不支持组件类型 ${String((component as { type?: unknown }).type)}。`;
    }
    if (reason) return { ok: false, reason, components: [] };
    components.push(component);
  }
  return { ok: true, reason: "", components };
}

export function LessonRenderer({ lesson }: { lesson: MicroLessonView }) {
  const validation = validateLesson(lesson);
  if (!validation.ok) {
    return (
      <div className="stack">
        <StatePanel
          kind="not-ready"
          title="微课堂内容暂不可读"
          message={`${validation.reason} 页面不会执行脚本或渲染未校验内容，也不能把这次活动标记为完成。`}
        />
        {isShortText(lesson.fallback_text) ? (
          <div className="reading prose">
            <p className="paneTitle">安全回退文本</p>
            <p style={{ whiteSpace: "pre-wrap" }}>{lesson.fallback_text}</p>
          </div>
        ) : null}
      </div>
    );
  }
  const hasMath = validation.components.some(
    (component) => component.type === "formula" || component.type === "derivation" || component.type === "contrast",
  );
  return (
    <div className="reading stackLg lessonBody">
      {validation.components.map((component) => (
        <LessonComponentView key={component.id} component={component} lesson={lesson} />
      ))}
      {hasMath ? (
        <p className="xs muted lessonMathNote">
          数学式按受限表达式排版（上标、运算符、分行），不是完整 LaTeX 渲染，也不加载外部脚本。
        </p>
      ) : null}
    </div>
  );
}

function LessonComponentView({ component, lesson }: { component: LessonComponent; lesson: MicroLessonView }) {
  switch (component.type) {
    case "text":
      return <TextBlock component={component} />;
    case "formula":
      return <FormulaBlock component={component} />;
    case "derivation":
      return <DerivationBlock component={component} />;
    case "contrast":
      return <ContrastBlock component={component} />;
    case "number_line":
      return <NumberLineBlock component={component} />;
    case "citation":
      return <CitationBlock component={component} lesson={lesson} />;
    default:
      return (
        <section className="lessonUnknown">
          <p className="small muted">这一段内容类型当前版本不支持，已安全跳过，不影响其余内容阅读。</p>
        </section>
      );
  }
}

function TextBlock({ component }: { component: LessonTextComponent }) {
  return (
    <section className="lessonBlock">
      {component.heading ? <h2 className="sectionTitle">{component.heading}</h2> : null}
      {component.paragraphs.map((paragraph, index) => (
        <p className="prose lessonParagraph" key={index}>
          {paragraph}
        </p>
      ))}
    </section>
  );
}

function FormulaBlock({ component }: { component: LessonFormulaComponent }) {
  const [copied, setCopied] = useState(false);
  return (
    <section className="lessonBlock">
      <p className="mathFormula" aria-label={`公式 ${component.expr}`}>
        {renderMathNodes(component.expr)}
      </p>
      {component.caption ? <p className="small muted lessonFormulaCaption">{component.caption}</p> : null}
      <button
        type="button"
        className="btn btnSm btnGhost"
        onClick={() => {
          void navigator.clipboard?.writeText(component.expr).then(
            () => setCopied(true),
            () => setCopied(false),
          );
        }}
      >
        {copied ? <><IconCheck size={14} /> 已复制原表达</> : "复制原表达"}
      </button>
    </section>
  );
}

function DerivationBlock({ component }: { component: LessonDerivationComponent }) {
  const modeLabel = component.mode === "equation" ? "等式变形" : component.mode === "inequality" ? "不等式变形" : "表达式推演";
  return (
    <section className="lessonBlock">
      <h3 className="paneTitle">{modeLabel}</h3>
      <ol className="derivationList">
        {component.steps.map((step, index) => (
          <li key={index} className="derivationStep">
            <span className="derivationIndex" aria-hidden="true">{index + 1}</span>
            <span className="derivationStepBody">
              <span className="derivationExpr" aria-label={`第 ${index + 1} 步：${step.expr}`}>
                {renderMathNodes(step.expr)}
              </span>
              <span className="small muted">{step.note}</span>
            </span>
          </li>
        ))}
      </ol>
      <p className="xs muted">每一步都来自课程材料并经过独立数学复核，页面不生成新的解答步骤。</p>
    </section>
  );
}

function ContrastBlock({ component }: { component: LessonContrastComponent }) {
  return (
    <section className="lessonBlock">
      <div className="contrastGrid">
        <div className="contrastCell">
          <h4 className="paneTitle">原题 / 情境</h4>
          <p className="prose contrastExpr">{component.before}</p>
        </div>
        <div className="contrastCell contrastCellWrong">
          <h4 className="paneTitle contrastHeadingWrong">错误做法</h4>
          <p className="prose contrastExpr">{component.incorrect}</p>
        </div>
        <div className="contrastCell contrastCellRight">
          <h4 className="paneTitle contrastHeadingRight">正确做法</h4>
          <p className="prose contrastExpr">{component.correct}</p>
        </div>
      </div>
      {component.note ? <p className="small muted">{component.note}</p> : null}
    </section>
  );
}

function NumberLineBlock({ component }: { component: LessonNumberLineComponent }) {
  const min = parseRational(component.min) as Rational;
  const max = parseRational(component.max) as Rational;
  const tick = parseRational(component.tick) as Rational;
  const left = 48;
  const right = 672;
  const position = (value: number) => left + ((value - toNumber(min)) / (toNumber(max) - toNumber(min))) * (right - left);
  const ticks: number[] = [];
  for (let value = toNumber(min); value <= toNumber(max) + 1e-9 && ticks.length < 41; value += toNumber(tick)) {
    ticks.push(Math.round(value * 1e6) / 1e6);
  }
  const rowHeight = 54;
  const height = 56 + component.sets.length * rowHeight;

  const operationLabel = component.operation === "intersection" ? "取交集" : component.operation === "union" ? "取并集" : null;

  return (
    <section>
      <h3 className="paneTitle">数轴表示</h3>
      <div className="numberLineWrap">
        <svg viewBox={`0 0 ${right + 24} ${height}`} role="img" aria-label="数轴与解集">
          {component.sets.map((set, setIndex) => {
            const y = 48 + setIndex * rowHeight;
            const isResult = component.result_index === setIndex && component.operation === "intersection";
            return (
              <g key={`${set.label}-${setIndex}`}>
                <text x={4} y={y - 12} fontSize={12} fill="var(--color-text-secondary)">
                  {set.label}{isResult ? "（交集结果）" : ""}
                </text>
                <line x1={left} y1={y} x2={right} y2={y} stroke="var(--color-border)" strokeWidth={1} />
                {set.intervals.map((interval, intervalIndex) => {
                  const lo = interval.lo === null || interval.lo === "" ? null : parseRational(interval.lo);
                  const hi = interval.hi === null || interval.hi === "" ? null : parseRational(interval.hi);
                  const x1 = lo ? position(toNumber(lo)) : left;
                  const x2 = hi ? position(toNumber(hi)) : right;
                  return (
                    <g key={intervalIndex}>
                      <line
                        x1={x1}
                        y1={y}
                        x2={x2}
                        y2={y}
                        stroke="var(--color-primary)"
                        strokeWidth={3}
                      />
                      {lo ? (
                        <circle
                          cx={x1}
                          cy={y}
                          r={5}
                          fill={interval.lo_closed ? "var(--color-primary)" : "var(--color-background)"}
                          stroke="var(--color-primary)"
                          strokeWidth={1.75}
                        />
                      ) : (
                        <path d={`M ${left} ${y} l 8 -5 v 10 z`} fill="none" stroke="var(--color-primary)" strokeWidth={1.75} />
                      )}
                      {hi ? (
                        <circle
                          cx={x2}
                          cy={y}
                          r={5}
                          fill={interval.hi_closed ? "var(--color-primary)" : "var(--color-background)"}
                          stroke="var(--color-primary)"
                          strokeWidth={1.75}
                        />
                      ) : (
                        <path d={`M ${right} ${y} l -8 -5 v 10 z`} fill="none" stroke="var(--color-primary)" strokeWidth={1.75} />
                      )}
                      {lo ? (
                        <text x={x1} y={y + 22} textAnchor="middle" fontSize={10} fill="var(--color-text-secondary)">
                          {interval.lo_closed ? "含端点" : "不含端点"}
                        </text>
                      ) : null}
                      {hi ? (
                        <text x={x2} y={y + 22} textAnchor="middle" fontSize={10} fill="var(--color-text-secondary)">
                          {interval.hi_closed ? "含端点" : "不含端点"}
                        </text>
                      ) : null}
                    </g>
                  );
                })}
              </g>
            );
          })}
          <line x1={left} y1={20} x2={left} y2={height - 10} stroke="var(--color-border)" strokeWidth={1} />
          {ticks.map((value) => (
            <g key={value}>
              <line x1={position(value)} y1={14} x2={position(value)} y2={20} stroke="var(--color-text-secondary)" strokeWidth={1} />
              <text x={position(value)} y={10} textAnchor="middle" fontSize={10} fill="var(--color-text-secondary)">
                {Number.isInteger(value) ? value : value.toFixed(2)}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className="stackSm" style={{ marginTop: 8 }}>
        {component.sets.map((set, index) => (
          <p className="small" key={`${set.label}-text-${index}`}>
            {set.label}：{set.intervals.map((interval) => {
              const lo = interval.lo === null || interval.lo === "" ? null : parseRational(interval.lo);
              const hi = interval.hi === null || interval.hi === "" ? null : parseRational(interval.hi);
              return intervalText(lo, hi, interval.lo_closed, interval.hi_closed);
            }).join(" 或 ")}
          </p>
        ))}
        {operationLabel ? <p className="small muted">集合运算：{operationLabel}，结果按审核版本绘制，页面不重新推导。</p> : null}
      </div>
    </section>
  );
}

function CitationBlock({ component, lesson }: { component: { labels: Array<{ label: string; source_index?: number | null; evidence_index?: number | null }> }; lesson: MicroLessonView }) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const sources = Array.isArray(lesson.safe_sources) ? lesson.safe_sources : [];
  return (
    <section className="lessonBlock lessonCitation">
      <h3 className="paneTitle">讲解依据</h3>
      <ul className="sourceList">
        {component.labels.map((label, index) => {
          const sourceIndex = label.source_index;
          const evidenceIndex = label.evidence_index;
          const source =
            sourceIndex !== undefined && sourceIndex !== null
              ? ((sources[sourceIndex] as Record<string, unknown> | undefined) ?? null)
              : null;
          const detail = sourceDetail(source);
          const resolved = detail !== null;
          const open = openIndex === index;
          const summary =
            sourceIndex !== undefined && sourceIndex !== null
              ? `来源索引 ${sourceIndex}`
              : `关联证据 ${evidenceIndex}`;
          return (
            <li key={index} className="sourceItem">
              <button
                type="button"
                className="btn btnSm btnGhost citationToggle"
                aria-expanded={open}
                onClick={() => setOpenIndex(open ? null : index)}
              >
                {label.label}
                <span className="xs muted"> · {summary} · {resolved ? "可展开来源" : "未附带来源原文"}</span>
              </button>
              {open ? (
                <div className="citationDetail" role="group" aria-label={`${label.label} 的来源说明`}>
                  {resolved ? (
                    <p className="small">来源：{detail}（服务端已授权；不提供外部链接）</p>
                  ) : evidenceIndex !== undefined && evidenceIndex !== null ? (
                    <p className="small">
                      关联证据 {evidenceIndex}：当前发布版本没有附带可展开的证据原文，请按上面的知识点/诊断编号在对应课程页核对。
                    </p>
                  ) : (
                    <p className="small">当前发布版本没有下发可核验的来源，页面不显示外部链接。</p>
                  )}
                  <p className="xs muted">引用只指向服务端授权的材料或证据，不提供可点击的外部链接。</p>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
