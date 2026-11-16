import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth } from "./auth";
import { AppShell } from "./components/AppShell";
import { ApiError } from "./lib/api";
import { Approvals } from "./pages/Approvals";
import { AuthPage } from "./pages/AuthPage";
import { Landing } from "./pages/Landing";
import { McpHub } from "./pages/McpHub";
import { Overview } from "./pages/Overview";
import { Projects } from "./pages/Projects";
import { ReportDetail, Reports } from "./pages/Reports";
import { Runs, RunTrace } from "./pages/Runs";
import { Settings } from "./pages/Settings";
import { Studio } from "./pages/Studio";
import "./styles.css";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2000,
      refetchOnWindowFocus: false,
      // Client errors (401, 404, validation) will not succeed on a second try.
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/login" element={<AuthPage mode="login" />} />
            <Route path="/register" element={<AuthPage mode="register" />} />
            <Route path="/app" element={<RequireAuth><AppShell /></RequireAuth>}>
              <Route index element={<Overview />} />
              <Route path="projects" element={<Projects />} />
              <Route path="studio" element={<Studio />} />
              <Route path="studio/:runId" element={<Studio />} />
              <Route path="runs" element={<Runs />} />
              <Route path="runs/:runId" element={<RunTrace />} />
              <Route path="mcp" element={<McpHub />} />
              <Route path="approvals" element={<Approvals />} />
              <Route path="reports" element={<Reports />} />
              <Route path="reports/:runId" element={<ReportDetail />} />
              <Route path="settings" element={<Settings />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
