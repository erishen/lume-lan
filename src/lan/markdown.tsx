/* 零依赖轻量 Markdown 渲染器:agent delta 一次流一个 token,所以必须
 * 保持对全文的单遍廉价处理(无嵌套、无树解析)。React 自动转义文本子节点,
 * 覆盖 agent/skill 实际用到的形态:``` 围栏、# 标题、-/1. 列表、> 引用、
 * 管道表格(|…| + |---| 分隔行)、行内 **粗体** *斜体* `代码`。 */
import React from "react";

export function renderInline(src: string): React.ReactNode {
  const re = /(`[^`\n]*`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)/g;
  const out: React.ReactNode[] = [];
  let last = 0;
  let n = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (m.index > last) out.push(src.slice(last, m.index));
    const tok = m[0];
    if (tok[0] === "`") out.push(<code key={n++}>{tok.slice(1, -1)}</code>);
    else if (tok[0] === "*" && tok[1] === "*")
      out.push(<strong key={n++}>{tok.slice(2, -2)}</strong>);
    else out.push(<em key={n++}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < src.length) out.push(src.slice(last));
  return out.length ? out : "";
}

const isBlockStart = (l: string): boolean =>
  /^(#{1,6}\s|\s*[-*]\s|\s*\d+\.\s|\s*>|```)/.test(l);

/* 表格分隔行:只含 | - : 与空白,且至少有一个 - */
const isTableDivider = (l: string): boolean => {
  const t = l.trim();
  return t.length > 0 && /^[\|:\-\s]+$/.test(t) && t.includes("-");
};

/* 疑似表格首行:本行含 | 且下一行是分隔行(供段落合并提前刹车用) */
const looksLikeTable = (l: string, next: string | undefined): boolean =>
  !!next && l.includes("|") && isTableDivider(next);

export function renderMarkdown(src: string): React.ReactElement {
  const lines = src.split("\n");
  const nodes: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块
    if (line.trimStart().startsWith("```")) {
      i++;
      const buf: string[] = [];
      while (i < lines.length && !lines[i].trimStart().startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      i++; // 跳过闭合 ```
      nodes.push(
        <pre key={key++}>
          <code>{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // 标题(# → h2,避免太大;最多 h5)
    const h = line.match(/^(#{1,6})\s+(.*)/);
    if (h) {
      const lvl = Math.min(h[1].length + 1, 5);
      nodes.push(
        React.createElement("h" + lvl, { key: key++ }, h[2]),
      );
      i++;
      continue;
    }

    // 无序列表
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      nodes.push(
        <ul key={key++}>
          {items.map((it, j) => (
            <li key={j}>{renderInline(it)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // 有序列表
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      nodes.push(
        <ol key={key++}>
          {items.map((it, j) => (
            <li key={j}>{renderInline(it)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // 引用
    if (/^\s*>/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*> ?/, ""));
        i++;
      }
      nodes.push(
        <blockquote key={key++}>
          {items.map((it, j) => (
            <p key={j}>{renderInline(it)}</p>
          ))}
        </blockquote>,
      );
      continue;
    }

    // 管道表格:首行含 |,下一行是 |---| 分隔行 → 收集连续 | 行
    if (looksLikeTable(line, lines[i + 1])) {
      const parseRow = (l: string): string[] => {
        let t = l.trim();
        if (t.startsWith("|")) t = t.slice(1);
        if (t.endsWith("|")) t = t.slice(0, -1);
        return t.split("|").map((c) => c.trim());
      };
      const header = parseRow(lines[i]);
      i += 2; // 跳过表头行 + 分隔行
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].includes("|")) {
        rows.push(parseRow(lines[i]));
        i++;
      }
      nodes.push(
        <div className="tbl" key={key++}>
          <table>
            <thead>
              <tr>
                {header.map((c, j) => (
                  <th key={j}>{renderInline(c)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, j) => (
                <tr key={j}>
                  {r.map((c, k) => (
                    <td key={k}>{renderInline(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // 空行
    if (line.trim() === "") {
      i++;
      continue;
    }

    // 普通段落:连续非块行合并(表格行不并入)
    const buf: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !isBlockStart(lines[i]) &&
      !looksLikeTable(lines[i], lines[i + 1])
    ) {
      buf.push(lines[i]);
      i++;
    }
    nodes.push(
      <p key={key++}>{renderInline(buf.join(" "))}</p>,
    );
  }
  return <div className="md">{nodes}</div>;
}
