import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { api } from "../lib/api";
import { shortId, truncate } from "../lib/format";
import { cx, Eyebrow, Status, Tag } from "./ui";

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-2.5 font-display text-xl tracking-tight text-fg", className)}>
      <svg aria-hidden viewBox="0 0 24 24" className="size-5">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="12" cy="12" r="3" className="fill-violet-soft" />
      </svg>
      operyxa
    </span>
  );
}

const NAV = [
  { to: "/app", label: "Overview", end: true },
  { to: "/app/projects", label: "Projects" },
  { to: "/app/studio", label: "Agent Studio" },
  { to: "/app/runs", label: "Runs & Traces" },
  { to: "/app/mcp", label: "MCP Hub" },
  { to: "/app/approvals", label: "Approvals" },
  { to: "/app/reports", label: "Reports" },
  { to: "/app/settings", label: "Settings" },
];

function usePlatform() {
  const config = useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: 60_000 });
  const tools = useQuery({ queryKey: ["tools"], queryFn: api.tools, staleTime: 60_000 });
  return { config: config.data, servers: new Set(tools.data?.tools.filter((t) => t.policy === "allow").map((t) => t.server)).size, failed: config.isError };
}

function Sidebar() {
  const { config, servers, failed } = usePlatform();
  const pending = useQuery({ queryKey: ["approvals", "pending"], queryFn: () => api.approvals("pending"), refetchInterval: 5000 });
  const waiting = pending.data?.length ?? 0;
  const { user, signOut } = useAuth();
  return (
    <aside className="flex shrink-0 flex-col border-line bg-panel max-lg:border-b lg:sticky lg:top-0 lg:h-dvh lg:w-[242px] lg:border-r">
      <div className="flex items-center justify-between px-6 py-5 lg:pb-8 lg:pt-8">
        <Link to="/app" aria-label="Operyxa overview"><Logo /></Link>
      </div>
      <Eyebrow className="px-6 pb-3 max-lg:hidden">Agent operations</Eyebrow>
      <nav aria-label="Main" className="flex gap-1 overflow-x-auto px-3 max-lg:pb-3 lg:flex-col">
        {NAV.map((item, index) => (
          <NavLink key={item.to} to={item.to} end={item.end}
            className={({ isActive }) => cx(
              "group flex shrink-0 items-center gap-3 rounded-[5px] px-3 py-3 text-[15px] transition-colors",
              isActive ? "bg-raised font-medium text-fg" : "text-muted hover:bg-raised/60 hover:text-fg")}>
            {({ isActive }) => (
              <>
                <span className={cx("font-mono text-[11px]", isActive ? "text-violet-soft" : "text-faint")}>
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="flex-1">{item.label}</span>
                {item.label === "Approvals" && waiting > 0 && (
                  <span className="rounded-[3px] border border-amber/60 px-1.5 py-0.5 font-mono text-[11px] text-amber">{waiting} pending</span>
                )}
                {isActive && !(item.label === "Approvals" && waiting > 0) && <span aria-hidden className="size-[6px] bg-violet-soft max-lg:hidden" />}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto space-y-4 px-6 py-6 max-lg:hidden">
        <div className="space-y-2.5 border-t border-line pt-5">
          <Eyebrow>System status</Eyebrow>
          {failed ? <Status status="failed" label="API · Unreachable" /> : (
            <>
              <Status status="connected" className="!text-muted [&>span]:text-teal"
                label={config ? `${config.llm_provider === "demo" ? "Demo model" : "Gemini API"} · Connected` : "Connecting…"} />
              <Status status="connected" className="!text-muted [&>span]:text-teal" label={`${servers} MCP servers · Healthy`} />
            </>
          )}
        </div>
        <div className="border-t border-line pt-4 text-xs text-faint">
          <p className="truncate" title={user?.email}>{user?.email}</p>
          <button type="button" onClick={signOut} className="mt-1 text-muted underline underline-offset-4 hover:text-fg">Sign out</button>
        </div>
      </div>
    </aside>
  );
}

function CommandPalette({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const runs = useQuery({ queryKey: ["runs", { limit: 20 }], queryFn: () => api.runs({ limit: 20 }) });
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);

  const items = useMemo(() => {
    const all = [
      ...NAV.map((n) => ({ group: "Go to", label: n.label, hint: "", to: n.to })),
      ...(projects.data?.items ?? []).map((p) => ({ group: "Project", label: p.name, hint: `${p.repo_owner}/${p.repo_name}`, to: "/app/projects" })),
      ...(runs.data?.items ?? []).map((r) => ({ group: "Run", label: truncate(r.objective, 60), hint: shortId(r.id), to: `/app/studio/${r.id}` })),
      { group: "Tool", label: "Tool permissions", hint: "MCP Hub", to: "/app/mcp" },
    ];
    const needle = query.trim().toLowerCase();
    return needle ? all.filter((i) => `${i.label} ${i.hint} ${i.group}`.toLowerCase().includes(needle)) : all;
  }, [query, projects.data, runs.data]);
  const index = Math.min(active, Math.max(items.length - 1, 0));

  const go = (to: string) => { onClose(); navigate(to); };
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 px-4 pt-[14vh]" onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Search" onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-xl overflow-hidden rounded-md border border-line-strong bg-panel shadow-2xl"
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          if (e.key === "ArrowDown") { e.preventDefault(); setActive(Math.min(index + 1, items.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setActive(Math.max(index - 1, 0)); }
          if (e.key === "Enter" && items[index]) go(items[index].to);
        }}>
        <input ref={input} value={query} onChange={(e) => { setQuery(e.target.value); setActive(0); }}
          placeholder="Search projects, runs, tools" aria-label="Search projects, runs, tools"
          className="w-full border-b border-line bg-transparent px-5 py-4 text-[15px] text-fg placeholder:text-faint focus:outline-none" />
        <ul role="listbox" className="max-h-80 overflow-y-auto py-2">
          {items.length === 0 && <li className="px-5 py-6 text-sm text-muted">No match for “{query}”.</li>}
          {items.map((item, i) => (
            <li key={`${item.group}-${item.to}-${i}`} role="option" aria-selected={i === index}>
              <button type="button" onClick={() => go(item.to)} onMouseEnter={() => setActive(i)}
                className={cx("flex w-full items-center gap-3 px-5 py-2.5 text-left text-sm", i === index ? "bg-raised text-fg" : "text-muted")}>
                <span className="w-16 shrink-0 font-mono text-[10px] uppercase tracking-widest text-faint">{item.group}</span>
                <span className="flex-1 truncate">{item.label}</span>
                <span className="font-mono text-xs text-faint">{item.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function AppShell() {
  const [palette, setPalette] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <ShellContext.Provider value={() => setPalette(true)}>
      <div className="min-h-dvh lg:flex">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:bg-violet focus:px-3 focus:py-2">Skip to content</a>
        <Sidebar />
        <div className="min-w-0 flex-1"><Outlet /></div>
      </div>
      {palette && <CommandPalette onClose={() => setPalette(false)} />}
    </ShellContext.Provider>
  );
}

const ShellContext = createContext<() => void>(() => {});

/** Topbar + page heading shared by every screen of the workspace. */
export function Page({ crumb, title, subtitle, badge, actions, children }: {
  crumb: string; title: string; subtitle?: ReactNode; badge?: ReactNode; actions?: ReactNode; children: ReactNode;
}) {
  const openPalette = useContext(ShellContext);
  const { config } = usePlatform();
  return (
    <>
      <header className="flex items-center justify-between gap-4 border-b border-line px-5 py-3 sm:px-9">
        <Eyebrow className="truncate !text-muted">{crumb}</Eyebrow>
        <div className="flex shrink-0 items-center gap-3">
          {config?.llm_provider === "demo" && <span className="max-sm:hidden" title="Runs use the deterministic offline model, not Gemini"><Tag>Demo model</Tag></span>}
          <button type="button" onClick={openPalette}
            className="flex items-center gap-6 rounded-[5px] border border-line-strong px-3.5 py-2.5 text-sm text-muted hover:bg-raised">
            <span className="max-sm:hidden">Search projects, runs, tools</span>
            <span className="sm:hidden">Search</span>
            <kbd className="font-mono text-[11px] text-faint">Ctrl K</kbd>
          </button>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-[1320px] px-5 py-8 sm:px-9 sm:py-10">
        <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-4">
              <h1 className="font-display text-[34px] leading-none tracking-tight sm:text-[40px]">{title}</h1>
              {badge}
            </div>
            {subtitle && <p className="mt-3 text-[15px] text-muted">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap gap-3">{actions}</div>}
        </div>
        {children}
      </main>
    </>
  );
}
