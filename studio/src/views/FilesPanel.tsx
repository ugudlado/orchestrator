import { useEffect, useState } from "react";
import { getArtifact } from "../live/api";
import { Button } from "../affectui/button-clean";
import { contentFor, filesFor, type Comment } from "../files";
import { roleById } from "../roles";
import type { Run } from "../sim/engine";
import type { Workflow } from "../types";

const inline = (t: string) =>
  t.split(/(`[^`]+`|\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("`") ? <code key={i}>{p.slice(1, -1)}</code> : p.startsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p));

export function FilesPanel({ run, wf, openFile, setOpenFile, comments, addComment }: {
  run: Run; wf: Workflow; openFile: string | null; setOpenFile: (f: string | null) => void; comments: Comment[]; addComment: (c: Omit<Comment, "id" | "at" | "runId">) => void;
}) {
  const files = filesFor(run, wf);
  const [sel, setSel] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const mine = comments.filter((c) => c.runId === run.id);
  const info = files.find((f) => f.name === openFile) ?? (run.live && openFile ? { name: openFile, owner: "", writers: [], version: 1, lastStep: "", lastN: 0 } : undefined);
  const [real, setReal] = useState<{ key: string; text: string } | null>(null);
  const liveKey = run.live && info ? `${run.slug}/${info.name}/${run.live.artifacts.find((a) => a.name === info.name)?.mtime ?? ""}` : "";
  useEffect(() => {
    if (!run.live || !info) return;
    let off = false;
    const key = liveKey;
    getArtifact(run.slug, info.name).then((text) => !off && setReal({ key, text })).catch((e: Error) => !off && setReal({ key, text: `Could not load ${info.name}: ${e.message}` }));
    return () => { off = true; };
  }, [liveKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const ownerLabel = (owner: string) => roleById(wf.steps.find((s) => s.id === owner)?.role)?.name ?? owner;

  if (!info) {
    return (
      <div className="files">
        <div className="files__cap">Saved in run store · <code>runs/{run.slug}/{run.live ? "artifacts/" : ""}</code></div>
        {files.length === 0 && <p className="muted">No artifacts yet. Files appear as steps write them.</p>}
        <div className="files__list">
          {files.map((f) => (
            <button key={f.name} className="frow" onClick={() => { setOpenFile(f.name); setSel(null); }}>
              <span className="frow__n">{f.name}</span>
              <span className="frow__s">{f.lastStep ? `written by ${f.lastStep}` : ""}</span>
              <span className="frow__v">v{f.version}</span>
              {mine.some((c) => c.file === f.name) && <span className="frow__c">{mine.filter((c) => c.file === f.name).length} comment{mine.filter((c) => c.file === f.name).length > 1 ? "s" : ""}</span>}
            </button>
          ))}
        </div>
        <CommentList mine={mine} wf={wf} open={(f) => setOpenFile(f)} />
      </div>
    );
  }

  const text = run.live ? (real?.key === liveKey ? real.text : "Loading…") : contentFor(info.name, run, info.version);
  const lines = text.replace(/\n$/, "").split("\n");
  const md = info.name.endsWith(".md");
  let fence = false;
  const owner = ownerLabel(info.owner);
  const submit = () => {
    if (!draft.trim() || sel === null) return;
    addComment({ file: info.name, line: sel, text: draft.trim(), owner: info.owner });
    setDraft(""); setSel(null);
  };

  return (
    <div className="files">
      <div className="fview__h">
        <Button size="sm" variant="ghost" onClick={() => { setOpenFile(null); setSel(null); }}>← Files</Button>
        <span className="fview__n">{info.name}</span>
        <span className="frow__v">v{info.version}</span>
        {info.lastStep && <span className="fview__w">written by {info.lastStep}</span>}
      </div>
      <div className="files__cap">Saved in run store · <code>runs/{run.slug}/{run.live ? "artifacts/" : ""}{info.name}</code>. Click a line to comment for the agent.</div>
      <div className="fview" role="table" aria-label={info.name}>
        {lines.map((l, i) => {
          const n = i + 1;
          let cls = "fl";
          let body: React.ReactNode = l || " ";
          if (md) {
            if (l.startsWith("```")) { fence = !fence; cls += " fl--code"; }
            else if (fence) cls += " fl--code";
            else if (/^#{1,3}\s/.test(l)) { const lv = l.match(/^#+/)![0].length; cls += ` fl--h${lv}`; body = inline(l.replace(/^#+\s/, "")); }
            else if (/^[-*]\s/.test(l)) { cls += " fl--li"; body = <>{"• "}{inline(l.replace(/^[-*]\s/, ""))}</>; }
            else if (l) body = inline(l);
          } else cls += " fl--mono";
          const cs = mine.filter((c) => c.file === info.name && c.line === n);
          return (
            <div key={i}>
              <div className={cls} data-sel={sel === n || undefined} onClick={() => { setSel(n); setDraft(""); }}>
                <button className="fl__g" aria-label={`Comment on line ${n}`} onClick={(e) => { e.stopPropagation(); setSel(n); setDraft(""); }}><span>{n}</span><i>+</i></button>
                <span className="fl__t">{body}</span>
                {cs.length > 0 && <span className="fl__dot" aria-hidden>{cs.length}</span>}
              </div>
              {cs.map((c) => (
                <div className="cmt" key={c.id}>
                  <div className="cmt__h"><strong>Comment for {ownerLabel(c.owner)}</strong><span>line {c.line} · {c.at}</span></div>
                  <p>{c.text}</p>
                  <div className="cmt__n">Will be passed to <code>{c.owner}</code> on rework</div>
                </div>
              ))}
              {sel === n && (
                <div className="cmt cmt--new">
                  <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={`Comment for ${owner} on line ${n}`} rows={2} aria-label="Comment" />
                  <div className="cmt__a"><span className="cmt__n">Goes to <code>{info.owner}</code> on its next run</span><Button size="sm" variant="ghost" onClick={() => setSel(null)}>Cancel</Button><Button size="sm" variant="primary" disabled={!draft.trim()} onClick={submit}>Comment</Button></div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <CommentList mine={mine} wf={wf} open={(f) => setOpenFile(f)} />
    </div>
  );
}

function CommentList({ mine, wf, open }: { mine: Comment[]; wf: Workflow; open: (f: string) => void }) {
  if (!mine.length) return null;
  return (
    <div className="clist">
      <div className="clist__h">Comments for agents ({mine.length})</div>
      {mine.map((c) => (
        <button key={c.id} className="clist__i" onClick={() => open(c.file)}>
          <span className="clist__w">{c.file}{c.line ? `:${c.line}` : ""} <em>{c.at}</em></span>
          <span className="clist__t">{c.text}</span>
          <span className="cmt__n">Will be passed to {c.owner} on rework{roleById(wf.steps.find((s) => s.id === c.owner)?.role) ? ` (${roleById(wf.steps.find((s) => s.id === c.owner)?.role)!.name})` : ""}</span>
        </button>
      ))}
    </div>
  );
}
