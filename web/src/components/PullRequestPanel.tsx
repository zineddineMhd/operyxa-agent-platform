import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type PullRequestProposal } from "../lib/api";
import { Button, ErrorNote, Eyebrow, Field, INPUT, Panel, Status, Tag } from "./ui";

// F-12. Two explicit human steps: prepare (snapshot + diff), then confirm the exact content.
function Proposal({ proposal, runId }: { proposal: PullRequestProposal; runId: string }) {
  const client = useQueryClient();
  const [acknowledged, setAcknowledged] = useState(false);
  const refresh = () => client.invalidateQueries({ queryKey: ["run", runId, "pull-requests"] });
  const confirm = useMutation({ mutationFn: () => api.confirmPullRequest(proposal.id, proposal.content_hash), onSettled: refresh });
  const cancel = useMutation({ mutationFn: () => api.cancelPullRequest(proposal.id), onSettled: refresh });
  return (
    <div className="mt-4 rounded-[5px] border border-line p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">{proposal.title}</p>
        {proposal.status === "awaiting_confirmation" ? <Tag tone="amber">Awaiting confirmation</Tag>
          : <Status status={proposal.status === "created" ? "completed" : proposal.status === "failed" ? "failed" : "cancelled"}
              label={proposal.status === "created" ? "Created" : undefined} />}
      </div>
      <p className="mt-1.5 font-mono text-xs text-muted">{proposal.branch} · from {proposal.base_sha.slice(0, 7)} · draft</p>
      <ul className="mt-3 space-y-1 text-[13px]">
        {proposal.files.map((f) => <li key={f.path}><code className="font-mono">{f.path}</code> <span className="text-muted">— {f.rationale}</span></li>)}
      </ul>
      <details className="mt-3">
        <summary className="cursor-pointer text-sm text-violet-soft">Review the diff</summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded bg-ink p-3 font-mono text-[11px] leading-relaxed">{proposal.diff}</pre>
      </details>
      {proposal.pr_url && <a href={proposal.pr_url} target="_blank" rel="noreferrer" className="mt-3 inline-block text-sm text-violet-soft underline underline-offset-4">Open pull request #{proposal.pr_number} →</a>}
      {proposal.error && <p className="mt-3 text-sm text-red">{proposal.error}</p>}
      {proposal.status === "awaiting_confirmation" && (
        <>
          <label className="mt-4 flex cursor-pointer items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-0.5" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
            <span>I reviewed this diff. Create a new branch and a draft pull request on GitHub.</span>
          </label>
          <p className="mt-1 font-mono text-[10px] text-faint">content {proposal.content_hash.slice(0, 16)}…</p>
          {(confirm.isError || cancel.isError) && <div className="mt-3"><ErrorNote error={confirm.error ?? cancel.error} /></div>}
          <div className="mt-3 flex gap-2">
            <Button disabled={!acknowledged || confirm.isPending} onClick={() => confirm.mutate()}>{confirm.isPending ? "Creating…" : "Confirm and create"}</Button>
            <Button variant="secondary" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Discard</Button>
          </div>
        </>
      )}
    </div>
  );
}

export function PullRequestPanel({ runId }: { runId: string }) {
  const client = useQueryClient();
  const changes = useQuery({ queryKey: ["run", runId, "changes"], queryFn: () => api.changes(runId) });
  const proposals = useQuery({ queryKey: ["run", runId, "pull-requests"], queryFn: () => api.pullRequests(runId) });
  const [title, setTitle] = useState("");
  const [includeReport, setIncludeReport] = useState(false);
  const prepare = useMutation({
    mutationFn: () => api.preparePullRequest(runId, { title: title.trim() || undefined, include_report: includeReport }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["run", runId, "pull-requests"] }),
  });
  const list = changes.data?.changes ?? [];
  const pending = proposals.data?.some((p) => p.status === "awaiting_confirmation");
  if (!changes.data && !proposals.data) return null;
  return (
    <Panel className="p-6">
      <Eyebrow>Fix proposals</Eyebrow>
      {list.length === 0 ? (
        <p className="mt-3 text-sm leading-relaxed text-muted">No fix was proposed. Start a run with “Propose fixes” to get reviewable changes.</p>
      ) : (
        <ul className="mt-3">
          {list.map((c) => (
            <li key={c.evidence_id} className="border-b border-line py-2.5 last:border-b-0">
              <p className="font-mono text-[13px]">{c.path} <span className="text-faint">· {c.change_type}</span></p>
              <p className="mt-1 text-xs text-muted">{c.rationale}</p>
            </li>
          ))}
        </ul>
      )}
      {!pending && (list.length > 0 || includeReport) && (
        <div className="mt-4 space-y-3">
          <Field label="Pull request title"><input className={INPUT} maxLength={200} placeholder="Operyxa: proposed fixes" value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        </div>
      )}
      {!pending && (
        <label className="mt-3 flex cursor-pointer items-center gap-2 text-[13px]">
          <input type="checkbox" checked={includeReport} onChange={(e) => setIncludeReport(e.target.checked)} />
          Add the report as docs/operyxa/report-….md
        </label>
      )}
      {prepare.isError && <div className="mt-3"><ErrorNote error={prepare.error} /></div>}
      {!pending && (list.length > 0 || includeReport) && (
        <Button className="mt-4 w-full" variant="secondary" disabled={prepare.isPending} onClick={() => prepare.mutate()}>
          {prepare.isPending ? "Preparing…" : "Prepare pull request"}
        </Button>
      )}
      {proposals.data?.slice().reverse().map((p) => <Proposal key={p.id} proposal={p} runId={runId} />)}
      <p className="mt-4 text-xs leading-relaxed text-faint">Agents never write. The server opens a draft pull request only after you confirm, with the project's write token.</p>
    </Panel>
  );
}
