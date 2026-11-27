import { Fragment, type ReactNode } from "react";

/**
 * 安全轻量 Markdown：只渲染标题、粗体、列表、代码、引用、分隔线和简单表格。
 * 全部通过 React 文本节点输出，从不使用 innerHTML，因此模型文本里的 HTML/脚本
 * 只会按字面显示；数学公式与单星号乘法保持纯文本，不做解析或求值。
 */

type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "code"; language: string; lines: string[] }
  | { kind: "quote"; lines: string[] }
  | { kind: "divider" }
  | { kind: "table"; header: string[]; rows: string[][] };

const HEADING = /^(#{1,6})\s+(.*)$/;
const DIVIDER = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const UL_ITEM = /^\s*[-*+]\s+(.*)$/;
const OL_ITEM = /^\s*\d{1,3}[.)]\s+(.*)$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/;
const FENCE = /^\s*```\s*([\w+-]*)\s*$/;

const CJK = /[\u3000-\u9fff\uff00-\uffef]/;

function joinParagraph(lines: string[]): string {
  return lines.reduce((joined, line) => {
    if (!joined) return line;
    const left = joined[joined.length - 1] ?? "";
    const right = line[0] ?? "";
    return CJK.test(left) || CJK.test(right) ? `${joined}${line}` : `${joined} ${line}`;
  }, "");
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function parseBlocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push({ kind: "code", language: fence[1] ?? "", lines: body });
      continue;
    }

    if (DIVIDER.test(line)) {
      blocks.push({ kind: "divider" });
      index += 1;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2].trim() });
      index += 1;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^\s*>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", lines: quote });
      continue;
    }

    const unordered = UL_ITEM.exec(line);
    const ordered = OL_ITEM.exec(line);
    if (unordered || ordered) {
      const isOrdered = Boolean(ordered);
      const items: string[] = [];
      const pattern = isOrdered ? OL_ITEM : UL_ITEM;
      while (index < lines.length) {
        const match = pattern.exec(lines[index]);
        if (!match) break;
        items.push(match[1].trim());
        index += 1;
      }
      blocks.push({ kind: "list", ordered: isOrdered, items });
      continue;
    }

    if (line.includes("|") && index + 1 < lines.length && TABLE_SEPARATOR.test(lines[index + 1])) {
      const header = splitTableRow(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push({ kind: "table", header, rows });
      continue;
    }

    const paragraph: string[] = [line.trim()];
    index += 1;
    while (index < lines.length) {
      const next = lines[index];
      if (
        !next.trim() ||
        HEADING.test(next) ||
        DIVIDER.test(next) ||
        FENCE.test(next) ||
        UL_ITEM.test(next) ||
        OL_ITEM.test(next) ||
        /^\s*>\s?/.test(next) ||
        (next.includes("|") && index + 1 < lines.length && TABLE_SEPARATOR.test(lines[index + 1]))
      ) {
        break;
      }
      paragraph.push(next.trim());
      index += 1;
    }
    blocks.push({ kind: "paragraph", text: joinParagraph(paragraph) });
  }

  return blocks;
}

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__)/g;

/** 行内只处理代码、粗体、斜体；其余字符原样作为文本节点。 */
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // 每次调用使用独立的正则实例：renderInline 会递归处理粗体/斜体内部文本，
  // 共享带 lastIndex 的全局正则会互相覆盖游标，导致同一 token 被无限重复匹配。
  const pattern = new RegExp(INLINE.source, "g");
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));
    const token = match[0];
    const key = `${keyPrefix}-${match.index}`;
    if (token.startsWith("`") && token.endsWith("`")) {
      nodes.push(
        <code className="mdCode" key={key}>
          {token.slice(1, -1)}
        </code>,
      );
    } else if (
      (token.startsWith("**") && token.endsWith("**")) ||
      (token.startsWith("__") && token.endsWith("__"))
    ) {
      nodes.push(<strong key={key}>{renderInline(token.slice(2, -2), key)}</strong>);
    } else {
      // 单星号/下划线不解析：数学乘法、变量名保持纯文本。
      nodes.push(token);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

function renderBlock(block: Block, index: number): ReactNode {
  const key = `b-${index}`;
  switch (block.kind) {
    case "heading": {
      const level = Math.min(block.level + 2, 6);
      const Tag = `h${level}` as "h3" | "h4" | "h5" | "h6";
      return (
        <Tag className="mdHeading" key={key}>
          {renderInline(block.text, key)}
        </Tag>
      );
    }
    case "paragraph":
      return (
        <p className="mdParagraph" key={key}>
          {renderInline(block.text, key)}
        </p>
      );
    case "list": {
      const items = block.items.map((item, itemIndex) => (
        <li key={`${key}-${itemIndex}`}>{renderInline(item, `${key}-${itemIndex}`)}</li>
      ));
      return block.ordered ? (
        <ol className="mdList" key={key}>
          {items}
        </ol>
      ) : (
        <ul className="mdList" key={key}>
          {items}
        </ul>
      );
    }
    case "code":
      return (
        <pre className="mdPre" key={key}>
          <code>{block.lines.join("\n")}</code>
        </pre>
      );
    case "quote":
      return (
        <blockquote className="mdQuote" key={key}>
          {block.lines.map((line, lineIndex) =>
            line.trim() ? (
              <p key={`${key}-${lineIndex}`}>{renderInline(line, `${key}-${lineIndex}`)}</p>
            ) : null,
          )}
        </blockquote>
      );
    case "divider":
      return <hr className="mdDivider" key={key} />;
    case "table":
      return (
        <div className="mdTableWrap" key={key}>
          <table className="mdTable">
            <thead>
              <tr>
                {block.header.map((cell, cellIndex) => (
                  <th key={`${key}-h-${cellIndex}`} scope="col">
                    {renderInline(cell, `${key}-h-${cellIndex}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={`${key}-r-${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td key={`${key}-r-${rowIndex}-${cellIndex}`}>{renderInline(cell, `${key}-r-${rowIndex}-${cellIndex}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    default:
      return <Fragment key={key} />;
  }
}

export function SafeMarkdown({ text }: { text: string }) {
  if (!text) return null;
  const blocks = parseBlocks(text);
  return <div className="mdBody">{blocks.map(renderBlock)}</div>;
}
