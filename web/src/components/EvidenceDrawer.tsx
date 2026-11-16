import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "../lib/api";
import { ErrorNote, Eyebrow, KeyValue, Skeleton, Status } from "./ui";

/** Shows the stored (redacted) tool output behind a claim, with the cited excerpt highlighted. */
export function EvidenceDrawer({ runId, evidenceId, quote, onClose }: { runId: string; evidenceId: string; quote?: string; onClose: () => void }) {
  const evidence = useQuery({ queryKey: ["evidence", runId, evidenceId], queryFn: () => api.evidence(runId, evidenceId) });
  const close = useRef<HTMLButtonElement>(null);
  const mark = useRef<HTMLElement>(null);
  useEffect(() => {
    close.current?.focus();
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  useEffect(() => { mark.current?.scrollIntoView({ block: "center" }); }, [evidence.data]);

  const text = evidence.data?.result_text ?? "";
  let pretty = text;
  try { pretty = JSON.stringify(JSON.parse(text), null, 2); } catch { /* plain text output */ }
  const needle = quote ? [quote, JSON.stringify(quote).slice(1, -1)].find((q) => pretty.includes(q)) : undefined;
  const at = needle ? pretty.indexOf(needle) : -1;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onMouseDown={onClose}>
      <aside role="dialog" aria-modal="true" aria-label={`Evidence ${evidenceId}`} onMouseDown={(e) => e.stopPropagation()}
        className="flex h-full w-full max-w-[640px] flex-col border-l border-line-strong bg-panel">
        <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-5">
          <div>
            <Eyebrow>Evidence</Eyebrow>
            <h2 className="mt-2 font-mono text-lg">{evidenceId}</h2>
          </div>
          <button ref={close} type="button" onClick={onClose} className="rounded border border-line-strong px-3 py-1.5 text-sm text-muted hover:bg-raised">Close</button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {evidence.isPending && <Skeleton className="h-64" />}
          {evidence.isError && <ErrorNote error={evidence.error} />}
          {evidence.data && (
            <>
              <dl>
                <KeyValue label="Tool">{evidence.data.tool}</KeyValue>
                <KeyValue label="Arguments">{JSON.stringify(evidence.data.args)}</KeyValue>
                <KeyValue label="Agent · step">{evidence.data.agent} · {evidence.data.step_key}</KeyValue>
                <KeyValue label="Outcome" mono={false}><Status status={evidence.data.status} /></KeyValue>
                <KeyValue label="Duration">{evidence.data.duration_ms} ms · {evidence.data.attempts} attempt(s)</KeyValue>
              </dl>
              <Eyebrow className="mb-3 mt-6">
                Stored output · secrets redacted{evidence.data.truncated && " · truncated"}
              </Eyebrow>
              {quote && at < 0 && <p className="mb-3 text-sm text-amber">The cited excerpt was not found in this output.</p>}
              <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md border border-line bg-ink p-4 font-mono text-xs leading-relaxed text-muted">
                {at < 0 ? pretty : (
                  <>{pretty.slice(0, at)}<mark ref={mark} className="rounded-sm bg-violet/40 px-0.5 text-fg">{needle}</mark>{pretty.slice(at + needle!.length)}</>
                )}
              </pre>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
