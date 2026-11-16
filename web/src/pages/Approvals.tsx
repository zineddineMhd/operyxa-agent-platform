import { useQuery } from "@tanstack/react-query";
import { Page } from "../components/AppShell";
import { Empty, Eyebrow, KeyValue, Panel, Tag } from "../components/ui";
import { api } from "../lib/api";

export function Approvals() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  const pending = overview.data?.pending_approvals ?? 0;
  return (
    <Page crumb="Approvals" title="approval center." subtitle="Review exact actions before agents can continue."
      actions={<Tag tone={pending ? "amber" : "muted"}>{String(pending).padStart(2, "0")} pending requests</Tag>}>
      <div className="grid items-start gap-7 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Panel>
          <div className="border-b border-line px-6 py-4"><Eyebrow>Queue</Eyebrow></div>
          <Empty title="nothing to approve.">
            Every tool available today is read-only and runs automatically, so no action reaches this queue.
            Requests will appear here once tools with side effects — sandboxed tests, pull requests — are enabled.
          </Empty>
        </Panel>
        <Panel className="p-6">
          <Eyebrow>How approvals work</Eyebrow>
          <dl className="mt-4">
            <KeyValue label="Low · moderate risk" mono={false}>Automatic</KeyValue>
            <KeyValue label="High risk" mono={false}><span className="text-amber">Explicit approval</span></KeyValue>
            <KeyValue label="Critical risk" mono={false}><span className="text-red">Disabled</span></KeyValue>
            <KeyValue label="Audit trail" mono={false}>Mandatory</KeyValue>
          </dl>
          <p className="mt-5 text-sm leading-relaxed text-muted">
            An approval never replaces authorisation checks. A decision is bound to the exact hash of the tool
            arguments and expires; on resume the server verifies the reviewer's role, the expiry and the hash.
          </p>
        </Panel>
      </div>
    </Page>
  );
}
