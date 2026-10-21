import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { getArtifact } from "../live/api";
import type { LiveComment } from "../live/types";
import type { LiveComments } from "../live/useComments";
import { Button } from "../affectui/button-clean";
import { renderMd } from "../md";
import type { Run } from "../sim/engine";
import type { AppApi } from "../app";
import type { Workflow } from "../types";
import { SendForm } from "./LiveActions";

export const relTime = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (!isFinite(s)) return "";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
export const sentLabel = (c: LiveComment) => (c.sent ? `Sent with ${c.sent.step}${c.sent.attempt ?? c.sent.wait ? ` #${c.sent.attempt ?? c.sent.wait}` : ""}` : "Pending");
export const clip = (s: string, n = 90) => (s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s);

/** Text of one artifact, reloaded when its mtime changes. */
export function useArtifactText(run: Run, name: string | null): string | null {
  const mtime = run.live?.artifacts.find((a) => a.name === name)?.mtime ?? "";
  const key = name ? `${run.slug}/${name}/${mtime}` : "";
  const [real, setReal] = useState<{ key: string; text: string } | null>(null);
  useEffect(() => {
    if (!name) return;
    let off = false;
    getArtifact(run.slug, name).then((text) => !off && setReal({ key, text })).catch((e: Error) => !off && setReal({ key, text: `Could not load ${name}: ${e.message}` }));
    return () => { off = true; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return real?.key === key ? real.text : null;
}

const BLK = "p,li,h1,h2,h3,h4,h5,h6,pre,td,th,blockquote";

/** Wrap the first occurrence of each quote (whitespace-insensitive, across element boundaries) in <mark data-cid>. Returns the ids found. */
function applyMarks(root: HTMLElement, items: { id: string; quote: string; cls: string }[]): Set<string> {
  root.querySelectorAll("mark[data-cid]").forEach((m) => m.replaceWith(...Array.from(m.childNodes)));
  const found = new Set<string>();
  for (const it of items) {
    const map: ([Text, number] | null)[] = [];
    let s = "";
    let prev: Element | null = null;
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = tw.nextNode() as Text | null; n; n = tw.nextNode() as Text | null) {
      const blk = n.parentElement?.closest(BLK) ?? null;
      if (blk !== prev && s && !s.endsWith(" ")) { s += " "; map.push(null); }
      prev = blk;
      for (let k = 0; k < n.data.length; k++) {
        if (/\s/.test(n.data[k])) { if (!s || s.endsWith(" ")) continue; s += " "; } else s += n.data[k];
        map.push([n, k]);
      }
    }
    const q = it.quote.replace(/\s+/g, " ").trim();
    const at = q ? s.indexOf(q) : -1;
    if (at < 0) continue;
    const segs = new Map<Text, [number, number]>();
    for (let k = at; k < at + q.length; k++) {
      const e = map[k]; if (!e) continue;
      const g = segs.get(e[0]);
      if (g) g[1] = e[1] + 1; else segs.set(e[0], [e[1], e[1] + 1]);
    }
    segs.forEach(([a, b], node) => {
      let t = node;
      if (a > 0) t = node.splitText(a);
      if (b - a < t.length) t.splitText(b - a);
      const m = document.createElement("mark");
      m.className = `dmark ${it.cls}`; m.dataset.cid = it.id;
      t.before(m); m.appendChild(t);
    });
    found.add(it.id);
  }
  return found;
}

const lineOf = (r: Range): number | null => {
  const el = r.startContainer.nodeType === 1 ? (r.startContainer as Element) : r.startContainer.parentElement;
  const b = el?.closest("[data-line]") as HTMLElement | null;
  if (!b) return null;
  let line = +(b.dataset.line ?? 0);
  if (b.tagName === "PRE") {
    const pre = document.createRange();
    pre.selectNodeContents(b); pre.setEnd(r.startContainer, r.startOffset);
    line += (pre.toString().match(/\n/g) ?? []).length;
  }
  return line || null;
};

const Body = memo(function Body({ text, md }: { text: string; md: boolean }) {
  return md ? <>{renderMd(text)}</> : <pre data-line={1}>{text}</pre>;
});

interface Draft { quote: string | null; line: number | null; top: number }

function Composer({ draft, onSave, onCancel }: { draft: Draft; onSave: (text: string) => Promise<void>; onCancel: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const save = () => {
    if (!text.trim() || busy) return;
    setBusy(true); setErr("");
    onSave(text.trim()).catch((e: Error) => { setBusy(false); setErr(e.message); });
  };
  return (
    <>
      <div className="dc__q">{draft.quote ? clip(draft.quote, 160) : "Whole document"}</div>
      <textarea autoFocus rows={3} value={text} aria-label="Comment" placeholder={draft.quote ? "Comment on this passage" : "Comment on this document"} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.stopPropagation(); onCancel(); }
          else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
        }} />
      {err && <p className="ferr" role="alert">{err}</p>}
      <div className="cmt__a"><span className="cmt__n">{navigator.platform?.includes("Mac") ? "⌘" : "Ctrl"}+Enter to save</span><Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button><Button size="sm" variant="primary" disabled={!text.trim() || busy} onClick={save}>Comment</Button></div>
    </>
  );
}

const useWide = () => {
  const [w, setW] = useState(() => window.innerWidth);
  useEffect(() => { const h = () => setW(window.innerWidth); window.addEventListener("resize", h); return () => window.removeEventListener("resize", h); }, []);
  return w;
};

/** A rendered artifact with selectable, commentable passages and a comment margin (cards below the text when narrow). */
export function DocView({ file, text, cm, wide }: { file: string; text: string; cm: LiveComments; wide: boolean }) {
  const md = /\.(md|markdown)$/i.test(file);
  const vw = useWide();
  const margin = wide && vw >= 900;
  const docRef = useRef<HTMLDivElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const cardEls = useRef<Record<string, HTMLElement | null>>({});
  const [draft, setDraft] = useState<Draft | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [sel, setSel] = useState<{ x: number; y: number; quote: string; line: number | null; top: number } | null>(null);
  const [anchors, setAnchors] = useState<Record<string, number | null>>({});
  const [pos, setPos] = useState<{ tops: Record<string, number>; h: number }>({ tops: {}, h: 0 });
  const mine = useMemo(() => cm.list.filter((c) => c.file === file), [cm.list, file]);
  const sig = mine.map((c) => `${c.id}:${c.sent ? 1 : 0}:${c.quote ?? ""}`).join("|") + (draft?.quote ? `|draft:${draft.quote}` : "");

  useLayoutEffect(() => {
    const root = docRef.current, grid = gridRef.current;
    if (!root || !grid) return;
    const items = mine.filter((c) => c.quote).map((c) => ({ id: c.id, quote: c.quote!, cls: c.sent ? "dmark--sent" : "dmark--pending" }));
    if (draft?.quote) items.push({ id: "draft", quote: draft.quote, cls: "dmark--draft" });
    const found = applyMarks(root, items);
    const gt = grid.getBoundingClientRect().top;
    const next: Record<string, number | null> = {};
    for (const it of items) {
      const m = root.querySelector(`mark[data-cid="${it.id}"]`);
      next[it.id] = found.has(it.id) && m ? m.getBoundingClientRect().top - gt : null;
    }
    setAnchors((a) => (JSON.stringify(a) === JSON.stringify(next) ? a : next));
  }, [text, sig, margin]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    docRef.current?.querySelectorAll("mark[data-cid]").forEach((m) => m.classList.toggle("is-focus", (m as HTMLElement).dataset.cid === focus));
  }, [focus, sig, text]);

  useEffect(() => {
    const h = () => { const s = window.getSelection(); if (!s || s.isCollapsed) setSel(null); };
    document.addEventListener("selectionchange", h);
    return () => document.removeEventListener("selectionchange", h);
  }, []);

  // Card order: unanchored first, then by position in the text. The draft card sits at its selection.
  const cards = useMemo(() => {
    const rows: { id: string; want: number; c?: LiveComment }[] = mine.map((c) => ({ id: c.id, want: c.quote && anchors[c.id] != null ? anchors[c.id]! : -1, c }));
    if (draft) rows.push({ id: "draft", want: draft.quote ? (anchors.draft ?? draft.top) : -1 });
    return rows.sort((a, b) => a.want - b.want);
  }, [mine, anchors, draft]);

  useLayoutEffect(() => {
    if (!margin) return;
    let y = 0;
    const tops: Record<string, number> = {};
    for (const r of cards) {
      const top = Math.max(r.want, y);
      tops[r.id] = top;
      y = top + (cardEls.current[r.id]?.offsetHeight ?? 90) + 8;
    }
    setPos((p) => (JSON.stringify(p.tops) === JSON.stringify(tops) && p.h === y ? p : { tops, h: y }));
  });

  const onSel = () => {
    const s = window.getSelection(), root = docRef.current, grid = gridRef.current;
    if (!s || s.isCollapsed || !s.rangeCount || !root || !grid) { setSel(null); return; }
    const r = s.getRangeAt(0);
    const quote = s.toString().trim();
    if (!quote || !root.contains(r.commonAncestorContainer)) { setSel(null); return; }
    const rect = r.getBoundingClientRect();
    setSel({ x: rect.left + rect.width / 2, y: rect.top, quote, line: lineOf(r), top: rect.top - grid.getBoundingClientRect().top });
  };
  const hit = (e: React.MouseEvent) => (e.target as HTMLElement).closest?.("mark[data-cid]") as HTMLElement | null;
  const focusCard = (id: string) => { setFocus(id); cardEls.current[id]?.scrollIntoView({ block: "nearest" }); };

  const save = (text: string) => cm.add({ text, file, ...(draft?.quote ? { quote: draft.quote } : {}), ...(draft?.line ? { line: draft.line } : {}) }).then(() => { setDraft(null); });
  const cardClass = (id: string) => `dc${focus === id ? " is-focus" : ""}`;
  const missing = (c: LiveComment) => !!c.quote && anchors[c.id] === null;
  const cardStyle = (id: string): React.CSSProperties | undefined => (margin ? { top: pos.tops[id] ?? 0 } : undefined);

  return (
    <div className={`dv${wide ? " dv--wide" : ""}`}>
      <div className="dv__bar">
        <Button size="sm" variant="secondary" onClick={() => { setDraft({ quote: null, line: null, top: 0 }); setFocus("draft"); }}>Comment on this document</Button>
        <span className="muted">{mine.filter((c) => !c.sent).length} pending · select text to comment on a passage</span>
      </div>
      <div className={`dv__grid${margin ? "" : " dv__grid--narrow"}`} ref={gridRef}>
        <div className="dv__doc" ref={docRef} onMouseUp={onSel} onKeyUp={onSel} onMouseOver={(e) => { const m = hit(e); if (m && m.dataset.cid !== focus) setFocus(m.dataset.cid!); }}
          onClick={(e) => { const m = hit(e); if (m) focusCard(m.dataset.cid!); }} aria-label={file}>
          <Body text={text} md={md} />
        </div>
        <div className="dv__margin" style={margin ? { height: pos.h } : undefined} aria-label="Comments">
          {cards.map((r) => (
            <div key={r.id} className={cardClass(r.id)} style={cardStyle(r.id)} ref={(el) => { cardEls.current[r.id] = el; }}
              onMouseEnter={() => setFocus(r.id)} onClick={() => setFocus(r.id)}>
              {r.c ? <>
                <div className="dc__q">{r.c.quote ? clip(r.c.quote, 160) : "Whole document"}</div>
                {missing(r.c) && <div className="dc__miss">Passage changed or no longer found</div>}
                <p className="dc__t">{r.c.text}</p>
                <div className="dc__f">
                  <span>{relTime(r.c.at)}</span>
                  <span className={`dc__chip${r.c.sent ? " dc__chip--sent" : ""}`}>{sentLabel(r.c)}</span>
                  {!r.c.sent && <button className="dc__del" onClick={(e) => { e.stopPropagation(); cm.remove(r.c!.id); }}>Delete</button>}
                </div>
              </> : draft && <Composer draft={draft} onSave={save} onCancel={() => { setDraft(null); setFocus(null); }} />}
            </div>
          ))}
        </div>
      </div>
      {sel && !draft && (
        <button className="dv__float" style={{ left: Math.min(Math.max(sel.x - 44, 8), window.innerWidth - 100), top: Math.max(sel.y - 38, 8) }}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => { setDraft({ quote: sel.quote.slice(0, 1000), line: sel.line, top: sel.top }); setFocus("draft"); setSel(null); window.getSelection()?.removeAllRanges(); }}>Comment</button>
      )}
    </div>
  );
}

/** Wide review panel for one run's artifacts. */
export function DocOverlay({ app, run, wf, cm, file, setFile, onClose, sent, setSent }: {
  app: AppApi; run: Run; wf: Workflow; cm: LiveComments; file: string; setFile: (f: string) => void; onClose: () => void; sent: string | null; setSent: (k: string | null) => void;
}) {
  const text = useArtifactText(run, file);
  const [send, setSend] = useState(false);
  const arts = run.live!.artifacts;
  const attn = run.live!.attention;
  const n = cm.pending.length;
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape" && !e.defaultPrevented) { if (send) setSend(false); else onClose(); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [send, onClose]);
  return createPortal(
    <div className="ov" role="dialog" aria-modal="true" aria-label={`Review ${file}`}>
      <div className="ov__h">
        <span className="ov__n">{file}</span>
        <div className="ov__tabs">
          {arts.map((a) => {
            const c = cm.pending.filter((x) => x.file === a.name).length;
            return <button key={a.name} className="tchip" data-on={a.name === file || undefined} onClick={() => setFile(a.name)}>{a.name}{c > 0 && <em>{c}</em>}</button>;
          })}
        </div>
        <span className="rd__sp" />
        <span className="muted">{n} pending</span>
        {attn && <Button size="sm" variant="primary" onClick={() => setSend((v) => !v)}>Send feedback{n ? ` (${n})` : ""}</Button>}
        <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close review">Close</Button>
      </div>
      {send && attn && (
        <div className="ov__send"><SendForm app={app} run={run} wf={wf} cm={cm} sent={sent} setSent={setSent} onDone={() => { setSend(false); onClose(); }} onCancel={() => setSend(false)} onOpen={(f) => { setFile(f); setSend(false); }} /></div>
      )}
      <div className="ov__body">
        {text === null ? <p className="muted">Loading…</p> : <DocView key={file} file={file} text={text} cm={cm} wide />}
      </div>
    </div>,
    document.body,
  );
}
