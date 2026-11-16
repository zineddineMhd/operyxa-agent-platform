import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { Page } from "../components/AppShell";
import { MissionForm } from "../components/MissionForm";
import { PlanGraph, type GraphNode } from "../components/PlanGraph";
import { Button, ButtonLink, cx, Empty, ErrorNote, Eyebrow, KeyValue, Panel, Skeleton, Status, Tag, TextLink } from "../components/ui";
import { api, type RunDetail, type RunEvent, type Step, type ToolCall } from "../lib/api";
import { clock, duration, elapsedSeconds, money, shortId } from "../lib/format";
import { streamRunEvents } from "../lib/sse";
import { useProjectNames } from "./Overview";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const STEP_LABEL: Record<string, string> = { pending: "Queued", running: "Running", completed: "Completed", failed: "Failed", skipped: "Skipped" };
const STOP_REASONS: Record<string, string> = {
  "budget_exceeded:tool_calls": "The tool-call budget was reached.",
  "budget_exceeded:llm_calls": "The agent-step budget was reached.",
  "budget_exceeded:time": "The run-time budget was reached.",
  "budget_exceeded:cost": "The cost budget was reached.",
  plan_invalid: "The planner could not produce a valid plan.",
  step_failed: "At least one step did not complete.",
  repository_unavailable: "The repository could not be read.",
  cancelled: "The run was cancelled.",
  internal_error: "An internal error stopped the run.",
};
const title = (text: string) => text.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function describe(event: RunEvent): { text: string; detail: string } | null {
  const d = event.data as Record<string, any>;
  switch (event.type) {
    case "run.created": return { text: "Run created", detail: "queued" };
    case "run.started": return { text: "Commit pinned", detail: String(d.commit_sha ?? "").slice(0, 10) };
    case "run.resumed": return { text: "Run resumed from checkpoint", detail: "run.resumed" };
    case "plan.validated": return { text: "Plan validated", detail: `${d.plan?.steps?.length ?? 0} tasks` };
    case "plan.rejected": return { text: "Plan rejected by the server", detail: String(d.errors?.[0] ?? "") };
    case "agent.started": return { text: `${title(d.agent)} agent started`, detail: `agent.started · ${d.step}` };
    case "agent.completed": return { text: `${title(d.agent)} agent finished`, detail: `${d.findings} findings · ${d.step}` };
    case "agent.failed": return { text: `${title(d.agent)} agent failed`, detail: String(d.error ?? "") };
    case "step.skipped": return { text: `Step ${d.step} skipped`, detail: `blocked by ${d.blocked_by?.[0]}` };
    case "tool.requested": return null; // the outcome line carries the same information
    case "tool.completed": return { text: `${d.tool} returned`, detail: `tool.completed · ${d.evidence_id} · ${d.duration_ms} ms` };
    case "tool.failed": return { text: `${d.tool} failed`, detail: `tool.failed · ${d.error_code}` };
    case "tool.rejected": return { text: `${d.tool} rejected by policy`, detail: `tool.rejected · ${d.code}` };
    case "findings.verified": return { text: "Evidence verified", detail: `${d.verified} verified · ${d.unverified} unverified` };
    case "report.ready": return { text: d.partial ? "Partial report saved" : "Report saved", detail: `${d.verified_findings} verified findings` };
    case "run.completed": return { text: "Run completed", detail: "run.completed" };
    case "run.failed": return { text: "Run stopped", detail: String(d.stop_reason ?? "run.failed") };
    case "run.cancelled": return { text: "Run cancelled", detail: "run.cancelled" };
    default: return { text: event.type, detail: "" };
  }
}

function useRunEvents(runId: string, onEnd: () => void) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [live, setLive] = useState(false);
  const end = useRef(onEnd);
  end.current = onEnd;
  useEffect(() => {
    setEvents([]);
    let stop = () => {};
    let cancelled = false;
    const add = (event: RunEvent) => setEvents((list) => (list.some((e) => e.id === event.id) ? list : [...list, event]));
    // History carries server timestamps; the stream then continues after the last known event.
    void api.eventHistory(runId).catch(() => [] as RunEvent[]).then((history) => {
      if (cancelled) return;
      setEvents(history);
      stop = streamRunEvents(runId, { onEvent: add, onEnd: () => { setLive(false); end.current(); }, onState: setLive },
        history.at(-1)?.id ?? 0);
    });
    return () => { cancelled = true; stop(); };
  }, [runId]);
  return { events, live };
}

function buildNodes(run: RunDetail, steps: Step[], events: RunEvent[]): GraphNode[] {
  const has = (type: string) => events.some((e) => e.type === type);
  const done = TERMINAL.has(run.status);
  const planStatus = run.plan ? "completed" : run.stop_reason === "plan_invalid" ? "failed" : done ? "skipped" : run.status === "running" ? "running" : "pending";
  const reportStatus = has("report.ready") ? "completed" : has("findings.verified") && !done ? "running" : done ? "skipped" : "pending";
  const keys = new Set(steps.map((s) => s.step_key));
  const leaves = steps.filter((s) => !steps.some((o) => o.depends_on.includes(s.step_key))).map((s) => s.step_key);
  return [
    { id: "__plan", label: "Plan", status: planStatus, statusLabel: STEP_LABEL[planStatus], deps: [] },
    ...steps.map((s) => ({
      id: s.step_key, label: title(s.step_key), status: s.status, statusLabel: STEP_LABEL[s.status],
      deps: s.depends_on.filter((d) => keys.has(d)).length ? s.depends_on.filter((d) => keys.has(d)) : ["__plan"],
    })),
    { id: "__report", label: "Report", status: reportStatus, statusLabel: STEP_LABEL[reportStatus], deps: leaves.length ? leaves : ["__plan"] },
  ];
}

function Inspector({ run, node, step, calls, now }: { run: RunDetail; node: GraphNode; step?: Step; calls: ToolCall[]; now: number }) {
  if (!step) {
    const isPlan = node.id === "__plan";
    return (
      <>
        <Eyebrow>Execution inspector</Eyebrow>
        <h2 className="mt-3 font-display text-[26px] tracking-tight">{isPlan ? "planner" : "reporting"}</h2>
        <dl className="mt-5">
          <KeyValue label="Status">{node.statusLabel}</KeyValue>
          {isPlan ? (
            <>
              <KeyValue label="Tasks">{run.plan?.steps.length ?? "—"}</KeyValue>
              <KeyValue label="Validation">server-side</KeyValue>
              <KeyValue label="Commit">{run.commit_sha?.slice(0, 10) ?? "—"}</KeyValue>
            </>
          ) : (
            <>
              <KeyValue label="Input">verified findings</KeyValue>
              <KeyValue label="Evidence check">deterministic</KeyValue>
            </>
          )}
        </dl>
        {!isPlan && node.status === "completed" && <TextLink to={`/app/reports/${run.id}`} className="mt-6 inline-block">View report →</TextLink>}
      </>
    );
  }
  const last = calls.at(-1);
  const rejected = calls.filter((c) => c.status === "rejected").length;
  return (
    <>
      <Eyebrow>Execution inspector</Eyebrow>
      <h2 className="mt-3 font-display text-[26px] tracking-tight">{step.agent} agent</h2>
      <p className="mt-2 text-sm leading-relaxed text-muted">{step.action}</p>
      <dl className="mt-4">
        <KeyValue label={step.status === "running" ? "Current action" : "Last action"}>{last ? last.tool.split(".")[1] : "—"}</KeyValue>
        <KeyValue label="Tool">{last ? `mcp.${last.tool.split(".")[0]}` : "—"}</KeyValue>
        <KeyValue label="Permission"><span className="text-teal">read-only</span></KeyValue>
        <KeyValue label="Elapsed">{step.started_at ? duration(elapsedSeconds(step.started_at, step.completed_at, now)) : "—"}</KeyValue>
        <KeyValue label="Tool calls">{calls.length - rejected}{rejected > 0 && <span className="text-amber"> · {rejected} rejected</span>}</KeyValue>
      </dl>
      {step.summary && <p className="mt-4 text-sm leading-relaxed text-muted">{step.summary}</p>}
      {step.error && <p className="mt-4 text-sm leading-relaxed text-red">{step.error}</p>}
      <TextLink to={`/app/runs/${run.id}?step=${step.step_key}`} className="mt-6 inline-block">View tool trace →</TextLink>
    </>
  );
}

function Meter({ label, value, max, text }: { label: string; value: number; max: number; text: string }) {
  const ratio = max > 0 ? Math.min(value / max, 1) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-muted">{label}</span>
        <span className="font-mono text-[13px]">{text}</span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={Math.min(value, max)}
        className="mt-2.5 h-[3px] bg-line">
        <div className={cx("h-full", ratio >= 1 ? "bg-red" : ratio >= 0.8 ? "bg-amber" : "bg-violet-soft")} style={{ width: `${Math.max(ratio * 100, 1.5)}%` }} />
      </div>
    </div>
  );
}

function RunStudio({ runId }: { runId: string }) {
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({ queryKey: ["run", runId] });
  const { events, live } = useRunEvents(runId, refresh);
  const run = useQuery({ queryKey: ["run", runId, "detail"], queryFn: () => api.run(runId),
    refetchInterval: (query) => (query.state.data && TERMINAL.has(query.state.data.status) ? false : 1500) });
  const active = !!run.data && !TERMINAL.has(run.data.status);
  const steps = useQuery({ queryKey: ["run", runId, "steps"], queryFn: () => api.steps(runId), refetchInterval: active ? 1500 : false });
  const calls = useQuery({ queryKey: ["run", runId, "calls"], queryFn: () => api.toolCalls(runId), refetchInterval: active ? 1500 : false });
  const projects = useProjectNames();
  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId), onSuccess: refresh });
  const [selected, setSelected] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);

  const nodes = useMemo(() => (run.data ? buildNodes(run.data, steps.data ?? [], events) : []), [run.data, steps.data, events]);
  const timeline = useRef<HTMLOListElement>(null);
  const lines = useMemo(() => events.map((e) => ({ event: e, line: describe(e) })).filter((x) => x.line), [events]);
  useEffect(() => { timeline.current?.scrollTo({ top: timeline.current.scrollHeight }); }, [lines.length]);

  if (run.isError) return <Page crumb="Agent Studio" title="workflow studio."><ErrorNote error={run.error} retry={() => void run.refetch()} /></Page>;
  if (!run.data) return <Page crumb="Agent Studio" title="workflow studio."><Skeleton className="h-96" /></Page>;

  const r = run.data;
  const project = projects.get(r.project_id);
  const auto = nodes.find((n) => n.status === "running") ?? [...nodes].reverse().find((n) => n.status === "completed" || n.status === "failed") ?? nodes[0];
  const node = nodes.find((n) => n.id === selected) ?? auto;
  const step = steps.data?.find((s) => s.step_key === node.id);
  const stepCalls = (calls.data?.items ?? []).filter((c) => c.step_key === node.id);
  const siblings = new Map<string, number>();
  for (const n of nodes) siblings.set(n.deps.join(","), (siblings.get(n.deps.join(",")) ?? 0) + 1);
  const branches = Math.max(...siblings.values());
  const seconds = elapsedSeconds(r.started_at, r.completed_at, now);
  const hasReport = events.some((e) => e.type === "report.ready");
  const note = r.stop_reason ? `${STOP_REASONS[r.stop_reason] ?? r.stop_reason}${hasReport ? " A partial report is available." : ""}`
    : r.status === "completed" ? "All steps completed and every finding was checked against its evidence."
    : r.status === "pending" ? "Waiting for a worker to pick up the run."
    : "Agents can only read. Every tool call goes through the gateway.";

  return (
    <Page crumb={`Agent Studio / ${project?.repo_name ?? "…"} / Run #${shortId(r.id)}`} title="workflow studio."
      badge={<span className="rounded-[3px] border border-line-strong px-2.5 py-1"><Status status={r.status} /></span>}
      subtitle={<span className="font-mono text-[13px]">Mission — {r.objective}</span>}
      actions={<>
        {hasReport ? <ButtonLink variant="secondary" to={`/app/reports/${r.id}`}>View report</ButtonLink>
          : <Button variant="secondary" disabled title="The report is written at the end of the run">View report</Button>}
        {active && <Button onClick={() => cancel.mutate()} disabled={cancel.isPending}>{cancel.isPending ? "Cancelling…" : "Cancel run"}</Button>}
      </>}>
      {cancel.isError && <div className="mb-5"><ErrorNote error={cancel.error} /></div>}
      <div className="grid items-start gap-7 xl:grid-cols-[minmax(0,1fr)_384px]">
        <div className="min-w-0 space-y-7">
          <Panel className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-6 py-4">
              <h2 className="font-medium">Visual execution plan</h2>
              <p className="font-mono text-xs text-muted">{nodes.length} tasks · {branches} {branches > 1 ? "parallel branches" : "branch"} · read-only</p>
            </div>
            <PlanGraph nodes={nodes} selected={node.id} onSelect={setSelected} />
            <p className={cx("border-t border-line px-6 py-4 text-sm", r.stop_reason && r.stop_reason !== "cancelled" ? "text-amber" : "text-muted")}>{note}</p>
          </Panel>
          <Panel>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-6 py-4">
              <h2 className="font-medium">Live timeline</h2>
              <p className="font-mono text-xs text-muted">
                SSE · {live ? "connected" : TERMINAL.has(r.status) ? "stream ended" : "reconnecting…"} · /runs/{shortId(r.id)}/events
              </p>
            </div>
            {lines.length === 0 ? <p className="px-6 py-6 text-sm text-muted">Waiting for the first event…</p> : (
              <ol ref={timeline} aria-live="polite" className="max-h-[340px] overflow-y-auto">
                {lines.map(({ event, line }, index) => (
                  <li key={event.id} className={cx("flex flex-wrap items-baseline gap-x-5 gap-y-1 border-b border-line px-6 py-3.5 last:border-b-0",
                    index === lines.length - 1 && active && "bg-raised")}>
                    <time className={cx("font-mono text-[13px]", index === lines.length - 1 && active ? "text-violet-soft" : "text-faint")}>{clock(event.created_at)}</time>
                    <span className={cx("flex-1", event.type.endsWith("rejected") || event.type.endsWith("failed") ? "text-amber" : "")}>{line!.text}</span>
                    <span className="font-mono text-xs text-muted">{line!.detail}</span>
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>
        <div className="space-y-7">
          <Panel className="p-6"><Inspector run={r} node={node} step={step} calls={stepCalls} now={now} /></Panel>
          <Panel className="space-y-5 p-6">
            <h2 className="font-medium">Run budget</h2>
            <Meter label="Agent steps" value={r.usage.llm_calls} max={r.limits.max_llm_calls} text={`${r.usage.llm_calls} / ${r.limits.max_llm_calls}`} />
            <Meter label="Tool calls" value={r.usage.tool_calls} max={r.limits.max_tool_calls} text={`${r.usage.tool_calls} / ${r.limits.max_tool_calls}`} />
            <Meter label="Run time" value={seconds} max={r.limits.max_seconds} text={`${duration(seconds)} / ${duration(r.limits.max_seconds)}`} />
            <Meter label="Estimated cost" value={r.usage.cost_usd} max={r.limits.max_cost_usd} text={`${money(r.usage.cost_usd)} / ${money(r.limits.max_cost_usd)}`} />
            <div className="flex flex-wrap gap-2 pt-1"><Tag tone="teal">Read-only</Tag>{r.commit_sha && <Tag>{r.commit_sha.slice(0, 7)}</Tag>}</div>
          </Panel>
        </div>
      </div>
    </Page>
  );
}

export function Studio() {
  const { runId } = useParams();
  const latest = useQuery({ queryKey: ["runs", { limit: 1 }], queryFn: () => api.runs({ limit: 1 }), enabled: !runId });
  if (runId) return <RunStudio key={runId} runId={runId} />;
  if (latest.data?.items[0]) return <Navigate to={`/app/studio/${latest.data.items[0].id}`} replace />;
  return (
    <Page crumb="Agent Studio" title="workflow studio." subtitle="Watch a plan execute, step by step.">
      {latest.isError ? <ErrorNote error={latest.error} retry={() => void latest.refetch()} /> : latest.isPending ? <Skeleton className="h-72" /> : (
        <div className="grid items-start gap-7 xl:grid-cols-[1.45fr_1fr]">
          <Panel><Empty title="no run to show yet.">Start a mission: the plan, each agent and every tool call appear here in real time.</Empty></Panel>
          <MissionForm />
        </div>
      )}
    </Page>
  );
}
