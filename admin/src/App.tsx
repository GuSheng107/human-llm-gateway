import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { ToastHost } from "./components/feedback/Toast";
import { AuthProvider, useAuth } from "./features/auth/AuthContext";
import { LoginPage } from "./features/auth/LoginPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { ConnectionsPage } from "./features/connections/ConnectionsPage";
import { ProvidersPage } from "./features/providers/ProvidersPage";
import { RoutesPage } from "./features/routes/RoutesPage";
import { ApiKeysPage } from "./features/api-keys/ApiKeysPage";
import { TasksPage } from "./features/tasks/TasksPage";
import { LogsPage } from "./features/logs/LogsPage";
import { UsersPage } from "./features/users/UsersPage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { AccountPage } from "./features/settings/AccountPage";

function RequireAuth({ children, adminOnly = false }: { children: React.ReactNode; adminOnly?: boolean }) {
  const { user, checking } = useAuth();
  if (checking) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#f4f6f9]">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-[#409eff]" />
      </main>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
  if (adminOnly && user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

function RedirectIfAuthed({ children }: { children: React.ReactNode }) {
  const { user, checking } = useAuth();
  if (checking) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#f4f6f9]">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-[#409eff]" />
      </main>
    );
  }
  if (user) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
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
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/connections" element={<ConnectionsPage />} />
          <Route path="/llm/providers" element={<ProvidersPage />} />
          <Route path="/llm/routes" element={<RoutesPage />} />
          <Route path="/llm/api-keys" element={<ApiKeysPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/admin" element={<RequireAuth adminOnly><DashboardPage /></RequireAuth>} />
          <Route path="/admin/connections" element={<RequireAuth adminOnly><ConnectionsPage /></RequireAuth>} />
          <Route path="/admin/api-keys" element={<RequireAuth adminOnly><ApiKeysPage /></RequireAuth>} />
          <Route path="/admin/providers" element={<RequireAuth adminOnly><ProvidersPage /></RequireAuth>} />
          <Route path="/admin/routes" element={<RequireAuth adminOnly><RoutesPage /></RequireAuth>} />
          <Route path="/admin/tasks" element={<RequireAuth adminOnly><TasksPage /></RequireAuth>} />
          <Route path="/admin/logs" element={<RequireAuth adminOnly><LogsPage /></RequireAuth>} />
          <Route path="/admin/users" element={<RequireAuth adminOnly><UsersPage /></RequireAuth>} />
          <Route path="/admin/settings" element={<RequireAuth adminOnly><SettingsPage /></RequireAuth>} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ToastHost />
    </AuthProvider>
  );
}
