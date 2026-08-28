import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/layout/AppShell";
import { ToastHost } from "./components/feedback/Toast";
import { AuthProvider, useAuth } from "./features/auth/AuthContext";
import { LoginPage } from "./features/auth/LoginPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { AccountPage } from "./features/settings/AccountPage";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, checking } = useAuth();
  if (checking) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#f4f6f9]">
        <span className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-[#409eff]" />
      </main>
    );
  }
  if (!user) return <Navigate to="/login" replace />;
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
        <Route
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
          <Route path="/" element={<DashboardPage />} />
          <Route path="/account" element={<AccountPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ToastHost />
    </AuthProvider>
  );
}
