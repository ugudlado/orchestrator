import { useCallback, useEffect, useRef, useState } from "react";
import { addLiveComment, deleteLiveComment, getComments } from "./api";
import type { LiveComment } from "./types";

export interface LiveComments {
  list: LiveComment[];
  pending: LiveComment[];
  refresh: () => Promise<void>;
  add: (b: { text: string; file?: string; quote?: string; line?: number }) => Promise<void>;
  remove: (id: string) => Promise<void>;
}

/** Comments of one live run: fetched on open, after each change, and with the 3 s poll. */
export function useLiveComments(slug: string, enabled: boolean): LiveComments {
  const [list, setList] = useState<LiveComment[]>([]);
  const cur = useRef(slug);
  cur.current = slug;
  const refresh = useCallback(() => getComments(slug).then((l) => { if (cur.current === slug) setList(l); }).catch(() => {}), [slug]);
  useEffect(() => {
    if (!enabled) return;
    setList([]);
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [enabled, refresh]);
  const add: LiveComments["add"] = (b) => addLiveComment(slug, b).then(() => refresh());
  const remove: LiveComments["remove"] = (id) => deleteLiveComment(slug, id).then(() => refresh());
  return { list, pending: list.filter((c) => !c.sent), refresh, add, remove };
}
