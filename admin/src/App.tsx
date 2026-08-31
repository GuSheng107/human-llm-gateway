import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { ToastHost } from "./components/feedback/Toast";
import { AppShell } from "./components/layout/AppShell";
import { ApiKeysPage } from "./features/apikeys/ApiKeysPage";
import { AuthProvider, useAuth } from "./features/auth/AuthContext";
import { ForcePasswordPage } from "./features/auth/ForcePasswordPage";
import { LoginPage } from "./features/auth/LoginPage";
import { RegisterPage } from "./features/auth/RegisterPage";
import { ConnectionsPage } from "./features/connections/ConnectionsPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { ModelsPage } from "./features/models/ModelsPage";
import { LlmConfigsPage } from "./features/llm/LlmConfigsPage";
import { InvitationsPage } from "./features/invitations/InvitationsPage";
import { LogsPage } from "./features/logs/LogsPage";
import { ToolsPage } from "./features/tools/ToolsPage";
import { AssistantPanel } from "./features/assistant/AssistantPanel";
import { AssistantProvider } from "./features/assistant/AssistantContext";
import { AccountPage } from "./features/settings/AccountPage";
import { ReplyPage } from "./features/tasks/ReplyPage";
import { RepliesWorkbenchPage } from "./features/tasks/RepliesWorkbenchPage";
import { TasksPage } from "./features/tasks/TasksPage";
import { UsersPage } from "./features/users/UsersPage";
import { APP_ROUTES, type AppRouteId } from "./navigation";
import type { Capability } from "./types/auth";

function LoadingScreen() {
  return (
    <main className="grid min-h-screen place-items-center bg-page">
      <span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-primary" />
    </main>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, checking } = useAuth();
  if (checking) return <LoadingScreen />;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function RequireFullAuth({ children }: { children: ReactNode }) {
  const { user, checking } = useAuth();
  if (checking) return <LoadingScreen />;
  if (!user) return <Navigate to="/login" replace />;
  if (user.must_change_password) return <Navigate to="/change-password" replace />;
  return <>{children}</>;
}

function RequireCapability({
  capability,
  children,
}: {
  capability: Capability;
  children: ReactNode;
}) {
  const { user } = useAuth();
  if (!user?.capabilities.includes(capability)) {
    return <Navigate to="/forbidden" replace />;
  }
  return <>{children}</>;
}

function RedirectIfAuthed({ children }: { children: ReactNode }) {
  const { user, checking } = useAuth();
  if (checking) return <LoadingScreen />;
  if (user) {
    return <Navigate to={user.must_change_password ? "/change-password" : "/console"} replace />;
  }
  return <>{children}</>;
}

function PasswordChangeRoute() {
  const { user } = useAuth();
  if (!user?.must_change_password) return <Navigate to="/console" replace />;
  return <ForcePasswordPage />;
}

function ForbiddenPage() {
  return (
    <section className="mx-auto max-w-xl rounded-lg border border-slate-200 bg-white p-8 text-center shadow-card">
      <h1 className="text-lg font-semibold text-slate-800">无权访问此页面</h1>
      <p className="mt-2 text-xs leading-5 text-slate-400">该功能仅向系统管理员开放。</p>
    </section>
  );
}

function AuthedShell() {
  return (
    <AssistantProvider>
      <AppShell />
      <AssistantPanel />
    </AssistantProvider>
  );
}

export default function App() {
  const routeElements: Record<AppRouteId, ReactNode> = {
    console: <DashboardPage />,
    tasks: <TasksPage />,
    replies: <RepliesWorkbenchPage />,
    connections: <ConnectionsPage />,
    apiKeys: <ApiKeysPage />,
    models: <ModelsPage />,
    llmConfigs: <LlmConfigsPage />,
    tools: <ToolsPage />,
    logs: <LogsPage />,
    invitations: <InvitationsPage />,
    users: <UsersPage />,
    account: <AccountPage />,
  };

  return (
    <AuthProvider>
      <Routes>
        <Route
          path="/login"
          element={
            <RedirectIfAuthed>
              <LoginPage />
            </RedirectIfAuthed>
          }
        />
        <Route
          path="/register"
          element={
            <RedirectIfAuthed>
              <RegisterPage />
            </RedirectIfAuthed>
          }
        />
        <Route
          path="/change-password"
          element={
            <RequireAuth>
              <PasswordChangeRoute />
            </RequireAuth>
          }
        />
        <Route
          element={
            <RequireFullAuth>
              <AuthedShell />
            </RequireFullAuth>
          }
        >
          {APP_ROUTES.map((route) => (
            <Route
              key={route.id}
              path={route.path}
              element={
                route.capability ? (
                  <RequireCapability capability={route.capability}>
                    {routeElements[route.id]}
                  </RequireCapability>
                ) : (
                  routeElements[route.id]
                )
              }
            />
          ))}
          {/* 独立回复页（需求 10）：沉浸式编辑思考链 / 正式回复 / 工具调用。 */}
          <Route path="/tasks/:id/reply" element={<ReplyPage />} />
          <Route path="/forbidden" element={<ForbiddenPage />} />
        </Route>
        <Route path="/" element={<Navigate to="/console" replace />} />
        <Route path="*" element={<Navigate to="/console" replace />} />
      </Routes>
      <ToastHost />
    </AuthProvider>
  );
}
