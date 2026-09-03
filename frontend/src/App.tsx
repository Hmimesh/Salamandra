import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { PublicLayout } from "./components/PublicLayout";
import { useWorkspace } from "./context/WorkspaceContext";

const ActivityPage = lazy(() => import("./pages/ActivityPage").then((module) => ({ default: module.ActivityPage })));
const ContactPage = lazy(() => import("./pages/ContactPage").then((module) => ({ default: module.ContactPage })));
const DashboardPage = lazy(() => import("./pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const EventsPage = lazy(() => import("./pages/EventsPage").then((module) => ({ default: module.EventsPage })));
const InventoryPage = lazy(() => import("./pages/InventoryPage").then((module) => ({ default: module.InventoryPage })));
const KitsPage = lazy(() => import("./pages/KitsPage").then((module) => ({ default: module.KitsPage })));
const LandingPage = lazy(() => import("./pages/LandingPage").then((module) => ({ default: module.LandingPage })));
const LegalPage = lazy(() => import("./pages/LegalPage").then((module) => ({ default: module.LegalPage })));
const RegisterPage = lazy(() => import("./pages/RegisterPage").then((module) => ({ default: module.RegisterPage })));
const ReturnsPage = lazy(() => import("./pages/ReturnsPage").then((module) => ({ default: module.ReturnsPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })));
const SignInPage = lazy(() => import("./pages/SignInPage").then((module) => ({ default: module.SignInPage })));
const TeamPage = lazy(() => import("./pages/TeamPage").then((module) => ({ default: module.TeamPage })));

function LoadingScreen() {
  return <div className="loading-screen" role="status"><span className="brand-symbol">S</span><strong>Opening Salamandra</strong></div>;
}

export function App() {
  const { state, loading, toast } = useWorkspace();
  if (loading) return <LoadingScreen />;

  return (
    <>
      <Suspense fallback={<LoadingScreen />}>
        {state?.auth.authenticated ? (
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<DashboardPage />} />
              <Route path="/dashboard" element={<Navigate to="/" replace />} />
              <Route path="/events/*" element={<EventsPage />} />
              <Route path="/inventory" element={<InventoryPage />} />
              <Route path="/kits" element={<KitsPage />} />
              <Route path="/returns" element={<ReturnsPage />} />
              <Route path="/team" element={<TeamPage />} />
              <Route path="/activity" element={<ActivityPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/contact" element={<ContactPage />} />
              <Route path="/legal" element={<LegalPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        ) : (
          <Routes>
            <Route element={<PublicLayout />}>
              <Route index element={<LandingPage />} />
              <Route path="/login" element={<SignInPage />} />
              <Route path="/register" element={<RegisterPage />} />
              <Route path="/contact" element={<ContactPage />} />
              <Route path="/legal" element={<LegalPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        )}
      </Suspense>
      <div className={`toast ${toast ? "visible" : ""}`} role="status" aria-live="polite">{toast}</div>
    </>
  );
}
