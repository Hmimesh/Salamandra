import {
  Activity,
  Boxes,
  CalendarDays,
  ChevronDown,
  CircleHelp,
  ClipboardCheck,
  Gauge,
  LogOut,
  Menu,
  PackageOpen,
  Plus,
  Settings,
  Users,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useWorkspace } from "../context/WorkspaceContext";
import { Avatar, Spinner } from "./ui";

const navigation = [
  { to: "/dashboard", label: "Dashboard", icon: Gauge },
  { to: "/events", label: "Events", icon: CalendarDays },
  { to: "/inventory", label: "Inventory", icon: Boxes },
  { to: "/kits", label: "Kits", icon: PackageOpen },
  { to: "/returns", label: "Returns", icon: ClipboardCheck },
  { to: "/team", label: "Team", icon: Users, divider: true },
  { to: "/activity", label: "Activity", icon: Activity },
  { to: "/settings", label: "Settings", icon: Settings },
];

const pageNames: Record<string, string> = {
  dashboard: "Dashboard",
  events: "Events",
  inventory: "Inventory",
  kits: "Kits",
  returns: "Returns",
  team: "Team",
  activity: "Activity",
  settings: "Settings",
  contact: "Contact",
  legal: "Legal & privacy",
};

export function AppShell() {
  const { state, busy, signOut } = useWorkspace();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const user = state!.auth.user!;
  const online = useMemo(() => state!.presence.filter((member) => member.online), [state]);
  const routeRoot = location.pathname.split("/")[1] || "dashboard";

  const closeNavigation = () => setMobileNavOpen(false);

  return (
    <div className={`application ${mobileNavOpen ? "nav-open" : ""}`}>
      <aside className="app-sidebar">
        <div className="brand-row">
          <span className="brand-symbol">S</span>
          <div><strong>SALAMANDRA</strong><span>EVENT OPERATIONS</span></div>
          <button className="icon-button mobile-only" type="button" onClick={closeNavigation} aria-label="Close navigation"><X size={20} /></button>
        </div>

        <button className="organization-switcher" type="button" onClick={() => navigate("/settings")}>
          <span className="organization-mark">{state!.organization.name?.[0] || "W"}</span>
          <span><strong>{state!.organization.name || "Workspace"}</strong><small>{state!.organization.plan || "Operations"}</small></span>
          <ChevronDown size={17} />
        </button>

        <nav className="primary-nav" aria-label="Main navigation">
          {navigation.map(({ to, label, icon: Icon, divider }) => (
            <NavLink
              key={to}
              className={({ isActive }) => `nav-link ${isActive ? "active" : ""} ${divider ? "nav-divider" : ""}`}
              to={to}
              onClick={closeNavigation}
            >
              <Icon size={19} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-support">
          <CircleHelp size={20} />
          <div><strong>Operations support</strong><span>Help with inventory, event prep, and returns.</span></div>
          <NavLink to="/contact" onClick={closeNavigation}>Contact support</NavLink>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-title">
            <button className="icon-button menu-trigger" type="button" onClick={() => setMobileNavOpen(true)} aria-label="Open navigation"><Menu size={20} /></button>
            <strong>{pageNames[routeRoot] || "Salamandra"}</strong>
          </div>
          <div className="topbar-actions">
            <button className="button button-primary new-event-button" type="button" onClick={() => navigate("/events/new")}>
              <span>New event</span><Plus size={17} />
            </button>
            <button className="team-presence" type="button" onClick={() => navigate("/team")} title="Open team">
              <span className="avatar-stack">
                {online.slice(0, 3).map((member) => <Avatar key={member.id} user={member} size="sm" />)}
              </span>
              <span>{online.length} online</span>
              <span className="online-dot" />
            </button>
            <div className="account-menu">
              <button className="account-trigger" type="button" onClick={() => setAccountOpen((value) => !value)} aria-expanded={accountOpen}>
                <Avatar user={user} />
                <span><strong>{user.name}</strong><small>{user.title}</small></span>
                <ChevronDown size={17} />
              </button>
              {accountOpen ? (
                <div className="account-popover">
                  <button type="button" onClick={() => { setAccountOpen(false); navigate("/settings"); }}><Settings size={16} />Account settings</button>
                  <button type="button" onClick={() => { setAccountOpen(false); void signOut(); }}><LogOut size={16} />Sign out</button>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <main className="route-surface"><Outlet /></main>
      </div>
      <button className="nav-scrim" type="button" aria-label="Close navigation" onClick={closeNavigation} />
      {busy ? <div className="busy-indicator"><Spinner label="Saving changes" />Saving</div> : null}
    </div>
  );
}

