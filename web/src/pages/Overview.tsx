import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Page } from "../components/AppShell";
import { MissionForm } from "../components/MissionForm";
import { cx, Empty, ErrorNote, Eyebrow, Panel, Skeleton, Status, TextLink } from "../components/ui";
import { api, type Project, type Run } from "../lib/api";
import { dayTime, greeting, money, percent, truncate } from "../lib/format";

export function useProjectNames(): Map<string, Project> {
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  return new Map(projects.data?.items.map((p) => [p.id, p]));
}

export function RunRow({ run, project }: { run: Run; project?: Project }) {
  return (
    <li className="border-b border-line last:border-b-0">
      <Link to={`/app/studio/${run.id}`} className="flex items-center gap-6 px-6 py-4 hover:bg-raised/60">
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{truncate(run.objective, 90)}</p>
          <p className="mt-1.5 font-mono text-xs text-muted">
            {project ? project.repo_name : "—"} · {dayTime(run.created_at)}
          </p>
        </div>
        <Status status={run.status} label={run.status === "running" ? "In progress" : undefined} />
      </Link>
    </li>
  );
}

export function Overview() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview, refetchInterval: 10_000 });
  const runs = useQuery({ queryKey: ["runs", { limit: 5 }], queryFn: () => api.runs({ limit: 5 }), refetchInterval: 5_000 });
  const projects = useProjectNames();
  const o = overview.data;
  const kpis = [
    { label: "Runs this week", value: o ? String(o.runs_this_week) : null, note: o ? `${o.runs_total} in total` : "" },
    { label: "Success rate", value: o ? percent(o.success_rate) : null, note: o ? `${o.runs_by_status.completed ?? 0} completed` : "" },
    { label: "Pending approvals", value: o ? String(o.pending_approvals).padStart(2, "0") : null,
      note: o?.pending_approvals ? "Require attention" : "Nothing waiting", tone: o?.pending_approvals ? "text-amber" : "" },
    { label: "Avg. run cost", value: o ? (o.avg_run_cost_usd === null ? "—" : money(o.avg_run_cost_usd)) : null, note: "Estimates only" },
  ];
  return (
    <Page crumb="Overview" title={`${greeting()}, developer.`} subtitle="Your projects, agent runs and decisions in one place.">
      {overview.isError && <div className="mb-6"><ErrorNote error={overview.error} retry={() => void overview.refetch()} /></div>}
      <Panel className="grid grid-cols-2 overflow-hidden xl:grid-cols-4">
        {kpis.map((kpi) => (
          <div key={kpi.label} className="-mb-px -mr-px border-b border-r border-line p-6">
            <Eyebrow>{kpi.label}</Eyebrow>
            {kpi.value === null ? <Skeleton className="mt-4 h-10 w-24" />
              : <p className={cx("mt-3 font-display text-[42px] leading-none tracking-tight", kpi.tone)}>{kpi.value}</p>}
            <p className="mt-3 text-sm text-muted">{kpi.note}</p>
          </div>
        ))}
      </Panel>
      <div className="mt-7 grid items-start gap-7 xl:grid-cols-[1.45fr_1fr]">
        <Panel>
          <div className="flex items-center justify-between border-b border-line px-6 py-5">
            <h2 className="font-medium">Recent workflows</h2>
            <TextLink to="/app/runs">View all</TextLink>
          </div>
          {runs.isPending && <div className="space-y-3 p-6"><Skeleton className="h-10" /><Skeleton className="h-10" /><Skeleton className="h-10" /></div>}
          {runs.isError && <div className="p-6"><ErrorNote error={runs.error} retry={() => void runs.refetch()} /></div>}
          {runs.data?.items.length === 0 && (
            <Empty title="no run yet.">
              Write a mission such as “Review architecture, dependencies and the last five commits. Make no changes.”
            </Empty>
          )}
          <ul>{runs.data?.items.map((run) => <RunRow key={run.id} run={run} project={projects.get(run.project_id)} />)}</ul>
        </Panel>
        <MissionForm />
      </div>
    </Page>
  );
}
