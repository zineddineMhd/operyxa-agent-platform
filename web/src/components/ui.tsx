import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";
import { ApiError } from "../lib/api";

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

type Tone = "teal" | "violet" | "amber" | "red" | "muted";
const TONE_TEXT: Record<Tone, string> = {
  teal: "text-teal", violet: "text-violet-soft", amber: "text-amber", red: "text-red", muted: "text-faint",
};

// State is always carried by a label and a shape, never by colour alone.
const STATUS: Record<string, { label: string; tone: Tone; shape: "square" | "ring" | "box" | "none" }> = {
  completed: { label: "Completed", tone: "teal", shape: "square" },
  connected: { label: "Connected", tone: "teal", shape: "square" },
  verified: { label: "Verified", tone: "teal", shape: "square" },
  running: { label: "Running", tone: "violet", shape: "ring" },
  waiting_approval: { label: "Waiting for approval", tone: "amber", shape: "box" },
  restricted: { label: "Restricted", tone: "amber", shape: "box" },
  unverified: { label: "Unverified", tone: "amber", shape: "box" },
  rejected: { label: "Rejected", tone: "amber", shape: "box" },
  failed: { label: "Failed", tone: "red", shape: "square" },
  pending: { label: "Queued", tone: "muted", shape: "none" },
  skipped: { label: "Skipped", tone: "muted", shape: "none" },
  cancelled: { label: "Cancelled", tone: "muted", shape: "none" },
};

export function statusTone(status: string): Tone {
  return STATUS[status]?.tone ?? "muted";
}

export function Status({ status, label, className }: { status: string; label?: string; className?: string }) {
  const spec = STATUS[status] ?? { label: status, tone: "muted" as Tone, shape: "none" as const };
  return (
    <span className={cx("inline-flex items-center gap-2 whitespace-nowrap text-[13px]", TONE_TEXT[spec.tone], className)}>
      {spec.shape === "square" && <span aria-hidden className="size-[7px] bg-current" />}
      {spec.shape === "ring" && <span aria-hidden className="size-[9px] rounded-full border-2 border-current" />}
      {spec.shape === "box" && <span aria-hidden className="size-[9px] border-2 border-current" />}
      {label ?? spec.label}
    </span>
  );
}

export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("font-mono text-[11px] uppercase tracking-[0.16em] text-faint", className)}>{children}</p>;
}

export function Panel({ children, className, tone }: { children: ReactNode; className?: string; tone?: "amber" }) {
  return (
    <section className={cx("rounded-md border bg-panel", tone === "amber" ? "border-amber/50" : "border-line", className)}>
      {children}
    </section>
  );
}

export function Tag({ children, tone = "muted" }: { children: ReactNode; tone?: Tone }) {
  const border = { teal: "border-teal/50", violet: "border-violet-soft/50", amber: "border-amber/60", red: "border-red/60", muted: "border-line-strong" }[tone];
  return (
    <span className={cx("inline-flex items-center rounded-[3px] border px-2 py-1 font-mono text-[11px] uppercase tracking-[0.12em]", border, tone === "muted" ? "text-muted" : TONE_TEXT[tone])}>
      {children}
    </span>
  );
}

const BUTTON = {
  primary: "bg-violet text-white hover:bg-violet-hover",
  secondary: "border border-line-strong text-fg hover:bg-raised",
  danger: "border border-red/60 text-red hover:bg-red/10",
};
const BUTTON_BASE = "inline-flex items-center justify-center rounded-[5px] px-4 py-2.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50";

export function Button({ variant = "primary", className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof BUTTON }) {
  return <button type="button" {...props} className={cx(BUTTON_BASE, BUTTON[variant], className)} />;
}

export function ButtonLink({ variant = "primary", className, ...props }: LinkProps & { variant?: keyof typeof BUTTON }) {
  return <Link {...props} className={cx(BUTTON_BASE, BUTTON[variant], className)} />;
}

export function TextLink({ className, ...props }: LinkProps) {
  return <Link {...props} className={cx("text-sm text-violet-soft underline underline-offset-4 hover:text-fg", className)} />;
}

export function KeyValue({ label, children, mono = true }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-6 border-b border-line py-3 last:border-b-0">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className={cx("text-right text-sm text-fg", mono && "font-mono text-[13px]")}>{children}</dd>
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="font-display text-xl text-fg">{title}</p>
      {children && <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">{children}</p>}
      {action && <div className="mt-5 flex justify-center">{action}</div>}
    </div>
  );
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  const requestId = error instanceof ApiError ? error.requestId : undefined;
  return (
    <div role="alert" className="rounded-md border border-red/50 bg-red/5 px-4 py-3 text-sm text-red">
      <p>{message}</p>
      {requestId && <p className="mt-1 font-mono text-[11px] text-faint">request {requestId}</p>}
      {retry && <button type="button" onClick={retry} className="mt-2 underline underline-offset-4">Try again</button>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("animate-pulse rounded bg-raised", className)} />;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-2 block text-sm text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-xs text-faint">{hint}</span>}
    </label>
  );
}

export const INPUT = "w-full rounded-[5px] border border-line-strong bg-ink px-3.5 py-2.5 text-sm text-fg placeholder:text-faint focus:border-violet-soft focus:outline-none";
