import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Page } from "../components/AppShell";
import { Button, cx, Empty, ErrorNote, Eyebrow, KeyValue, Panel, Skeleton, Status, Tag, TextLink } from "../components/ui";
import { api, type Approval } from "../lib/api";
import { dayTime, duration, shortId } from "../lib/format";

const EFFECTS: Record<string, string> = {
  "test_runner.run_test_profile": "Executes repository test code in the sandbox.",
};
const remaining = (approval: Approval, now: number) => Math.max(0, (Date.parse(approval.expires_at) - now) / 1000);

function Detail({ approval, now }: { approval: Approval; now: number }) {
  const client = useQueryClient();
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => api.decideApproval(approval.id, decision),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const left = remaining(approval, now);
  const expired = left <= 0;
  return (
    <Panel>
      <div className="border-b border-line p-6">
        <div className="flex items-center justify-between gap-3">
          <Eyebrow>Approval request #{shortId(approval.id).replace("RA", "AP")}</Eyebrow>
          <Tag tone="amber">{approval.risk} impact</Tag>
        </div>
        <h2 className="mt-4 font-display text-[28px] leading-tight tracking-tight">{approval.agent} agent requests execution.</h2>
        <p className="mt-2 font-mono text-[13px] text-muted">Run {shortId(approval.run_id)} · step {approval.step_key}</p>
      </div>
      <div className="p-6">
        <dl>
          <KeyValue label="Tool">{approval.tool}</KeyValue>
          {Object.entries(approval.args).map(([key, value]) => (
            <KeyValue key={key} label={key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}>
              <span className="break-all">{typeof value === "string" ? value : JSON.stringify(value)}</span>
            </KeyValue>
          ))}
          <KeyValue label="Argument hash">{approval.args_hash.slice(0, 16)}…</KeyValue>
        </dl>
        <div className="mt-5 rounded-[5px] border border-amber/50 p-4">
          <Eyebrow className="!text-amber">Potential effect</Eyebrow>
          <p className="mt-2 text-[15px]">{EFFECTS[approval.tool] ?? "Runs a tool with side effects, with exactly the arguments shown above."}</p>
        </div>
        <p className={cx("mt-4 text-sm leading-relaxed", expired ? "text-red" : "text-muted")}>
          {expired ? "This request has expired. The agent was told the action is not authorised."
            : `Approval expires in ${duration(left)}. Arguments are locked: the decision is bound to their exact hash.`}
        </p>
        {decide.isError && <div className="mt-4"><ErrorNote error={decide.error} /></div>}
        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <Button variant="danger" disabled={expired || decide.isPending} onClick={() => decide.mutate("reject")} className="px-6 py-3">Reject</Button>
          <Button disabled={expired || decide.isPending} onClick={() => decide.mutate("approve")} className="px-6 py-3">Approve action</Button>
        </div>
      </div>
    </Panel>
  );
}

export function Approvals() {
  const pending = useQuery({ queryKey: ["approvals", "pending"], queryFn: () => api.approvals("pending"), refetchInterval: 3000 });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const items = pending.data ?? [];
  const selected = items.find((a) => a.id === selectedId) ?? items[0];
  return (
    <Page crumb={selected ? `Approvals / #${shortId(selected.id).replace("RA", "AP")}` : "Approvals"} title="approval center."
      subtitle="Review exact actions before agents can continue."
      actions={<Tag tone={items.length ? "amber" : "muted"}>{String(items.length).padStart(2, "0")} pending requests</Tag>}>
      {pending.isError && <div className="mb-6"><ErrorNote error={pending.error} retry={() => void pending.refetch()} /></div>}
      <div className="grid items-start gap-7 xl:grid-cols-[300px_minmax(0,1fr)_340px]">
        <Panel>
          <div className="border-b border-line px-5 py-4"><Eyebrow>Queue</Eyebrow></div>
          {pending.isPending && <div className="p-5"><Skeleton className="h-16" /></div>}
          {pending.isSuccess && items.length === 0 && <p className="px-5 py-6 text-sm text-muted">No request is waiting.</p>}
          <ul>
            {items.map((approval) => (
              <li key={approval.id} className="border-b border-line last:border-b-0">
                <button type="button" onClick={() => setSelectedId(approval.id)} aria-pressed={approval.id === selected?.id}
                  className={cx("w-full px-5 py-4 text-left", approval.id === selected?.id ? "bg-raised" : "hover:bg-raised/60")}>
                  <span className="flex justify-between font-mono text-xs">
                    <span className="text-violet-soft">#{shortId(approval.id).replace("RA", "AP")}</span>
                    <span className="text-amber">{duration(remaining(approval, now))} left</span>
                  </span>
                  <span className="mt-2 block font-medium">{approval.agent} agent requests execution</span>
                  <span className="mt-1 block truncate font-mono text-xs text-muted">{approval.tool} · {dayTime(approval.requested_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>
        {selected ? <Detail key={selected.id} approval={selected} now={now} /> : (
          <Panel>
            <Empty title="nothing to approve.">
              The tools enabled today are read-only and run automatically. A request appears here as soon as an
              agent asks for a high-risk tool, such as running tests in the sandbox.
            </Empty>
          </Panel>
        )}
        <Panel className="p-6">
          <Eyebrow>Evidence & policy</Eyebrow>
          <dl className="mt-3">
            {selected ? (
              <>
                <KeyValue label="Risk score" mono={false}><span className="capitalize text-amber">{selected.risk}</span></KeyValue>
                <KeyValue label="Agent" mono={false}><span className="capitalize">{selected.agent}</span></KeyValue>
                <KeyValue label="MCP server" mono={false}>{selected.tool.split(".")[0]}</KeyValue>
                <KeyValue label="Scope" mono={false}>Selected repo</KeyValue>
                <KeyValue label="Status" mono={false}><Status status="waiting_approval" label="Pending" /></KeyValue>
              </>
            ) : (
              <>
                <KeyValue label="Low · moderate risk" mono={false}>Automatic</KeyValue>
                <KeyValue label="High risk" mono={false}><span className="text-amber">Explicit approval</span></KeyValue>
                <KeyValue label="Critical risk" mono={false}><span className="text-red">Disabled</span></KeyValue>
              </>
            )}
            <KeyValue label="Audit trail" mono={false}>Mandatory</KeyValue>
          </dl>
          <p className="mt-5 text-sm leading-relaxed text-muted">
            An approval never replaces authorisation checks. The decision is bound to the exact argument hash and
            expires; the server verifies the reviewer, the expiry and the hash before the tool runs.
          </p>
          {selected && <TextLink to={`/app/studio/${selected.run_id}`} className="mt-5 inline-block">Open run {shortId(selected.run_id)} →</TextLink>}
        </Panel>
      </div>
    </Page>
  );
}
