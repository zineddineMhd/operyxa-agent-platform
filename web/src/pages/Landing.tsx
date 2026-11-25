import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { Logo } from "../components/AppShell";
import { ButtonLink, Eyebrow, Status } from "../components/ui";

const AGENTS = [
  ["planner", "Turns the objective and the available capabilities into a plan.", "ExecutionPlan"],
  ["supervisor", "Routes each step within the current state, the plan and the budgets.", "RoutingDecision"],
  ["repository", "Reads the tree, the changes and the references of the authorised repository.", "RepoFindings"],
  ["security", "Reviews dependencies, configuration and authorised excerpts, with a severity and a proof for each finding.", "SecurityFindings"],
  ["testing", "Runs approved test profiles in an isolated sandbox and returns status, logs and artifacts.", "TestResults"],
  ["reporting", "Assembles validated results into a summary, prioritised risks and recommendations.", "FinalReport"],
];
const PRINCIPLES = [
  ["Evidence-first", "A conclusion must reference a file, a line, a test or a tool."],
  ["Human-controlled", "Actions with side effects are explicitly approved by a person."],
  ["Replayable", "Each run can be inspected and resumed, with idempotent tool calls."],
  ["Tool-transparent", "Every MCP call is traced with its permission and its result."],
  ["Developer-first", "A flow centred on the repository, its risks, its tests and its reports."],
];
const LIMITS = [["20", "agent steps per run"], ["40", "tool calls per run"], ["600 s", "of run time"], ["$2.00", "estimated cost per run"]];
const POLICY: [string, string, string, string][] = [
  ["List authorised files", "Low", "text-teal", "Automatic"],
  ["Read an authorised repository", "Low", "text-teal", "Automatic"],
  ["Scan dependencies", "Moderate", "text-fg", "Project authorisation and limits"],
  ["Run tests", "High", "text-amber", "Sandbox, authorised profile"],
  ["Modify files or open a pull request", "High", "text-amber", "Explicit approval"],
  ["Delete, or run arbitrary shell", "Critical", "text-red", "Disabled"],
];
const RUN_ROWS: [string, string, string, string?][] = [
  ["01", "Plan", "completed"], ["02", "Inspect repository", "completed"],
  ["03", "Security · scan_dependencies", "running"], ["04", "Tests", "waiting_approval"], ["05", "Report", "pending"],
];
const WRAP = "mx-auto max-w-[1180px] px-6 sm:px-10";

export function Landing() {
  const { user } = useAuth();
  const start = user ? "/app" : "/register";
  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-10 border-b border-line bg-ink/90 backdrop-blur">
        <div className={`${WRAP} flex items-center justify-between py-3.5`}>
          <Link to="/" aria-label="Operyxa home"><Logo /></Link>
          <nav aria-label="Sections" className="flex gap-8 text-sm text-muted max-md:hidden">
            {["Platform", "Agents", "Principles", "Control"].map((s) => (
              <a key={s} href={`#${s.toLowerCase()}`} className="hover:text-fg">{s}</a>
            ))}
          </nav>
          <div className="flex items-center gap-5">
            <Link to={user ? "/app" : "/login"} className="text-sm font-medium hover:text-violet-soft">{user ? "Open workspace" : "Sign in"}</Link>
            <ButtonLink to={start} className="max-sm:hidden">Analyze a repository</ButtonLink>
          </div>
        </div>
      </header>

      <section className="dot-grid border-b border-line">
        <div className={`${WRAP} grid items-center gap-14 py-20 lg:grid-cols-[1fr_1.05fr] lg:py-28`}>
          <div>
            <Eyebrow>Agentic AI / Developer operations</Eyebrow>
            <h1 className="mt-6 font-display text-[52px] leading-[1.02] tracking-tight sm:text-[68px]">
              agent workflows you can see, verify and control.
            </h1>
            <p className="mt-7 max-w-md text-[17px] leading-relaxed text-muted">
              Hand Operyxa a mission on your repository. Review the plan, watch every tool call, approve what
              matters, and get a report where each finding points to its evidence.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-6">
              <ButtonLink to={start} className="px-5 py-3">Analyze a repository</ButtonLink>
              <a href="#platform" className="text-sm underline underline-offset-4 hover:text-violet-soft">See how a run works →</a>
            </div>
          </div>
          <div className="rounded-md border border-line bg-panel">
            <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
              <Eyebrow>Run RA-2418 · agentops-core</Eyebrow>
              <Eyebrow className="!text-teal">Read-only</Eyebrow>
            </div>
            <div className="border-b border-line px-5 py-4">
              <Eyebrow>Mission</Eyebrow>
              <p className="mt-3 font-mono text-[13px] leading-relaxed text-fg">
                Connect my GitHub repository, examine the architecture, the dependencies and the last five commits;
                detect weak points; produce a prioritised report with evidence. Make no changes.
              </p>
            </div>
            <ul>
              {RUN_ROWS.map(([n, label, status]) => (
                <li key={n} className={`flex items-center gap-4 border-b border-line px-5 py-3.5 last:border-b-0 ${status === "running" ? "bg-raised" : ""}`}>
                  <span className={`font-mono text-xs ${status === "running" ? "text-violet-soft" : "text-faint"}`}>{n}</span>
                  <span className={`flex-1 text-sm ${status === "pending" ? "text-muted" : "text-fg"}`}>{label}</span>
                  <Status status={status} className="!text-xs" />
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section id="platform" className="border-b border-line">
        <div className={`${WRAP} grid gap-10 py-16 md:grid-cols-3`}>
          {[
            ["plan.", "The Planner turns your objective into a structured execution plan — steps, dependencies, expected outputs — validated on the server before anything runs."],
            ["execute.", "A supervisor routes work to repository and security agents. Every MCP tool call is traced with its permission, its duration and its result."],
            ["verify.", "Findings are checked against their evidence before the report is written. A conclusion without a source is marked uncertain or rejected."],
          ].map(([title, text], i) => (
            <div key={title}>
              <Eyebrow>{`0${i + 1}`}</Eyebrow>
              <h2 className="mt-3 font-display text-[32px] tracking-tight">{title}</h2>
              <p className="mt-3 text-sm leading-relaxed text-muted">{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="border-b border-line">
        <p className={`${WRAP} py-24 font-display text-[30px] leading-snug tracking-tight sm:text-[36px]`}>
          <span className="block max-w-3xl">
            Operyxa is a working tool for software engineering, not a chatbot.{" "}
            <span className="text-muted">You hand over a mission, review the plan, control the tools, approve the
              sensitive steps — and receive a report built on evidence.</span>
          </span>
        </p>
      </section>

      <section id="agents" className="border-b border-line">
        <div className={`${WRAP} py-20`}>
          <Eyebrow>Orchestration</Eyebrow>
          <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
            <h2 className="font-display text-[40px] leading-none tracking-tight">six agents, one contract each.</h2>
            <Link to={user ? "/app/studio" : "/login"} className="text-sm underline underline-offset-4 hover:text-violet-soft">Open Agent Studio →</Link>
          </div>
          <div className="mt-10 grid overflow-hidden rounded-md border border-line sm:grid-cols-2 lg:grid-cols-3">
            {AGENTS.map(([name, text, contract], i) => (
              <article key={name} className="-mb-px -mr-px border-b border-r border-line p-6">
                <Eyebrow>{`A${i + 1}`}{name === "testing" && " · opt-in"}</Eyebrow>
                <h3 className="mt-4 font-display text-[22px] tracking-tight">{name}</h3>
                <p className="mt-2 min-h-[60px] text-sm leading-relaxed text-muted">{text}</p>
                <p className="mt-4 font-mono text-xs text-violet-soft">→ {contract}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="principles" className="border-b border-line">
        <div className={`${WRAP} grid gap-12 py-20 lg:grid-cols-2`}>
          <div>
            <Eyebrow>Principles</Eyebrow>
            <h2 className="mt-4 font-display text-[40px] leading-[1.05] tracking-tight">nothing happens out of sight.</h2>
          </div>
          <dl className="border-t border-line">
            {PRINCIPLES.map(([term, text]) => (
              <div key={term} className="border-b border-line py-5">
                <dt className="font-medium">{term}</dt>
                <dd className="mt-1.5 text-sm text-muted">{text}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section id="control" className="border-b border-line">
        <div className={`${WRAP} py-20`}>
          <Eyebrow>Control</Eyebrow>
          <h2 className="mt-4 font-display text-[40px] leading-none tracking-tight">bounded by design.</h2>
          <p className="mt-4 max-w-xl text-[15px] leading-relaxed text-muted">
            Authorisation is decided by the backend, never by the model. Every run has hard limits, and stops
            cleanly with a partial report when one is reached.
          </p>
          <dl className="mt-10 grid grid-cols-2 overflow-hidden rounded-md border border-line lg:grid-cols-4">
            {LIMITS.map(([value, label]) => (
              <div key={label} className="-mb-px -mr-px border-b border-r border-line p-6">
                <dd className="font-display text-[40px] leading-none tracking-tight">{value}</dd>
                <dt className="mt-3 text-xs text-muted">{label}</dt>
              </div>
            ))}
          </dl>
          <p className="mt-4 font-mono text-xs text-faint">Default limits, configurable per deployment.</p>
          <div className="mt-8 overflow-x-auto rounded-md border border-line">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead>
                <tr className="border-b border-line font-mono text-[11px] uppercase tracking-[0.16em] text-faint">
                  <th className="px-5 py-3.5 font-normal">Action</th><th className="px-5 font-normal">Risk</th><th className="px-5 font-normal">Policy</th>
                </tr>
              </thead>
              <tbody>
                {POLICY.map(([action, risk, tone, policy]) => (
                  <tr key={action} className="border-b border-line last:border-b-0">
                    <td className="px-5 py-3.5">{action}</td>
                    <td className={`px-5 ${tone}`}>{risk}</td>
                    <td className="px-5 text-muted">{policy}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="dot-grid border-b border-line">
        <div className={`${WRAP} py-24`}>
          <h2 className="max-w-md font-display text-[52px] leading-[1.02] tracking-tight">start with a read-only analysis.</h2>
          <p className="mt-6 max-w-sm text-[17px] leading-relaxed text-muted">
            Connect a GitHub repository, write a mission, review the plan. Nothing in your code is modified.
          </p>
          <ButtonLink to={start} className="mt-8 px-5 py-3">Analyze a repository</ButtonLink>
        </div>
      </section>

      <footer className={`${WRAP} py-14`}>
        <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Logo />
            <Eyebrow className="mt-4">Plan. Execute. Verify.</Eyebrow>
          </div>
          <FooterList title="Product" items={[["Overview", "/app"], ["Agent Studio", "/app/studio"], ["MCP Hub", "/app/mcp"], ["Approvals", "/app/approvals"]]} />
          <FooterList title="Built on" items={[["LangGraph · LangChain"], ["Model Context Protocol"], ["Gemini API"], ["FastAPI · React · PostgreSQL"]]} />
          <FooterList title="Project" items={[["Reports", "/app/reports"], ["Runs & Traces", "/app/runs"], ["Settings", "/app/settings"]]} />
        </div>
        <p className="mt-12 border-t border-line pt-6 text-xs text-faint">
          © 2026 Operyxa — working name; trademark and domain availability to be confirmed.
        </p>
      </footer>
    </div>
  );
}

function FooterList({ title, items }: { title: string; items: [string, string?][] }) {
  return (
    <div>
      <Eyebrow>{title}</Eyebrow>
      <ul className="mt-5 space-y-4 text-sm text-muted">
        {items.map(([label, to]) => (
          <li key={label}>{to ? <Link to={to} className="hover:text-fg">{label}</Link> : label}</li>
        ))}
      </ul>
    </div>
  );
}
