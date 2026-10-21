import { Fragment, type ReactNode } from "react";

/** Small markdown renderer that builds React elements (never raw HTML), so agent output can't inject markup. Blocks carry data-line (1-based source line). */

type Block =
  | { t: "h"; n: number; text: string; line: number }
  | { t: "p"; text: string; line: number }
  | { t: "code"; text: string; line: number }
  | { t: "list"; ordered: boolean; items: { text: string; line: number; kids: Block[] }[]; line: number }
  | { t: "quote"; text: string; line: number }
  | { t: "table"; head: string[]; rows: string[][]; line: number }
  | { t: "hr"; line: number };

const LI = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const FENCE = /^\s*(```|~~~)/;
const HEAD = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR = /^\s*([-*_])(\s*\1){2,}\s*$/;
const TSEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const cells = (l: string) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));

function nest(flat: { indent: number; ordered: boolean; text: string; line: number }[], i: number): [Block, number] {
  const base = flat[i].indent;
  const items: { text: string; line: number; kids: Block[] }[] = [];
  const first = flat[i];
  while (i < flat.length && flat[i].indent >= base) {
    if (flat[i].indent > base && items.length) { const [sub, ni] = nest(flat, i); items[items.length - 1].kids.push(sub); i = ni; continue; }
    items.push({ text: flat[i].text, line: flat[i].line, kids: [] }); i++;
  }
  return [{ t: "list", ordered: first.ordered, items, line: first.line }, i];
}

export function parseMd(src: string): Block[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: Block[] = [];
  const starts = (l: string, nx?: string) => FENCE.test(l) || HEAD.test(l) || HR.test(l) || /^\s*>/.test(l) || LI.test(l) || (l.includes("|") && !!nx && TSEP.test(nx));
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (FENCE.test(l)) {
      const mark = l.trim().slice(0, 3); const body: string[] = []; const line = i + 2; i++;
      while (i < lines.length && !lines[i].trim().startsWith(mark)) body.push(lines[i++]);
      i++; out.push({ t: "code", text: body.join("\n"), line }); continue;
    }
    let m = l.match(HEAD);
    if (m) { out.push({ t: "h", n: m[1].length, text: m[2], line: i + 1 }); i++; continue; }
    if (HR.test(l)) { out.push({ t: "hr", line: i + 1 }); i++; continue; }
    if (l.includes("|") && i + 1 < lines.length && TSEP.test(lines[i + 1])) {
      const head = cells(l); const rows: string[][] = []; const line = i + 1; i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) rows.push(cells(lines[i++]));
      out.push({ t: "table", head, rows, line }); continue;
    }
    if (/^\s*>/.test(l)) {
      const line = i + 1; const body: string[] = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push({ t: "quote", text: body.join(" "), line }); continue;
    }
    if ((m = l.match(LI))) {
      const flat: { indent: number; ordered: boolean; text: string; line: number }[] = [];
      while (i < lines.length) {
        const x = lines[i].match(LI);
        if (x) { flat.push({ indent: x[1].replace(/\t/g, "  ").length, ordered: /\d/.test(x[2]), text: x[3], line: i + 1 }); i++; }
        else if (lines[i].trim() && /^\s+/.test(lines[i]) && flat.length) { flat[flat.length - 1].text += " " + lines[i].trim(); i++; }
        else if (!lines[i].trim() && i + 1 < lines.length && LI.test(lines[i + 1]) && flat.length) i++;
        else break;
      }
      let k = 0;
      while (k < flat.length) { const [b, nk] = nest(flat, k); out.push(b); k = nk; }
      continue;
    }
    const line = i + 1; const body: string[] = [];
    while (i < lines.length && lines[i].trim() && !(body.length && starts(lines[i], lines[i + 1]))) body.push(lines[i++].trim());
    out.push({ t: "p", text: body.join(" "), line });
  }
  return out;
}

const safeHref = (h: string) => (/^(https?:|mailto:|#|\/|\.)/i.test(h) ? h : null);

export function inline(t: string): ReactNode[] {
  return t.split(/(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|\[[^\]]+\]\([^)\s]+\))/g).map((p, i) => {
    if (!p) return null;
    if (p.startsWith("`")) return <code key={i}>{p.slice(1, -1)}</code>;
    if (p.startsWith("**") || p.startsWith("__")) return <strong key={i}>{inline(p.slice(2, -2))}</strong>;
    if (p.startsWith("*") && p.length > 2) return <em key={i}>{inline(p.slice(1, -1))}</em>;
    const a = p.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
    if (a) { const h = safeHref(a[2]); return h ? <a key={i} href={h} target="_blank" rel="noreferrer noopener">{inline(a[1])}</a> : <Fragment key={i}>{a[1]}</Fragment>; }
    return <Fragment key={i}>{p}</Fragment>;
  });
}

function view(b: Block, key: number): ReactNode {
  switch (b.t) {
    case "h": { const H = (`h${b.n}`) as "h1"; return <H key={key} data-line={b.line}>{inline(b.text)}</H>; }
    case "p": return <p key={key} data-line={b.line}>{inline(b.text)}</p>;
    case "quote": return <blockquote key={key} data-line={b.line}>{inline(b.text)}</blockquote>;
    case "hr": return <hr key={key} />;
    case "code": return <pre key={key} data-line={b.line}><code>{b.text}</code></pre>;
    case "list": {
      const L = b.ordered ? "ol" : "ul";
      return <L key={key}>{b.items.map((it, j) => <li key={j} data-line={it.line}>{inline(it.text)}{it.kids.map(view)}</li>)}</L>;
    }
    case "table":
      return (
        <div key={key} className="md-tbl"><table><thead><tr>{b.head.map((c, j) => <th key={j} data-line={b.line}>{inline(c)}</th>)}</tr></thead>
          <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k} data-line={b.line + 2 + j}>{inline(c)}</td>)}</tr>)}</tbody></table></div>
      );
  }
}

export const renderMd = (src: string): ReactNode[] => parseMd(src).map(view);
