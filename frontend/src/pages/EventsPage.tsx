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
  Plus,
  RotateCcw,
  Sparkles,
  Truck,
  Users,
} from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Avatar, ConflictState, EmptyState, Modal, PageHeader, Readiness, StatusTag } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { eventCrew, eventReadiness, formatDateLong, formatEventDate, titleCase } from "../lib/format";
import type { EventDraft, EventPlan, EventRecord, PlanLine, StateEnvelope } from "../types";

const weekDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

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

function TransportBand({ plan }: { plan: EventPlan }) {
  const transport = plan.transport_summary;
  if (!transport?.vehicle_label) return null;
  return <div className="transport-band"><span className="transport-icon"><Truck size={19} /></span><div><strong>Load & transport</strong><small>{transport.basis}</small></div><span><strong>{transport.vehicle_label}</strong><small>Vehicle</small></span><span><strong>{transport.cart_count} cart{transport.cart_count === 1 ? "" : "s"}</strong><small>Venue movement</small></span><span><strong>{transport.payload_kg} kg</strong><small>{transport.volume_m3} m3 estimated</small></span></div>;
}

export function EventsPage() {
  const { state, mutate } = useWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [month, setMonth] = useState(() => new Date());
  const [description, setDescription] = useState("");
  const [draft, setDraft] = useState<EventDraft | null>(null);
  const [draftDirty, setDraftDirty] = useState(false);
  const [planning, setPlanning] = useState(false);
  const showComposer = location.pathname.endsWith("/new");
  const selectedEvent = state!.events.events.find((event) => event.id === searchParams.get("event")) || null;
  const calendarEvents = useMemo(() => {
    const grouped = new Map<string, EventRecord[]>();
    for (const event of state!.events.events) grouped.set(event.start_date, [...(grouped.get(event.start_date) || []), event]);
    return grouped;
  }, [state]);
  const cells = useMemo(() => monthCells(month), [month]);
  const activeEvents = useMemo(
    () => state!.events.events.filter((event) => event.status !== "returned").sort((a, b) => `${a.start_date}${a.start_time}`.localeCompare(`${b.start_date}${b.start_time}`)),
    [state],
  );

  async function planEvent(event: FormEvent) {
    event.preventDefault();
    setPlanning(true);
    try {
      const response = await mutate<{ draft: EventDraft }>("/api/events/describe", { description });
      setDraft(response.draft);
      setDraftDirty(false);
    } catch {
      // The workspace provider reports the API message.
    } finally {
      setPlanning(false);
    }
  }

  function updateDraft(field: "title" | "start_date" | "start_time" | "location", value: string) {
    setDraft((current) => current ? { ...current, event: { ...current.event, [field]: value } } : current);
    setDraftDirty(true);
  }

  async function recalculateDraft() {
    if (!draft) return;
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
        },
      });
      setDraft(response.draft);
      setDraftDirty(false);
    } catch {
      // The workspace provider reports the API message.
    } finally {
      setPlanning(false);
    }
  }

  async function saveDraft() {
    if (!draft) return;
    try {
      await mutate<StateEnvelope & { event: EventRecord }>("/api/events/save", {
        description,
        overrides: {
          title: draft.event.title,
          start_date: draft.event.start_date,
          start_time: draft.event.start_time,
          location: draft.event.location,
          duration_minutes: draft.event.duration_minutes,
        },
      }, { success: "Event saved to the workspace." });
      setDraft(null);
      setDescription("");
      setDraftDirty(false);
      navigate("/events");
    } catch {
      // The workspace provider reports the API message.
    }
  }

  async function toggleChecklist(eventId: string, phase: "pack" | "return", itemId: string, done: boolean) {
    await mutate("/api/events/checklist", { event_id: eventId, phase, item_id: itemId, done }, { success: "Checklist updated." }).catch(() => undefined);
  }

  async function changeStatus(eventId: string, status: EventRecord["status"]) {
    await mutate("/api/events/status", { event_id: eventId, status }, { success: `Event marked ${titleCase(status).toLowerCase()}.` }).catch(() => undefined);
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
            <form className="brief-editor" onSubmit={planEvent}>
              <div className="brief-title"><span><Sparkles size={20} /></span><div><h2>Describe the event</h2><p>Write it the way the brief reaches you. Salamandra will use only stock in this workspace.</p></div></div>
              <label className="field-label" htmlFor="event-brief">Event brief</label>
              <textarea id="event-brief" value={description} onChange={(event) => setDescription(event.target.value)} rows={8} placeholder="Conference for 120 guests on 2026-09-12 at 18:00, speeches, panel microphones, stage lighting, and power at Main Hall." required />
              <div className="example-briefs"><span>Include:</span><span>date and time</span><span>venue</span><span>guest count</span><span>equipment, site, or transport needs</span></div>
              <button className="button button-primary button-large" type="submit" disabled={planning}>{planning ? "Building plan..." : "Build event plan"}<ChevronRight size={18} /></button>
            </form>

            <section className="generated-plan" aria-live="polite">
              {draft ? (
                <>
                  <div className="generated-plan-head"><div><span>Operations plan</span><h2>{draft.event.title}</h2><p><CalendarDays size={15} />{formatDateLong(draft.event.start_date)} at {draft.event.start_time}</p><p><MapPin size={15} />{draft.event.location || "Location not detected"}</p></div><StatusTag status={draft.event.plan.is_ready ? "confirmed" : "planning"} /></div>
                  <div className="event-facts"><span><small>Scale</small><strong>{titleCase(draft.event.event_size)}</strong></span><span><small>Guests</small><strong>{draft.event.attendee_count || "Not stated"}</strong></span><span><small>Priority</small><strong>{draft.event.priority_score}/100</strong></span><span><small>Venue</small><strong>{draft.event.venue_kind ? titleCase(draft.event.venue_kind.replaceAll("_", " ")) : "Not stated"}</strong></span></div>
                  <div className="draft-fields"><label>Event name<input value={draft.event.title} onChange={(event) => updateDraft("title", event.target.value)} /></label><label>Date<input type="date" value={draft.event.start_date} onChange={(event) => updateDraft("start_date", event.target.value)} /></label><label>Start<input type="time" value={draft.event.start_time} onChange={(event) => updateDraft("start_time", event.target.value)} /></label><label>Venue<input value={draft.event.location} onChange={(event) => updateDraft("location", event.target.value)} /></label></div>
                  {draftDirty ? <div className="recalculate-strip"><AlertTriangle size={16} /><span>Schedule details changed. Recalculate availability before saving.</span><button className="button button-secondary button-compact" type="button" onClick={() => void recalculateDraft()} disabled={planning}><RotateCcw size={15} />Recalculate</button></div> : null}
                  {draft.event.milestones.length ? <div className="run-of-show"><span>Run of show</span><div>{draft.event.milestones.map((milestone) => <div key={`${milestone.time}-${milestone.label}`}><strong>{milestone.time}</strong><span>{milestone.label}</span></div>)}</div></div> : null}
                  <div className="plan-summary"><div><span>Allocated lines</span><strong>{draft.event.plan.lines.filter((line) => line.item_id).length}</strong></div><div><span>Required missing</span><strong>{draft.event.plan.total_missing}</strong></div><div><span>Spare missing</span><strong>{draft.event.plan.recommended_missing}</strong></div></div>
                  <TransportBand plan={draft.event.plan} />
                  {draft.event.plan.reallocations.length ? <div className="reallocation-list"><div className="subsection-title"><h3><ArrowRightLeft size={16} />Overlap reallocation</h3><span>{draft.event.plan.reallocations.length} event affected</span></div>{draft.event.plan.reallocations.map((reallocation) => <div className="reallocation-row" key={reallocation.event_id}><strong>{reallocation.event_title}{reallocation.required_missing ? ` · ${reallocation.required_missing} now missing` : ""}</strong><span>{Object.entries(reallocation.removed).map(([item, amount]) => `${amount}x ${titleCase(item)}`).join(", ") || "Previous allocation"}</span><ArrowRightLeft size={15} /><span>{Object.entries(reallocation.added).map(([item, amount]) => `${amount}x ${titleCase(item)}`).join(", ") || "Rebalanced stock"}</span><small>{reallocation.reason}</small></div>)}</div> : null}
                  <div className="plan-lines">
                    {draft.event.plan.lines.map((line, index) => {
                      const name = line.item_id ? titleCase(line.item_id) : capabilityName(line.capability);
                      const details = line.reasons.join(" · ") || (line.source === "event" ? "From the event brief" : `Required by ${titleCase(line.source)}`);
                      const missing = line.required_missing || line.recommended_missing || line.optional_missing;
                      return <div className={`plan-line level-${line.level}`} key={`${line.level}-${line.capability}-${line.item_id}-${index}`}><span><strong>{line.amount}x {name}</strong><small className="line-breakdown">{lineBreakdown(line)}</small><small title={details}>{line.item_id ? details : "No matching inventory item is available"}</small>{line.alternatives.length ? <small className="alternative-copy">Alternatives: {line.alternatives.slice(0, 2).map((item) => `${titleCase(item.item_id)} (${Math.round(item.score)})`).join(", ")}</small> : null}</span><span><em>{line.required_amount && line.recommended_amount ? "required + spare" : line.level}</em><b className={missing ? "line-missing" : "line-ready"}>{missing ? `${missing} missing` : line.substitution ? "Substitute" : "Allocated"}</b></span></div>;
                    })}
                  </div>
                  {draft.event.plan.total_missing ? <div className="planning-warning"><AlertTriangle size={17} /><span><strong>Required stock is still missing</strong><small>The event can be saved in planning so the team can resolve rentals or inventory changes.</small></span></div> : null}
                  <div className="composer-actions"><button className="button button-secondary" type="button" onClick={() => { setDraft(null); setDraftDirty(false); }}>Revise brief</button><button className="button button-primary" type="button" onClick={() => void saveDraft()} disabled={draftDirty}>{draft.event.plan.total_missing ? "Save as planning" : "Save event"}</button></div>
                </>
              ) : <EmptyState title="Your operations plan will appear here" message="Salamandra detects the schedule, venue, equipment, site needs, transport, linked requirements, and stock conflicts from the brief." />}
            </section>
          </div>
        </section>
      ) : (
        <>
          <section className="calendar-section data-section">
            <div className="calendar-toolbar"><div><h2>{new Intl.DateTimeFormat("en", { month: "long", year: "numeric" }).format(month)}</h2><p>{activeEvents.length} active events</p></div><div><button className="icon-button" title="Previous month" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft size={18} /></button><button className="button button-secondary button-compact" onClick={() => setMonth(new Date())}>Today</button><button className="icon-button" title="Next month" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight size={18} /></button></div></div>
            <div className="calendar-grid calendar-weekdays">{weekDays.map((day) => <span key={day}>{day}</span>)}</div>
            <div className="calendar-grid">
              {cells.map((date) => {
                const key = isoDate(date);
                const dayEvents = calendarEvents.get(key) || [];
                const muted = date.getMonth() !== month.getMonth();
                const today = key === isoDate(new Date());
                return <div className={`calendar-cell ${muted ? "muted" : ""} ${today ? "today" : ""}`} key={key}><span>{date.getDate()}</span>{dayEvents.slice(0, 2).map((event) => <button type="button" className={`calendar-event status-border-${event.status}`} key={event.id} onClick={() => setSearchParams({ event: event.id })}><strong>{event.start_time}</strong>{event.title}</button>)}{dayEvents.length > 2 ? <small>+{dayEvents.length - 2} more</small> : null}</div>;
              })}
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

      <Modal open={Boolean(selectedEvent)} title={selectedEvent?.title || "Event"} description={selectedEvent ? `${formatDateLong(selectedEvent.start_date)} · ${selectedEvent.start_time} · ${selectedEvent.location || "Location TBD"}` : undefined} onClose={() => setSearchParams({})} size="lg">
        {selectedEvent ? (
          <div className="event-detail">
            <div className="event-detail-summary"><div><StatusTag status={selectedEvent.status} /><Readiness value={eventReadiness(selectedEvent)} /></div><div><Users size={17} />{eventCrew(selectedEvent, state!.auth.users).map((member) => member.name).join(", ") || "No crew assigned"}</div></div>
            <p className="event-description">{selectedEvent.description}</p>
            <TransportBand plan={selectedEvent.plan} />
            <div className="event-detail-grid">
              <section><div className="subsection-title"><h3>{selectedEvent.status === "out" ? "Return checklist" : "Packing checklist"}</h3><span>{(selectedEvent.status === "out" ? selectedEvent.return_checklist : selectedEvent.checklist).filter((item) => item.done).length}/{(selectedEvent.status === "out" ? selectedEvent.return_checklist : selectedEvent.checklist).length}</span></div><div className="checklist-list">{(selectedEvent.status === "out" ? selectedEvent.return_checklist : selectedEvent.checklist).map((item) => <label key={item.id}><input type="checkbox" checked={item.done} onChange={(event) => void toggleChecklist(selectedEvent.id, item.phase, item.item_id, event.target.checked)} /><span><strong>{item.amount}x {titleCase(item.item_id)}</strong><small>{item.phase === "return" ? "Inspect and return to stock" : "Pack and verify"}</small></span><CheckCircle2 size={18} /></label>)}</div></section>
              <section><div className="subsection-title"><h3>Operations plan</h3><span>{selectedEvent.plan.lines.length} lines</span></div><div className="detail-gear-list">{selectedEvent.plan.lines.map((line, index) => <div key={`${line.level}-${line.capability}-${line.item_id}-${index}`}><span><strong>{line.amount}x {line.item_id ? titleCase(line.item_id) : capabilityName(line.capability)}</strong><small>{lineBreakdown(line)} · {titleCase(line.type || line.capability)}</small></span><span className={line.missing ? "line-missing" : "line-ready"}>{line.missing ? `${line.missing} missing` : "Ready"}</span></div>)}</div></section>
            </div>
            <div className="modal-actions"><button className="button button-secondary" onClick={() => setSearchParams({})}>Close</button>{eventAction(selectedEvent) ? <button className="button button-primary" onClick={() => void changeStatus(selectedEvent.id, eventAction(selectedEvent)!.status)}>{eventAction(selectedEvent)!.label}</button> : null}</div>
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
