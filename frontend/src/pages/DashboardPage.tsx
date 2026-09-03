import {
  AlertTriangle,
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  PackageCheck,
  PackagePlus,
  RotateCcw,
  Trophy,
  UserPlus,
} from "lucide-react";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Avatar, ConflictState, EmptyState, PageHeader, Readiness, StatusTag } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import {
  allActivity,
  eventCrew,
  eventReadiness,
  formatEventDate,
  formatTimeAgo,
  titleCase,
  todayGreeting,
} from "../lib/format";
import type { InventoryItem } from "../types";

const categoryOrder = ["transport", "pa", "microphone", "mixer", "lighting", "video", "power", "furniture", "barrier", "catering", "display", "cable", "stand", "case", "tool", "accessory", "other"];

function categoryLabel(category: string): string {
  const labels: Record<string, string> = {
    pa: "Speakers & PA",
    microphone: "Microphones",
    mixer: "Mixers & control",
    cable: "Cables",
    power: "Power",
    stand: "Stands",
    case: "Cases",
  };
  return labels[category] || titleCase(category);
}

function inventoryGroups(items: InventoryItem[]) {
  const groups = new Map<string, { total: number; ready: number; unavailable: number }>();
  for (const item of items) {
    const current = groups.get(item.type) || { total: 0, ready: 0, unavailable: 0 };
    current.total += item.count + item.in_use_count;
    current.ready += item.count;
    current.unavailable += item.in_use_count;
    groups.set(item.type, current);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => categoryOrder.indexOf(a) - categoryOrder.indexOf(b))
    .slice(0, 8);
}

function FirstRunDashboard() {
  const { state, mutate } = useWorkspace();
  const navigate = useNavigate();
  const user = state!.auth.user!;
  const showOnboarding = !user.preferences.onboarding_dismissed;

  async function dismissOnboarding() {
    await mutate("/api/account/preferences", {
      ...user.preferences,
      onboarding_dismissed: true,
    }).catch(() => undefined);
  }

  const actions = [
    { title: "Add inventory", copy: "Add equipment manually, from presets, or with a CSV import.", icon: PackagePlus, onClick: () => navigate("/inventory") },
    { title: "Invite team", copy: "Create individual accounts and assign the access each person needs.", icon: UserPlus, onClick: () => navigate("/team") },
    { title: "Create first event", copy: "Describe the job and build a plan against your real stock.", icon: CalendarDays, onClick: () => navigate("/events/new") },
  ];

  return (
    <div className="page dashboard-page first-run-dashboard">
      <header className="first-run-heading">
        <div><h1>Your workspace is ready.</h1><p>Welcome to {state!.organization.name}. Add real stock and plan the first job when you are ready.</p></div>
        {showOnboarding ? <button className="text-button" type="button" onClick={() => void dismissOnboarding()}>Skip setup</button> : null}
      </header>

      {showOnboarding ? (
        <section className="onboarding-panel" aria-labelledby="setup-title">
          <div className="section-title-row"><div><h2 id="setup-title">Set up the workspace</h2><p>Three useful starting points. None are mandatory.</p></div><span className="result-count">0 of 3 complete</span></div>
          <div className="onboarding-actions">
            {actions.map(({ title, copy, icon: Icon, onClick }) => (
              <button key={title} type="button" onClick={onClick}>
                <span><Icon size={21} aria-hidden="true" /></span>
                <span><strong>{title}</strong><small>{copy}</small></span>
                <ChevronRight size={19} aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <div className="first-run-grid">
        <section className="data-section">
          <div className="section-title-row"><div><h2>Event schedule</h2><p>Your confirmed work will appear here.</p></div><button className="button button-secondary" type="button" onClick={() => navigate("/events/new")}>Create event</button></div>
          <EmptyState title="No events yet" message="Describe your first event to create its schedule and inventory plan." action={<button className="button button-primary" type="button" onClick={() => navigate("/events/new")}>Create event</button>} />
        </section>
        <section className="data-section">
          <div className="section-title-row"><div><h2>Inventory readiness</h2><p>Availability will be calculated from real holdings.</p></div><button className="button button-secondary" type="button" onClick={() => navigate("/inventory")}>Add inventory</button></div>
          <EmptyState title="Your inventory is empty" message="Add items manually, start from the catalog, or import a CSV." action={<button className="button button-primary" type="button" onClick={() => navigate("/inventory")}>Add inventory</button>} />
        </section>
      </div>
    </div>
  );
}

export function DashboardPage() {
  const { state } = useWorkspace();
  const navigate = useNavigate();
  const user = state!.auth.user!;
  const isEmptyWorkspace = state!.events.events.length === 0 && state!.inventory.items.length === 0;
  const events = useMemo(
    () => state!.events.events.filter((event) => event.status !== "returned").sort((a, b) => a.start_date.localeCompare(b.start_date)),
    [state],
  );
  const activity = useMemo(() => allActivity(state!.events.events).slice(0, 5), [state]);
  const groups = useMemo(() => inventoryGroups(state!.inventory.items), [state]);
  const conflictCount = events.reduce((sum, event) => sum + (event.plan.total_missing || event.conflicts.length), 0);
  const tracked = events.filter((event) => eventReadiness(event) >= 70).length;
  const readyPercent = events.length ? Math.round((tracked / events.length) * 100) : 100;
  const totalStock = state!.inventory.summary.in_stock + state!.inventory.summary.in_use;
  const inventoryPercent = totalStock ? Math.round((state!.inventory.summary.in_stock / totalStock) * 100) : 100;
  const completedEvents = state!.events.events.filter((event) => event.status === "returned").length;
  const closedChecks = state!.events.events.flatMap((event) => [...event.checklist, ...event.return_checklist]).filter((item) => item.done).length;
  const operationsPoints = completedEvents * 100 + closedChecks * 5 + tracked * 20;
  const operationsLevel = Math.floor(operationsPoints / 250) + 1;
  const levelProgress = operationsPoints % 250;

  if (isEmptyWorkspace) return <FirstRunDashboard />;

  return (
    <div className="page dashboard-page">
      <PageHeader
        title={todayGreeting(user.name)}
        description="Here is what needs attention across events and inventory."
      />

      <section className="metric-strip" aria-label="Operations overview">
        <article><span className="metric-icon teal"><CalendarDays size={22} /></span><div><strong>{events.length}</strong><span>Upcoming events</span><small>{state!.events.learning_count} briefs learned</small></div></article>
        <article><span className="metric-icon blue"><CheckCircle2 size={22} /></span><div><strong>{readyPercent}%</strong><span>Events on track</span><small>{tracked} ready to advance</small></div></article>
        <article><span className="metric-icon amber"><AlertTriangle size={22} /></span><div><strong>{conflictCount}</strong><span>Stock conflicts</span><small>{conflictCount ? "Needs attention" : "No blockers"}</small></div></article>
        <article><span className="metric-icon green"><PackageCheck size={22} /></span><div><strong>{inventoryPercent}%</strong><span>Inventory ready</span><small>{state!.inventory.summary.in_use} items out</small></div></article>
      </section>

      {user.preferences.show_progress ? <section className="operations-progress" aria-label="Operations progress"><span className="progress-mark"><Trophy size={20} /></span><div className="progress-copy"><span><strong>Operations level {operationsLevel}</strong><small>{operationsPoints} points from completed jobs and checklists</small></span><div className="operations-progress-track"><span style={{ width: `${(levelProgress / 250) * 100}%` }} /></div></div><div className="progress-facts"><span><strong>{completedEvents}</strong>jobs returned</span><span><strong>{closedChecks}</strong>checks closed</span><span><strong>{250 - levelProgress}</strong>to next level</span></div></section> : null}

      <section className="data-section upcoming-section">
        <div className="section-title-row"><div><h2>Upcoming events</h2><p>Readiness, crew, and stock position at a glance.</p></div><button className="text-button" type="button" onClick={() => navigate("/events")}>View calendar<ArrowRight size={16} /></button></div>
        {events.length ? (
          <div className="table-scroll">
            <table className="operations-table">
              <thead><tr><th>Date</th><th>Event</th><th>Readiness</th><th>Crew</th><th>Stock conflicts</th><th>Status</th><th aria-label="Open" /></tr></thead>
              <tbody>
                {events.slice(0, 6).map((event) => {
                  const date = formatEventDate(event.start_date);
                  const crew = eventCrew(event, state!.auth.users);
                  const conflicts = event.plan.total_missing || event.conflicts.length;
                  return (
                    <tr key={event.id} className="clickable-row" onClick={() => navigate(`/events?event=${event.id}`)}>
                      <td><span className="date-tile"><strong>{date.day}</strong><small>{date.month}</small></span></td>
                      <td><strong>{event.title}</strong><small>{event.start_time || "Time TBD"} · {event.location || "Location TBD"}</small></td>
                      <td><Readiness value={eventReadiness(event)} /></td>
                      <td><span className="avatar-stack table-avatars">{crew.slice(0, 3).map((member) => <Avatar key={member.id} user={member} size="sm" />)}{crew.length > 3 ? <span className="avatar avatar-sm avatar-fallback">+{crew.length - 3}</span> : null}</span></td>
                      <td><ConflictState count={conflicts} /></td>
                      <td><StatusTag status={event.status} /></td>
                      <td><ArrowRight size={17} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No upcoming events" message="Create an event from a plain-language brief to begin." action={<button className="button button-primary" onClick={() => navigate("/events/new")}>Create event</button>} />}
      </section>

      <div className="dashboard-lower-grid">
        <section className="data-section inventory-health-section">
          <div className="section-title-row"><div><h2>Inventory health</h2><p>Availability across operational categories.</p></div><button className="text-button" type="button" onClick={() => navigate("/inventory")}>View inventory<ArrowRight size={16} /></button></div>
          <div className="health-table">
            <div className="health-row health-head"><span>Category</span><span>Total</span><span>Ready</span><span>Out</span><span>Health</span></div>
            {groups.map(([category, values]) => {
              const health = values.total ? Math.round((values.ready / values.total) * 100) : 100;
              return <div className="health-row" key={category}><strong>{categoryLabel(category)}</strong><span>{values.total}</span><span>{values.ready}</span><span>{values.unavailable}</span><span className="health-score">{health}%<i><i style={{ width: `${health}%` }} /></i></span></div>;
            })}
          </div>
        </section>

        <section className="data-section activity-summary">
          <div className="section-title-row"><div><h2>Recent activity</h2><p>Latest operational changes.</p></div><button className="text-button" type="button" onClick={() => navigate("/activity")}>View all<ArrowRight size={16} /></button></div>
          <div className="activity-list compact-list">
            {activity.map((entry, index) => (
              <div className="activity-entry" key={`${entry.event.id}-${entry.timestamp}-${index}`}>
                <span className={`activity-icon action-${entry.action}`}><RotateCcw size={16} /></span>
                <div><strong>{entry.note}</strong><span>{entry.event.title}</span></div>
                <time>{formatTimeAgo(entry.timestamp)}</time>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
