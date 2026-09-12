import { inventoryLabel } from "../lib/inventoryLabel";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRightLeft,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock3,
  MapPin,
  Pencil,
  Plus,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Truck,
  Trash2,
  UserPlus,
  Users,
  XCircle,
} from "lucide-react";
import { type FormEvent, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Avatar, ConflictState, EmptyState, Modal, PageHeader, Readiness, StatusTag } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { HistoricalSuggestions } from "../components/HistoricalSuggestions";
import { eventCrew, eventReadiness, formatDateLong, formatEventDate, titleCase } from "../lib/format";
import type { EventDraft, EventPlan, EventRecord, PlanLine, StateEnvelope, UserAccount } from "../types";

const weekDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const requirementOptions = [
  ["pa.main", "Main PA", "Audio"],
  ["monitor.stage", "Stage monitor", "Audio"],
  ["microphone.vocal", "Vocal microphone", "Audio"],
  ["di.instrument", "Instrument DI", "Audio"],
  ["lighting.fixture", "Lighting fixture", "Lighting"],
  ["transport.vehicle", "Transport vehicle", "Transport"],
  ["furniture.table", "Table", "Furniture"],
  ["furniture.chair", "Chair", "Furniture"],
  ["power.distribution", "Power distribution", "Power & site"],
  ["site.barrier", "Barrier", "Power & site"],
  ["hospitality.service", "Hospitality service", "Hospitality"],
] as const;
type ManualRequirement = { id: string; capability: string; customCapability: string; amount: number; level: string };

function monthCells(month: Date) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const startOffset = (first.getDay() + 6) % 7;
  const start = new Date(month.getFullYear(), month.getMonth(), 1 - startOffset);
  return Array.from({ length: 42 }, (_, index) => {
    const value = new Date(start);
    value.setDate(start.getDate() + index);
    return value;
  });
}

function isoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function eventAction(event: EventRecord): { status: EventRecord["status"]; label: string } | null {
  if (event.status === "planning") return { status: "confirmed", label: "Confirm event" };
  if (event.status === "confirmed" && event.checklist.every((item) => item.done)) return { status: "packed", label: "Mark packed" };
  if (event.status === "packed") return { status: "out", label: "Dispatch load" };
  if (event.status === "out" && event.return_checklist.every((item) => item.done)) return { status: "returned", label: "Close return" };
  return null;
}

function capabilityName(capability: string): string {
  return titleCase(capability.replaceAll(".", " "));
}

function lineBreakdown(line: PlanLine): string {
  const parts = [];
  if (line.required_amount) parts.push(`${line.required_amount} required`);
  if (line.recommended_amount) parts.push(`${line.recommended_amount} spare`);
  if (line.optional_amount) parts.push(`${line.optional_amount} optional`);
  return parts.join(" + ") || `${line.amount} planned`;
}

function planningFocus(score: number): string {
  if (score >= 75) return "Reliability first";
  if (score >= 55) return "Balanced operation";
  return "Standard operation";
}

function TransportBand({ plan }: { plan: EventPlan }) {
  const transport = plan.transport_summary;
  if (!transport?.vehicle_label) return null;
  return <div className="transport-band"><span className="transport-icon"><Truck size={19} /></span><div><strong>Load & transport</strong><small>{transport.basis}</small></div><span><strong>{transport.vehicle_label}</strong><small>Vehicle</small></span><span><strong>{transport.cart_count} cart{transport.cart_count === 1 ? "" : "s"}</strong><small>Venue movement</small></span><span><strong>{transport.payload_kg} kg</strong><small>{transport.volume_m3} m3 estimated</small></span></div>;
}

function CrewPicker({ users, selected, requiredId, onChange }: { users: UserAccount[]; selected: string[]; requiredId: string; onChange: (ids: string[]) => void }) {
  return (
    <fieldset className="crew-picker">
      <legend><UserPlus size={16} />Event crew</legend>
      <p>Choose everyone who should see this event in their assigned work.</p>
      <div>
        {users.map((member) => (
          <label key={member.id}>
            <input
              type="checkbox"
              checked={selected.includes(member.id)}
              disabled={member.id === requiredId}
              onChange={(event) => onChange(event.target.checked ? [...selected, member.id] : selected.filter((id) => id !== member.id))}
            />
            <Avatar user={member} size="sm" />
            <span><strong>{member.name}</strong><small>{titleCase(member.role)}</small></span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function EventsPage() {
  const { state, mutate } = useWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [month, setMonth] = useState(() => new Date());
  const [description, setDescription] = useState("");
  const manualForm = useRef<HTMLFormElement>(null);
  const [planningMode, setPlanningMode] = useState<"describe" | "manual">("describe");
  const [manualEvent, setManualEventState] = useState({ title: "", start_date: "", start_time: "", location: "", duration_minutes: 240, attendee_count: 0 });
  const [manualRequirements, setManualRequirementsState] = useState<ManualRequirement[]>([{ id: crypto.randomUUID(), capability: "pa.main", customCapability: "", amount: 1, level: "required" }]);
  const draftRevision = useRef(0);
  const [draft, setDraft] = useState<EventDraft | null>(null);
  const [draftDirty, setDraftDirty] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [createKey, setCreateKey] = useState(() => crypto.randomUUID());
  const [editing, setEditing] = useState<EventRecord | null>(null);
  const [editingSaving, setEditingSaving] = useState(false);
  const [overflowDate, setOverflowDate] = useState<string | null>(null);
  const [destructiveAction, setDestructiveAction] = useState<"cancel" | "delete" | null>(null);
  const showComposer = location.pathname.endsWith("/new");
  const selectedEvent = state!.events.events.find((event) => event.id === searchParams.get("event")) || null;
  const calendarEvents = useMemo(() => {
    const grouped = new Map<string, EventRecord[]>();
    for (const event of state!.events.events.filter((item) => item.status !== "cancelled")) grouped.set(event.start_date, [...(grouped.get(event.start_date) || []), event]);
    return grouped;
  }, [state]);
  const cells = useMemo(() => monthCells(month), [month]);
  const activeEvents = useMemo(
    () => state!.events.events.filter((event) => !["returned", "cancelled"].includes(event.status)).sort((a, b) => `${a.start_date}${a.start_time}`.localeCompare(`${b.start_date}${b.start_time}`)),
    [state],
  );

  async function planEvent(event: FormEvent) {
    event.preventDefault();
    const revision = draftRevision.current;
    setPlanning(true);
    try {
      const eventDescription = planningMode === "manual"
        ? `${manualEvent.title}. Manually structured event requirements.`
        : description;
      const response = await mutate<{ draft: EventDraft }>("/api/events/describe", {
        description: eventDescription,
        overrides: planningMode === "manual" ? {
          ...manualEvent,
          planning_mode: "manual",
          capability_requirements: manualRequirements.map(({ capability, customCapability, amount, level }) => ({ capability: capability === "custom.resource" ? customCapability : capability, amount, level })),
          assigned_user_ids: [state!.auth.user!.id],
        } : { assigned_user_ids: [state!.auth.user!.id] },
      });
      if (revision !== draftRevision.current) return;
      setDescription(eventDescription);
      setDraft(response.draft);
      setDraftDirty(false);
      setCreateKey(crypto.randomUUID());
    } catch {
      // The workspace provider reports the API message.
    } finally {
      setPlanning(false);
    }
  }

  function updateDraft(field: "title" | "start_date" | "start_time" | "location" | "attendee_count", value: string | number) {
    draftRevision.current += 1;
    setDraft((current) => current ? { ...current, event: { ...current.event, [field]: value } } : current);
    if (planningMode === "manual") setManualEventState(current => ({ ...current, [field]: value }));
    setDraftDirty(true);
  }

  function markDraftChanged() {
    draftRevision.current += 1;
    if (draft) setDraftDirty(true);
  }

  function setManualRequirements(requirements: ManualRequirement[]) {
    markDraftChanged();
    setManualRequirementsState(requirements);
  }

  function setManualEvent(value: typeof manualEvent) {
    markDraftChanged();
    setManualEventState(value);
    setDraft(current => current ? { ...current, event: { ...current.event, ...value } } : current);
  }

  async function recalculateDraft() {
    if (!draft) return;
    const revision = draftRevision.current;
    setPlanning(true);
    try {
      const response = await mutate<{ draft: EventDraft }>("/api/events/describe", {
        description,
        overrides: {
          title: draft.event.title,
          start_date: draft.event.start_date,
          start_time: draft.event.start_time,
          location: draft.event.location,
          duration_minutes: draft.event.duration_minutes,
          attendee_count: draft.event.attendee_count,
          assigned_user_ids: draft.event.assigned_user_ids,
          ...(planningMode === "manual" ? { planning_mode: "manual", capability_requirements: manualRequirements.map(({ capability, customCapability, amount, level }) => ({ capability: capability === "custom.resource" ? customCapability : capability, amount, level })) } : {}),
        },
      });
      if (revision !== draftRevision.current) return;
      setDraft(response.draft);
      setDraftDirty(false);
    } catch {
      // The workspace provider reports the API message.
    } finally {
      setPlanning(false);
    }
  }

  async function saveDraft() {
    if (!draft || saving || draftDirty || planning) return;
    setSaving(true);
    try {
      await mutate<StateEnvelope & { event: EventRecord }>("/api/events/save", {
        description,
        overrides: {
          title: draft.event.title,
          start_date: draft.event.start_date,
          start_time: draft.event.start_time,
          location: draft.event.location,
          duration_minutes: draft.event.duration_minutes,
          attendee_count: draft.event.attendee_count,
          assigned_user_ids: draft.event.assigned_user_ids,
          ...(planningMode === "manual" ? { planning_mode: "manual", capability_requirements: manualRequirements.map(({ capability, customCapability, amount, level }) => ({ capability: capability === "custom.resource" ? customCapability : capability, amount, level })) } : {}),
        },
        idempotency_key: createKey,
      }, { success: "Event saved to the workspace." });
      setDraft(null);
      setDescription("");
      setDraftDirty(false);
      setCreateKey(crypto.randomUUID());
      navigate("/events");
    } catch {
      // The workspace provider reports the API message.
    } finally {
      setSaving(false);
    }
  }

  function applyHistoricalRequirements(suggestions: { capability: string; amount: number }[]) {
    if (!draft || draftDirty || planning) return;
    const replaced = new Set(suggestions.map(item => item.capability));
    const requirements = draft.event.capability_requirements.filter(item => !replaced.has(item.capability))
      .map(({ capability, amount, level }) => ({ capability, amount, level }));
    requirements.push(...suggestions.map(item => ({ ...item, level: "required" as const })));
    setManualRequirements(requirements.map(item => ({ ...item, id: crypto.randomUUID(), customCapability: item.capability, capability: requirementOptions.some(option => option[0] === item.capability) ? item.capability : "custom.resource" })));
    setManualEvent({ title: draft.event.title, start_date: draft.event.start_date, start_time: draft.event.start_time, location: draft.event.location, duration_minutes: draft.event.duration_minutes, attendee_count: draft.event.attendee_count });
    setPlanningMode("manual");
    setDraftDirty(true);
    setCreateKey(crypto.randomUUID());
    requestAnimationFrame(() => manualForm.current?.querySelector<HTMLInputElement>(".manual-requirement-row input[type=number]")?.focus());
  }

  function beginEdit(event: EventRecord) {
    setEditing({ ...event });
  }

  async function saveEdit(event: FormEvent) {
    event.preventDefault();
    if (!editing || editingSaving) return;
    setEditingSaving(true);
    try {
      await mutate<StateEnvelope & { event: EventRecord }>("/api/events/update", {
        event_id: editing.id,
        version: editing.version,
        description: editing.description,
        overrides: {
          title: editing.title,
          start_date: editing.start_date,
          start_time: editing.start_time,
          location: editing.location,
          duration_minutes: editing.duration_minutes,
          attendee_count: editing.attendee_count,
          assigned_user_ids: editing.assigned_user_ids,
        },
      }, { success: "Event details and plan updated." });
      setEditing(null);
    } catch {
      // The workspace provider reports the API message.
    } finally {
      setEditingSaving(false);
    }
  }

  async function toggleChecklist(eventId: string, phase: "pack" | "return", itemId: string, done: boolean) {
    await mutate("/api/events/checklist", { event_id: eventId, phase, item_id: itemId, done }, { success: "Checklist updated." }).catch(() => undefined);
  }

  async function changeStatus(eventId: string, status: EventRecord["status"]) {
    await mutate("/api/events/status", { event_id: eventId, status }, { success: `Event marked ${titleCase(status).toLowerCase()}.` }).catch(() => undefined);
  }

  async function confirmDestructiveAction() {
    if (!selectedEvent || !destructiveAction) return;
    const endpoint = destructiveAction === "cancel" ? "/api/events/cancel" : "/api/events/delete";
    await mutate(endpoint, { event_id: selectedEvent.id, confirm: true }, {
      success: destructiveAction === "cancel" ? "Event cancelled and reservations released." : "Draft event permanently deleted.",
    });
    setDestructiveAction(null);
    setSearchParams({});
  }

  return (
    <div className="page events-page">
      <PageHeader
        title="Events"
        description="Plan, schedule, assign, pack, dispatch, and close every job."
        actions={!showComposer ? <button className="button button-primary" onClick={() => navigate("/events/new")}><Plus size={17} />New event</button> : undefined}
      />

      {showComposer ? (
        <section className="event-composer">
          <button className="back-link" type="button" onClick={() => navigate("/events")}><ArrowLeft size={17} />Back to events</button>
          <div className="composer-grid">
            <form ref={manualForm} className="brief-editor" onSubmit={planEvent} onChange={markDraftChanged}>
              <div className="brief-title"><span>{planningMode === "describe" ? <Sparkles size={20} /> : <SlidersHorizontal size={20} />}</span><div><h2>{planningMode === "describe" ? "Describe the event" : "Build manually"}</h2><p>{planningMode === "describe" ? "Write it the way the brief reaches you. Salamandra will use only stock in this workspace." : "Enter the schedule and operational needs directly. Inventory is still allocated by the same planner."}</p></div></div>
              <div className="segmented-control composer-mode" aria-label="Event creation method"><button type="button" className={planningMode === "describe" ? "active" : ""} onClick={() => { markDraftChanged(); setPlanningMode("describe"); setDraft(null); }}>Describe</button><button type="button" className={planningMode === "manual" ? "active" : ""} onClick={() => { markDraftChanged(); setPlanningMode("manual"); setDraft(null); }}>Build manually</button></div>
              {planningMode === "describe" ? <><label className="field-label" htmlFor="event-brief">Event brief</label><textarea dir="auto" id="event-brief" value={description} onChange={(event) => { setDescription(event.target.value); if (draft) setDraftDirty(true); }} rows={8} placeholder="Conference for 120 guests on 2026-09-12 at 18:00, speeches, panel microphones, stage lighting, and power at Main Hall." required /><div className="example-briefs"><span>Include:</span><span>date and time</span><span>venue</span><span>guest count</span><span>equipment, site, or transport needs</span></div></> : <div className="manual-builder"><div className="form-grid"><label className="form-field-wide">Event name<input dir="auto" value={manualEvent.title} onChange={(event) => setManualEvent({ ...manualEvent, title: event.target.value })} required /></label><label>Date<input type="date" value={manualEvent.start_date} onChange={(event) => setManualEvent({ ...manualEvent, start_date: event.target.value })} required /></label><label>Start time<input type="time" value={manualEvent.start_time} onChange={(event) => setManualEvent({ ...manualEvent, start_time: event.target.value })} required /></label><label>Duration (minutes)<input type="number" min="15" max="10080" value={manualEvent.duration_minutes} onChange={(event) => setManualEvent({ ...manualEvent, duration_minutes: Number(event.target.value) })} required /></label><label>Guests<input type="number" min="0" max="1000000" value={manualEvent.attendee_count || ""} onChange={(event) => setManualEvent({ ...manualEvent, attendee_count: Number(event.target.value || 0) })} /></label><label className="form-field-wide">Venue<input dir="auto" value={manualEvent.location} onChange={(event) => setManualEvent({ ...manualEvent, location: event.target.value })} /></label></div><div className="manual-requirements"><div className="subsection-title"><h3>Requirements</h3><button type="button" className="button button-secondary button-compact" onClick={() => setManualRequirements([...manualRequirements, { id: crypto.randomUUID(), capability: "pa.main", customCapability: "", amount: 1, level: "required" }])}><Plus size={15} />Add</button></div>{manualRequirements.map((requirement) => <div className="manual-requirement-row" key={requirement.id}><label>Department & item<select value={requirement.capability} onChange={(event) => setManualRequirements(manualRequirements.map((item) => item.id === requirement.id ? { ...item, capability: event.target.value } : item))}>{requirementOptions.map(([value, label, group]) => <option key={value} value={value}>{group} · {label}</option>)}<option value="custom.resource">Custom requirement</option></select></label>{requirement.capability === "custom.resource" ? <label>Custom capability<input value={requirement.customCapability} placeholder="example: catering.coffee" onChange={(event) => setManualRequirements(manualRequirements.map((item) => item.id === requirement.id ? { ...item, customCapability: event.target.value } : item))} required /></label> : null}<label>Quantity<input type="number" min="1" max="10000" value={requirement.amount} onChange={(event) => setManualRequirements(manualRequirements.map((item) => item.id === requirement.id ? { ...item, amount: Number(event.target.value) } : item))} required /></label><label>Priority<select value={requirement.level} onChange={(event) => setManualRequirements(manualRequirements.map((item) => item.id === requirement.id ? { ...item, level: event.target.value } : item))}><option value="required">Required</option><option value="recommended">Recommended</option><option value="optional">Optional</option></select></label><button type="button" className="icon-button" aria-label="Remove requirement" title="Remove requirement" disabled={manualRequirements.length === 1} onClick={() => setManualRequirements(manualRequirements.filter((item) => item.id !== requirement.id))}><Trash2 size={16} /></button></div>)}</div></div>}
              <button className="button button-primary button-large" type="submit" disabled={planning}>{planning ? "Building plan..." : "Build event plan"}<ChevronRight size={18} /></button>
            </form>

            <section className="generated-plan" aria-live="polite">
              {draft ? (
                <>
                  <div className="generated-plan-head"><div><span>Operations plan</span><h2>{draft.event.title}</h2><p><CalendarDays size={15} />{formatDateLong(draft.event.start_date)} at {draft.event.start_time}</p><p><MapPin size={15} />{draft.event.location || "Location not detected"}</p></div><StatusTag status={draft.event.plan.is_ready ? "confirmed" : "planning"} /></div>
                  <div className="event-facts"><span><small>Scale</small><strong>{titleCase(draft.event.event_size)}</strong></span><span><small>Guests</small><strong>{draft.event.attendee_count || "Not stated"}</strong></span><span><small>Planning focus</small><strong>{planningFocus(draft.event.priority_score)}</strong></span><span><small>Venue</small><strong>{draft.event.venue_kind ? titleCase(draft.event.venue_kind.replaceAll("_", " ")) : "Not stated"}</strong></span></div>
                  <div className="draft-fields"><label>Event name<input value={draft.event.title} onChange={(event) => updateDraft("title", event.target.value)} /></label><label>Date<input type="date" value={draft.event.start_date} onChange={(event) => updateDraft("start_date", event.target.value)} /></label><label>Start<input type="time" value={draft.event.start_time} onChange={(event) => updateDraft("start_time", event.target.value)} /></label><label>Guests<input type="number" min="0" value={draft.event.attendee_count || ""} onChange={(event) => updateDraft("attendee_count", Number(event.target.value || 0))} /></label><label className="draft-field-wide">Venue<input value={draft.event.location} onChange={(event) => updateDraft("location", event.target.value)} /></label></div>
                  <CrewPicker users={state!.auth.users} selected={draft.event.assigned_user_ids} requiredId={state!.auth.user!.id} onChange={(assigned_user_ids) => setDraft({ ...draft, event: { ...draft.event, assigned_user_ids } })} />
                  {draftDirty ? <div className="recalculate-strip"><AlertTriangle size={16} /><span>Plan details changed. Recalculate availability before saving.</span><button className="button button-secondary button-compact" type="button" onClick={() => void recalculateDraft()} disabled={planning}><RotateCcw size={15} />Recalculate</button></div> : null}
                  {draft.event.milestones.length ? <div className="run-of-show"><span>Run of show</span><div>{draft.event.milestones.map((milestone) => <div key={`${milestone.time}-${milestone.label}`}><strong>{milestone.time}</strong><span>{milestone.label}</span></div>)}</div></div> : null}
                  <div className="plan-summary"><div><span>Allocated lines</span><strong>{draft.event.plan.lines.filter((line) => line.item_id).length}</strong></div><div><span>Required missing</span><strong>{draft.event.plan.total_missing}</strong></div><div><span>Spare missing</span><strong>{draft.event.plan.recommended_missing}</strong></div></div>
                  <TransportBand plan={draft.event.plan} />
                  {!draftDirty && !planning ? <HistoricalSuggestions event={draft.event} onApply={applyHistoricalRequirements} /> : null}
                  {draft.event.plan.reallocations.length ? <div className="reallocation-list"><div className="subsection-title"><h3><ArrowRightLeft size={16} />Overlap reallocation</h3><span>{draft.event.plan.reallocations.length} event affected</span></div>{draft.event.plan.reallocations.map((reallocation) => <div className="reallocation-row" key={reallocation.event_id}><strong>{reallocation.event_title}{reallocation.required_missing ? ` · ${reallocation.required_missing} now missing` : ""}</strong><span>{Object.entries(reallocation.removed).map(([item, amount]) => `${amount}x ${titleCase(item)}`).join(", ") || "Previous allocation"}</span><ArrowRightLeft size={15} /><span>{Object.entries(reallocation.added).map(([item, amount]) => `${amount}x ${titleCase(item)}`).join(", ") || "Rebalanced stock"}</span><small>{reallocation.reason}</small></div>)}</div> : null}
                  <div className="plan-lines">
                    {draft.event.plan.lines.map((line, index) => {
                      const name = line.item_id ? inventoryLabel(state!.inventory.items.find(candidate => candidate.id === line.item_id)) : capabilityName(line.capability);
                      const details = line.reasons.join(" · ") || (line.source === "event" ? "From the event brief" : `Required by ${inventoryLabel(state!.inventory.items.find(candidate => candidate.id === line.source))}`);
                      const missing = line.required_missing || line.recommended_missing || line.optional_missing;
                      return <div className={`plan-line level-${line.level}`} key={`${line.level}-${line.capability}-${line.item_id}-${index}`}><span><strong>{line.amount}x {name}</strong><small className="line-breakdown">{lineBreakdown(line)}</small><small title={details}>{line.item_id ? details : "No matching inventory item is available"}</small>{line.alternatives.length ? <small className="alternative-copy">Other suitable stock: {line.alternatives.slice(0, 2).map((item) => inventoryLabel(state!.inventory.items.find(candidate => candidate.id === item.item_id))).join(", ")}</small> : null}</span><span><em>{line.required_amount && line.recommended_amount ? "required + spare" : line.level}</em><b className={missing ? "line-missing" : "line-ready"}>{missing ? `${missing} missing` : line.substitution ? "Substitute" : "Allocated"}</b></span></div>;
                    })}
                  </div>
                  {draft.event.plan.total_missing ? <div className="planning-warning"><AlertTriangle size={17} /><span><strong>Required stock is still missing</strong><small>The event can be saved in planning so the team can resolve rentals or inventory changes.</small></span></div> : null}
                  <div className="composer-actions"><button className="button button-secondary" type="button" onClick={() => { markDraftChanged(); setDraft(null); setDraftDirty(false); setCreateKey(crypto.randomUUID()); }}>Revise brief</button><button className="button button-primary" type="button" onClick={() => void saveDraft()} disabled={draftDirty || saving || planning}>{saving ? "Saving event..." : draft.event.plan.total_missing ? "Save as planning" : "Save event"}</button></div>
                </>
              ) : <EmptyState title="Your operations plan will appear here" message="Salamandra detects the schedule, venue, equipment, site needs, transport, linked requirements, and stock conflicts from the brief." />}
            </section>
          </div>
        </section>
      ) : (
        <>
          <section className="calendar-section data-section">
            <div className="calendar-toolbar"><div><h2>{new Intl.DateTimeFormat("en", { month: "long", year: "numeric" }).format(month)}</h2><p>{activeEvents.length} active events</p></div><div><button className="icon-button" title="Previous month" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft size={18} /></button><button className="button button-secondary button-compact" onClick={() => setMonth(new Date())}>Today</button><button className="icon-button" title="Next month" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight size={18} /></button></div></div>
            <div className="calendar-scroll" role="region" aria-label="Event calendar" tabIndex={0}>
            <div className="calendar-grid calendar-weekdays">{weekDays.map((day) => <span key={day}>{day}</span>)}</div>
            <div className="calendar-grid">
              {cells.map((date) => {
                const key = isoDate(date);
                const dayEvents = calendarEvents.get(key) || [];
                const muted = date.getMonth() !== month.getMonth();
                const today = key === isoDate(new Date());
                return <div className={`calendar-cell ${muted ? "muted" : ""} ${today ? "today" : ""}`} key={key}><span>{date.getDate()}</span>{dayEvents.slice(0, 2).map((event) => <button type="button" className={`calendar-event status-border-${event.status}`} key={event.id} onClick={() => setSearchParams({ event: event.id })}><strong>{event.start_time}</strong>{event.title}</button>)}{dayEvents.length > 2 ? <button type="button" className="calendar-more" onClick={() => setOverflowDate(key)} aria-label={`Show ${dayEvents.length - 2} more events on ${formatDateLong(key)}`}>+{dayEvents.length - 2} more</button> : null}</div>;
              })}
            </div>
            </div>
          </section>

          <section className="data-section event-list-section">
            <div className="section-title-row"><div><h2>Event schedule</h2><p>Open an event to work through its current stage.</p></div><span className="result-count">{activeEvents.length} active</span></div>
            {activeEvents.length ? <div className="event-list">{activeEvents.map((event) => {
              const date = formatEventDate(event.start_date);
              const crew = eventCrew(event, state!.auth.users);
              return <button className="event-list-row" type="button" key={event.id} onClick={() => setSearchParams({ event: event.id })}><span className="date-tile"><strong>{date.day}</strong><small>{date.month}</small></span><span className="event-list-title"><strong>{event.title}</strong><small><Clock3 size={14} />{event.start_time}<MapPin size={14} />{event.location || "Location TBD"}</small></span><Readiness value={eventReadiness(event)} /><span className="avatar-stack table-avatars">{crew.slice(0, 3).map((member) => <Avatar key={member.id} user={member} size="sm" />)}</span><ConflictState count={event.plan.total_missing || event.conflicts.length} /><StatusTag status={event.status} /><ChevronRight size={18} /></button>;
            })}</div> : <EmptyState title="No events yet" message="Describe your first event to create its schedule and inventory plan." action={<button className="button button-primary" type="button" onClick={() => navigate("/events/new")}>Create event</button>} />}
          </section>
        </>
      )}

      <Modal open={Boolean(selectedEvent)} suspended={Boolean(destructiveAction)} title={editing ? "Edit event" : selectedEvent?.title || "Event"} description={selectedEvent ? `${formatDateLong(selectedEvent.start_date)} · ${selectedEvent.start_time} · ${selectedEvent.location || "Location TBD"}` : undefined} onClose={() => { setEditing(null); setSearchParams({}); }} size="lg">
        {selectedEvent && editing ? (
          <form className="event-edit-form" onSubmit={saveEdit}>
            <p className="event-edit-intro">Update the brief or schedule. Salamandra will rebuild the inventory plan from current workspace stock when you save.</p>
            <div className="form-grid event-edit-grid">
              <label className="form-field-wide">Event name<input dir="auto" value={editing.title} onChange={(event) => setEditing({ ...editing, title: event.target.value })} required /></label>
              <label>Date<input type="date" value={editing.start_date} onChange={(event) => setEditing({ ...editing, start_date: event.target.value })} required /></label>
              <label>Start time<input type="time" value={editing.start_time} onChange={(event) => setEditing({ ...editing, start_time: event.target.value })} required /></label>
              <label>Duration (minutes)<input type="number" min="15" max="10080" value={editing.duration_minutes} onChange={(event) => setEditing({ ...editing, duration_minutes: Number(event.target.value) })} required /></label>
              <label>Guests<input type="number" min="0" max="1000000" value={editing.attendee_count} onChange={(event) => setEditing({ ...editing, attendee_count: Number(event.target.value) })} /></label>
              <label className="form-field-wide">Venue<input dir="auto" value={editing.location} onChange={(event) => setEditing({ ...editing, location: event.target.value })} /></label>
              <label className="form-field-wide">Event brief<textarea dir="auto" rows={7} value={editing.description} onChange={(event) => setEditing({ ...editing, description: event.target.value })} required /></label>
            </div>
            <CrewPicker users={state!.auth.users} selected={editing.assigned_user_ids} requiredId={editing.owner_id} onChange={(assigned_user_ids) => setEditing({ ...editing, assigned_user_ids })} />
            <div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => setEditing(null)}>Cancel</button><button className="button button-primary" type="submit" disabled={editingSaving}>{editingSaving ? "Saving changes..." : "Save and rebuild plan"}</button></div>
          </form>
        ) : selectedEvent ? (
          <div className="event-detail">
            <div className="event-detail-summary"><div><StatusTag status={selectedEvent.status} /><Readiness value={eventReadiness(selectedEvent)} /></div><div><Users size={17} />{eventCrew(selectedEvent, state!.auth.users).map((member) => member.name).join(", ") || "No crew assigned"}</div></div>
            <p className="event-description">{selectedEvent.description}</p>
            <TransportBand plan={selectedEvent.plan} />
            <div className="event-detail-grid">
              <section><div className="subsection-title"><h3>{selectedEvent.status === "out" ? "Return checklist" : "Packing checklist"}</h3><span>{(selectedEvent.status === "out" ? selectedEvent.return_checklist : selectedEvent.checklist).filter((item) => item.done).length}/{(selectedEvent.status === "out" ? selectedEvent.return_checklist : selectedEvent.checklist).length}</span></div><div className="checklist-list">{(selectedEvent.status === "out" ? selectedEvent.return_checklist : selectedEvent.checklist).map((item) => <label key={item.id}><input type="checkbox" checked={item.done} onChange={(event) => void toggleChecklist(selectedEvent.id, item.phase, item.item_id, event.target.checked)} /><span><strong>{item.amount}x {inventoryLabel(state!.inventory.items.find(candidate => candidate.id === item.item_id))}</strong><small>{item.phase === "return" ? "Inspect and return to stock" : "Pack and verify"}</small></span><CheckCircle2 size={18} /></label>)}</div></section>
              <section><div className="subsection-title"><h3>Operations plan</h3><span>{selectedEvent.plan.lines.length} lines</span></div><div className="detail-gear-list">{selectedEvent.plan.lines.map((line, index) => <div key={`${line.level}-${line.capability}-${line.item_id}-${index}`}><span><strong>{line.amount}x {line.item_id ? inventoryLabel(state!.inventory.items.find(candidate => candidate.id === line.item_id)) : capabilityName(line.capability)}</strong><small>{lineBreakdown(line)} · {titleCase(line.type || line.capability)}</small></span><span className={line.missing ? "line-missing" : "line-ready"}>{line.missing ? `${line.missing} missing` : "Ready"}</span></div>)}</div></section>
            </div>
            {selectedEvent.status !== "planning" ? <p className="event-edit-lock-note"><AlertTriangle size={16} />Editing is locked after confirmation to protect reservations and inventory movement history.</p> : null}
            <div className="modal-actions"><button className="button button-secondary" onClick={() => setSearchParams({})}>Close</button>{selectedEvent.status === "planning" && ["owner", "admin"].includes(state!.auth.user!.role) ? <button className="button button-danger" type="button" onClick={() => setDestructiveAction("delete")}><Trash2 size={16} />Delete draft</button> : null}{["planning", "confirmed", "packed"].includes(selectedEvent.status) ? <button className="button button-secondary" type="button" onClick={() => setDestructiveAction("cancel")}><XCircle size={16} />Cancel event</button> : null}{selectedEvent.status === "planning" ? <button className="button button-secondary" type="button" onClick={() => beginEdit(selectedEvent)}><Pencil size={16} />Edit event</button> : null}{eventAction(selectedEvent) ? <button className="button button-primary" onClick={() => void changeStatus(selectedEvent.id, eventAction(selectedEvent)!.status)}>{eventAction(selectedEvent)!.label}</button> : null}</div>
          </div>
        ) : null}
      </Modal>
      <Modal open={Boolean(overflowDate)} title={overflowDate ? `Events on ${formatDateLong(overflowDate)}` : "Events"} onClose={() => setOverflowDate(null)}>
        <div className="calendar-overflow-list">
          {(overflowDate ? calendarEvents.get(overflowDate) || [] : []).map((event) => <button type="button" key={event.id} onClick={() => { setOverflowDate(null); setSearchParams({ event: event.id }); }}><span><strong>{event.start_time}</strong><small>{event.location || "Location TBD"}</small></span><b>{event.title}</b><StatusTag status={event.status} /></button>)}
        </div>
      </Modal>
      <Modal open={Boolean(destructiveAction)} title={destructiveAction === "delete" ? "Delete this draft?" : "Cancel this event?"} description={destructiveAction === "delete" ? "This permanently removes an unconfirmed draft. This cannot be undone." : "The event will move to history and any reserved inventory will be released."} onClose={() => setDestructiveAction(null)}>
        <div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => setDestructiveAction(null)}>Keep event</button><button className="button button-danger" type="button" onClick={() => void confirmDestructiveAction()}>{destructiveAction === "delete" ? "Delete permanently" : "Cancel event"}</button></div>
      </Modal>
    </div>
  );
}
