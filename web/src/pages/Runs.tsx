import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Page } from "../components/AppShell";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { Button, ButtonLink, cx, Empty, ErrorNote, Panel, Skeleton, Status, Tag } from "../components/ui";
import { api } from "../lib/api";
import { clock, dayTime, shortId, truncate } from "../lib/format";
import { useProjectNames } from "./Overview";

const FILTERS = ["", "running", "completed", "failed", "cancelled"];
const PAGE = 15;
const TH = "px-5 py-3.5 font-normal";
const HEAD = "border-b border-line text-left font-mono text-[11px] uppercase tracking-[0.16em] text-faint";
const RISK: Record<string, string> = { low: "text-teal", moderate: "text-fg", high: "text-amber", critical: "text-red" };

export function Runs() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const runs = useQuery({ queryKey: ["runs", { status, offset }], queryFn: () => api.runs({ limit: PAGE, offset, status }), refetchInterval: 5000 });
  const projects = useProjectNames();
  return (
    <Page crumb="Runs & Traces" title="runs & traces." subtitle="Every execution, with its plan, tool calls and outcome.">
      <div className="mb-5 flex flex-wrap gap-2" role="group" aria-label="Filter by status">
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={status === f} onClick={() => { setStatus(f); setOffset(0); }}
            className={cx("rounded-[3px] border px-3 py-1.5 font-mono text-[11px] uppercase tracking-[0.12em]",
              status === f ? "border-violet-soft text-violet-soft" : "border-line-strong text-muted hover:text-fg")}>
            {f || "All"}
          </button>
        ))}
      </div>
      <Panel className="overflow-x-auto">
        {runs.isPending && <div className="space-y-3 p-6"><Skeleton className="h-9" /><Skeleton className="h-9" /><Skeleton className="h-9" /></div>}
        {runs.isError && <div className="p-6"><ErrorNote error={runs.error} retry={() => void runs.refetch()} /></div>}
        {runs.data?.items.length === 0 && <Empty title="no run matches." action={<ButtonLink to="/app">Start a mission</ButtonLink>}>Runs appear here as soon as a mission starts.</Empty>}
        {!!runs.data?.items.length && (
          <table className="w-full min-w-[760px] text-sm">
            <thead><tr className={HEAD}><th className={TH}>Run</th><th className={TH}>Mission</th><th className={TH}>Repository</th><th className={TH}>Started</th><th className={TH}>Status</th><th className={TH}><span className="sr-only">Links</span></th></tr></thead>
            <tbody>
              {runs.data.items.map((run) => (
                <tr key={run.id} className="border-b border-line last:border-b-0 hover:bg-raised/50">
                  <td className="px-5 py-3.5 font-mono text-[13px]"><Link to={`/app/studio/${run.id}`} className="underline underline-offset-4">{shortId(run.id)}</Link></td>
                  <td className="max-w-[340px] truncate px-5">{truncate(run.objective, 70)}</td>
                  <td className="px-5 font-mono text-[13px] text-muted">{projects.get(run.project_id)?.repo_name ?? "—"}</td>
                  <td className="px-5 font-mono text-[13px] text-muted">{dayTime(run.created_at)}</td>
                  <td className="px-5"><Status status={run.status} />{run.stop_reason && run.status === "failed" && <span className="ml-2 font-mono text-[11px] text-faint">{run.stop_reason}</span>}</td>
                  <td className="px-5 text-right"><Link to={`/app/runs/${run.id}`} className="text-violet-soft underline underline-offset-4">Trace</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      {runs.data && runs.data.total > PAGE && (
        <div className="mt-5 flex items-center justify-between text-sm text-muted">
          <span>{offset + 1}–{Math.min(offset + PAGE, runs.data.total)} of {runs.data.total}</span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
            <Button variant="secondary" disabled={offset + PAGE >= runs.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
          </div>
        </div>
      )}
    </Page>
  );
}

export function RunTrace() {
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const stepFilter = params.get("step");
  const run = useQuery({ queryKey: ["run", runId, "detail"], queryFn: () => api.run(runId) });
  const calls = useQuery({ queryKey: ["run", runId, "calls"], queryFn: () => api.toolCalls(runId) });
  const [evidence, setEvidence] = useState<string | null>(null);
  const items = (calls.data?.items ?? []).filter((c) => !stepFilter || c.step_key === stepFilter);
  return (
    <Page crumb={`Runs & Traces / Run #${shortId(runId)}`} title="tool trace."
      subtitle="Each call with the policy decision the gateway took, its duration and its result."
      actions={<ButtonLink variant="secondary" to={`/app/studio/${runId}`}>Open in Agent Studio</ButtonLink>}>
      {run.data && <p className="mb-5 font-mono text-[13px] text-muted">Mission — {run.data.objective}</p>}
      {stepFilter && (
        <p className="mb-5 flex items-center gap-3 text-sm text-muted">
          <Tag tone="violet">step · {stepFilter}</Tag>
          <button type="button" className="underline underline-offset-4" onClick={() => setParams({})}>Show all steps</button>
        </p>
      )}
      <Panel className="overflow-x-auto">
        {calls.isPending && <div className="space-y-3 p-6"><Skeleton className="h-9" /><Skeleton className="h-9" /></div>}
        {calls.isError && <div className="p-6"><ErrorNote error={calls.error} retry={() => void calls.refetch()} /></div>}
        {calls.isSuccess && items.length === 0 && <Empty title="no tool call recorded.">Calls appear here as agents work.</Empty>}
        {items.length > 0 && (
          <table className="w-full min-w-[860px] text-sm">
            <thead><tr className={HEAD}><th className={TH}>Time</th><th className={TH}>Tool</th><th className={TH}>Agent · step</th><th className={TH}>Risk</th><th className={TH}>Decision</th><th className={TH}>Result</th><th className={TH}>Duration</th><th className={TH}>Evidence</th></tr></thead>
            <tbody>
              {items.map((call) => (
                <tr key={call.id} className="border-b border-line align-top last:border-b-0">
                  <td className="px-5 py-3.5 font-mono text-[13px] text-faint">{clock(call.created_at)}</td>
                  <td className="px-5 py-3.5">
                    <span className="font-mono text-[13px]">{call.tool}</span>
                    {Object.keys(call.args).length > 0 && <span className="mt-1 block max-w-[260px] truncate font-mono text-xs text-faint" title={JSON.stringify(call.args)}>{JSON.stringify(call.args)}</span>}
                  </td>
                  <td className="px-5 py-3.5 text-muted">{call.agent} · {call.step_key}</td>
                  <td className={cx("px-5 py-3.5 capitalize", RISK[call.risk] ?? "text-muted")}>{call.risk}</td>
                  <td className="px-5 py-3.5 text-muted">{call.decision === "allow" ? "Allow · read" : "Deny"}</td>
                  <td className="px-5 py-3.5"><Status status={call.status} />{call.error_code && <span className="mt-1 block font-mono text-[11px] text-faint">{call.error_code}</span>}</td>
                  <td className="px-5 py-3.5 font-mono text-[13px] text-muted">{call.status === "rejected" ? "—" : `${call.duration_ms} ms`}</td>
                  <td className="px-5 py-3.5">
                    <button type="button" onClick={() => setEvidence(call.evidence_id)} className="font-mono text-[13px] text-violet-soft underline underline-offset-4">{call.evidence_id}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      {evidence && <EvidenceDrawer runId={runId} evidenceId={evidence} onClose={() => setEvidence(null)} />}
    </Page>
  );
}
