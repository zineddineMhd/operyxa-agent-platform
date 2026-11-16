import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Logo } from "../components/AppShell";
import { Button, ErrorNote, Eyebrow, Field, INPUT } from "../components/ui";
import { api } from "../lib/api";

export function AuthPage({ mode }: { mode: "login" | "register" }) {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const target = (location.state as { from?: string } | null)?.from ?? "/app";
  if (user) return <Navigate to={target} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "register") await api.register(email, password);
      await signIn(email, password);
      navigate(target, { replace: true });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };
  const register = mode === "register";
  return (
    <div className="dot-grid grid min-h-dvh place-items-center px-5 py-10">
      <div className="w-full max-w-[420px]">
        <Link to="/" aria-label="Operyxa home"><Logo /></Link>
        <form onSubmit={submit} className="mt-8 space-y-5 rounded-md border border-line bg-panel p-7">
          <div>
            <Eyebrow>{register ? "Create an account" : "Sign in"}</Eyebrow>
            <h1 className="mt-3 font-display text-[28px] leading-tight tracking-tight">
              {register ? "start with a read-only analysis." : "welcome back."}
            </h1>
          </div>
          <Field label="Email">
            <input className={INPUT} type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Password" hint={register ? "At least 10 characters." : undefined}>
            <input className={INPUT} type="password" required minLength={register ? 10 : undefined}
              autoComplete={register ? "new-password" : "current-password"} value={password}
              onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error != null && <ErrorNote error={error} />}
          <Button type="submit" disabled={busy} className="w-full py-3">
            {busy ? "Please wait…" : register ? "Create account" : "Sign in"}
          </Button>
          <p className="text-sm text-muted">
            {register ? "Already registered? " : "No account yet? "}
            <Link to={register ? "/login" : "/register"} state={location.state}
              className="text-violet-soft underline underline-offset-4">
              {register ? "Sign in" : "Create one"}
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
