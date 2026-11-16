import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, auth, type User } from "./lib/api";

interface AuthValue {
  user: User | undefined;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => void;
}
const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [token, setToken] = useState(auth.token);
  const me = useQuery({ queryKey: ["me", token], queryFn: api.me, enabled: !!token, retry: false });

  const signOut = useCallback(() => {
    auth.set(null);
    setToken(null);
    client.clear();
  }, [client]);
  useEffect(() => { auth.onUnauthorized(signOut); }, [signOut]);

  const value = useMemo<AuthValue>(() => ({
    user: token ? me.data : undefined,
    loading: !!token && me.isPending,
    signOut,
    signIn: async (email, password) => {
      const { access_token } = await api.login(email, password);
      auth.set(access_token);
      setToken(access_token);
    },
  }), [token, me.data, me.isPending, signOut]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="grid min-h-dvh place-items-center text-sm text-muted">Loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return children;
}
