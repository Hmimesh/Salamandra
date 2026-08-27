import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { useWorkspace } from "./context/WorkspaceContext";
import { ActivityPage } from "./pages/ActivityPage";
import { ContactPage } from "./pages/ContactPage";
import { DashboardPage } from "./pages/DashboardPage";
import { EventsPage } from "./pages/EventsPage";
import { InventoryPage } from "./pages/InventoryPage";
import { KitsPage } from "./pages/KitsPage";
import { LegalPage } from "./pages/LegalPage";
import { ReturnsPage } from "./pages/ReturnsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SignInPage } from "./pages/SignInPage";
import { TeamPage } from "./pages/TeamPage";

function LoadingScreen() {
  return <div className="loading-screen"><span className="brand-symbol">S</span><strong>Opening Salamandra</strong></div>;
}

export function App() {
  const { state, loading, toast } = useWorkspace();
  if (loading) return <LoadingScreen />;

  return (
    <>
      {state?.auth.authenticated ? (
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/events/*" element={<EventsPage />} />
            <Route path="/inventory" element={<InventoryPage />} />
            <Route path="/kits" element={<KitsPage />} />
            <Route path="/returns" element={<ReturnsPage />} />
            <Route path="/team" element={<TeamPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/contact" element={<ContactPage />} />
            <Route path="/legal" element={<LegalPage />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Route>
        </Routes>
      ) : (
        <Routes>
          <Route path="/contact" element={<ContactPage />} />
          <Route path="/legal" element={<LegalPage />} />
          <Route path="*" element={<SignInPage />} />
        </Routes>
      )}
      <div className={`toast ${toast ? "visible" : ""}`} role="status" aria-live="polite">{toast}</div>
    </>
  );
}
