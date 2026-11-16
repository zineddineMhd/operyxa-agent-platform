import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Page } from "../components/AppShell";
import { EvidenceDrawer } from "../components/EvidenceDrawer";
import { Button, ButtonLink, cx, Empty, ErrorNote, Eyebrow, KeyValue, Panel, Skeleton, Status, Tag } from "../components/ui";
import { api, ApiError, type EvidenceRef, type Finding } from "../lib/api";
import { dayTime, shortId, truncate } from "../lib/format";
import { useProjectNames } from "./Overview";

const SEVERITY: Record<string, "red" | "amber" | "muted" | "teal"> = { critical: "red", high: "red", medium: "amber", low: "muted", info: "muted" };

export function Reports() {
  const runs = useQuery({ queryKey: ["runs", { limit: 50 }], queryFn: () => api.runs({ limit: 50 }) });
  const projects = useProjectNames();
  const finished = runs.data?.items.filter((r) => r.status === "completed" || (r.status === "failed" && r.plan)) ?? [];
  return (
    <Page crumb="Reports" title="reports." subtitle="Findings that point to their evidence.">
      <Panel>
        {runs.isPending && <div className="space-y-3 p-6"><Skeleton className="h-10" /><Skeleton className="h-10" /></div>}
        {runs.isError && <div className="p-6"><ErrorNote error={runs.error} retry={() => void runs.refetch()} /></div>}
        {runs.isSuccess && finished.length === 0 && <Empty title="no report yet." action={<ButtonLink to="/app">Start a mission</ButtonLink>}>A report is written at the end of every run, even a partial one.</Empty>}
        <ul>
          {finished.map((run) => (
            <li key={run.id} className="border-b border-line last:border-b-0">
              <Link to={`/app/reports/${run.id}`} className="flex flex-wrap items-center gap-x-6 gap-y-2 px-6 py-4 hover:bg-raised/60">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{truncate(run.objective, 100)}</p>
                  <p className="mt-1.5 font-mono text-xs text-muted">
                    {shortId(run.id)} · {projects.get(run.project_id)?.repo_name ?? "—"} · {dayTime(run.created_at)}
                  </p>
                </div>
                {run.status === "failed" ? <Tag tone="amber">Partial</Tag> : <Status status="completed" label="Complete" />}
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </Page>
  );
}

function EvidenceLine({ item, onOpen }: { item: EvidenceRef; onOpen: () => void }) {
  const where = item.path ? `${item.path}${item.start_line ? `:${item.start_line}` : ""}` : item.tool ?? "unknown source";
  return (
    <li className="flex flex-wrap items-baseline gap-x-4 gap-y-1.5 border-t border-line py-3 first:border-t-0">
      <button type="button" onClick={onOpen} className="font-mono text-[13px] text-violet-soft underline underline-offset-4">{item.evidence_id}</button>
      <span className="font-mono text-[13px] text-fg">{where}</span>
      <code className="min-w-0 flex-1 basis-full truncate font-mono text-xs text-muted sm:basis-0" title={item.quote}>{item.quote}</code>
      {!item.verified && <span className="text-xs text-amber">{item.reason ?? "not confirmed"}</span>}
    </li>
  );
}

function FindingCard({ finding, onEvidence }: { finding: Finding; onEvidence: (item: EvidenceRef) => void }) {
  return (
    <article className="border-b border-line px-6 py-6 last:border-b-0">
      <div className="flex flex-wrap items-center gap-3">
        <Tag tone={SEVERITY[finding.severity] ?? "muted"}>{finding.severity}</Tag>
        <Tag>{finding.category}</Tag>
        <span className="font-mono text-xs text-faint">{finding.agent} agent</span>
        <Status status={finding.status === "verified" ? "verified" : "unverified"} className="ml-auto" />
      </div>
      <h3 className="mt-4 text-[17px] font-medium">{finding.title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-muted">{finding.description}</p>
      {finding.recommendation && <p className="mt-3 text-sm leading-relaxed"><span className="text-muted">Recommendation — </span>{finding.recommendation}</p>}
      <Eyebrow className="mb-1 mt-5">Evidence</Eyebrow>
      <ul>{finding.evidence.map((item, i) => <EvidenceLine key={i} item={item} onOpen={() => onEvidence(item)} />)}</ul>
    </article>
  );
}

export function ReportDetail() {
  const { runId = "" } = useParams();
  const report = useQuery({ queryKey: ["report", runId], queryFn: () => api.report(runId), retry: false });
  const [evidence, setEvidence] = useState<EvidenceRef | null>(null);
  const [copied, setCopied] = useState(false);
  const crumb = `Reports / Run #${shortId(runId)}`;

  if (report.isPending) return <Page crumb={crumb} title="report."><Skeleton className="h-96" /></Page>;
  if (report.isError) {
    const notReady = report.error instanceof ApiError && report.error.code === "REPORT_NOT_READY";
    return (
      <Page crumb={crumb} title="report.">
        {notReady ? <Panel><Empty title="the report is not written yet." action={<ButtonLink to={`/app/studio/${runId}`}>Open the run</ButtonLink>}>It is saved when the run finishes.</Empty></Panel>
          : <ErrorNote error={report.error} retry={() => void report.refetch()} />}
      </Page>
    );
  }
  const { body, markdown, partial } = report.data;
  const download = () => {
    const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown" }));
    const link = Object.assign(document.createElement("a"), { href: url, download: `operyxa-report-${shortId(runId)}.md` });
    link.click();
    URL.revokeObjectURL(url);
  };
  const copy = async () => {
    await navigator.clipboard.writeText(markdown);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <Page crumb={crumb} title="report." subtitle={<span className="font-mono text-[13px]">{body.repository} @ {body.commit_sha.slice(0, 10)}</span>}
      badge={partial ? <Tag tone="amber">Partial report</Tag> : <Tag tone="teal">Complete</Tag>}
      actions={<>
        <Button variant="secondary" onClick={() => void copy()}>{copied ? "Copied" : "Copy Markdown"}</Button>
        <Button onClick={download}>Export Markdown</Button>
      </>}>
      <div className="grid items-start gap-7 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-7">
          <Panel className="p-6">
            <Eyebrow>Summary</Eyebrow>
            <p className="mt-4 text-[15px] leading-relaxed">{body.summary}</p>
            {partial && <p className="mt-4 text-sm text-amber">This run stopped early ({body.stop_reason}). The findings below cover only the completed steps.</p>}
            {body.recommendations.length > 0 && (
              <>
                <Eyebrow className="mt-7">Recommendations</Eyebrow>
                <ol className="mt-3 space-y-2.5">
                  {body.recommendations.map((text, i) => (
                    <li key={i} className="flex gap-4 text-sm leading-relaxed">
                      <span className="font-mono text-xs text-faint">{String(i + 1).padStart(2, "0")}</span>{text}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </Panel>
          <Panel>
            <div className="flex items-center justify-between border-b border-line px-6 py-5">
              <h2 className="font-medium">Verified findings</h2>
              <span className="font-mono text-xs text-muted">{body.findings.length} · each quote confirmed in tool output</span>
            </div>
            {body.findings.length === 0 && <Empty title="no verified finding.">Nothing the agents reported could be confirmed against tool output, or nothing was found.</Empty>}
            {body.findings.map((f) => <FindingCard key={f.id} finding={f} onEvidence={setEvidence} />)}
          </Panel>
          {body.unverified_findings.length > 0 && (
            <Panel tone="amber">
              <div className="border-b border-line px-6 py-5">
                <h2 className="font-medium">Unverified claims</h2>
                <p className="mt-1.5 text-sm text-muted">Excluded from the conclusions: the cited excerpt was not found in what the tools returned.</p>
              </div>
              {body.unverified_findings.map((f) => <FindingCard key={f.id} finding={f} onEvidence={setEvidence} />)}
            </Panel>
          )}
        </div>
        <div className="space-y-7">
          <Panel className="p-6">
            <Eyebrow>Run</Eyebrow>
            <dl className="mt-3">
              <KeyValue label="Run"><Link to={`/app/studio/${runId}`} className="underline underline-offset-4">{shortId(runId)}</Link></KeyValue>
              <KeyValue label="Agent steps">{body.usage.llm_calls}</KeyValue>
              <KeyValue label="Tool calls">{body.usage.tool_calls}</KeyValue>
              <KeyValue label="Tokens">{(body.usage.input_tokens + body.usage.output_tokens).toLocaleString("en-US")}</KeyValue>
              <KeyValue label="Estimated cost">${body.usage.cost_usd.toFixed(2)}</KeyValue>
            </dl>
            <Link to={`/app/runs/${runId}`} className="mt-5 inline-block text-sm text-violet-soft underline underline-offset-4">View tool trace →</Link>
          </Panel>
          <Panel className="p-6">
            <Eyebrow>Steps</Eyebrow>
            <ul className="mt-3">
              {body.steps.map((s) => (
                <li key={s.id} className="border-b border-line py-3 last:border-b-0">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-mono text-[13px]">{s.id}</span><Status status={s.status} />
                  </div>
                  {(s.summary || s.error) && <p className={cx("mt-1.5 text-xs leading-relaxed", s.error ? "text-red" : "text-muted")}>{s.error ?? s.summary}</p>}
                </li>
              ))}
            </ul>
          </Panel>
          <p className="text-xs leading-relaxed text-faint">{body.disclaimer}</p>
        </div>
      </div>
      {evidence && <EvidenceDrawer runId={runId} evidenceId={evidence.evidence_id} quote={evidence.quote} onClose={() => setEvidence(null)} />}
    </Page>
  );
}
