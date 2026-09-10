import { CheckCircle2, ClipboardCheck, PackageCheck, Truck } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, PageHeader, StatusTag } from "../components/ui";
import { EventReviewModal } from "../components/EventReviewModal";
import { useWorkspace } from "../context/WorkspaceContext";
import { formatDateLong, titleCase } from "../lib/format";
import type { EventRecord } from "../types";

function nextAction(event: EventRecord): { label: string; status: EventRecord["status"]; icon: typeof PackageCheck } | null {
  if (event.status === "planning") return { label: "Confirm event", status: "confirmed", icon: CheckCircle2 };
  if (event.status === "confirmed" && event.checklist.every((item) => item.done)) return { label: "Mark packed", status: "packed", icon: PackageCheck };
  if (event.status === "packed") return { label: "Dispatch load", status: "out", icon: Truck };
  if (event.status === "out" && event.return_checklist.every((item) => item.done)) return { label: "Complete return", status: "returned", icon: CheckCircle2 };
  return null;
}

export function ReturnsPage() {
  const { state, mutate } = useWorkspace();
  const active = useMemo(() => state!.events.events.filter((event) => event.status !== "returned").sort((a, b) => a.start_date.localeCompare(b.start_date)), [state]);
  const returnEvents = active.filter((event) => event.status === "out");
  const [reviewEvent, setReviewEvent] = useState<EventRecord | null>(null);
  const returned = state!.events.events.filter((event) => event.status === "returned").sort((a, b) => b.start_date.localeCompare(a.start_date));
  const dueItems = returnEvents.reduce((sum, event) => sum + event.return_checklist.filter((item) => !item.done).reduce((count, item) => count + item.amount, 0), 0);
  const allChecks = active.flatMap((event) => event.status === "out" ? event.return_checklist : event.checklist);
  const completion = allChecks.length ? Math.round((allChecks.filter((item) => item.done).length / allChecks.length) * 100) : 100;

  async function toggle(event: EventRecord, itemId: string, phase: "pack" | "return", done: boolean) {
    await mutate("/api/events/checklist", { event_id: event.id, item_id: itemId, phase, done }, { success: "Checklist updated." }).catch(() => undefined);
  }

  async function advance(event: EventRecord, status: EventRecord["status"]) {
    await mutate("/api/events/status", { event_id: event.id, status }, { success: `Event marked ${titleCase(status).toLowerCase()}.` }).catch(() => undefined);
  }

  return (
    <div className="page returns-page">
      <PageHeader title="Returns & checklists" description="Pack against the plan, dispatch cleanly, and bring every item back into stock." />
      <section className="returns-summary"><article><span>Events in operations</span><strong>{active.length}</strong></article><article><span>Items due back now</span><strong>{dueItems}</strong></article><article><span>Checklist completion</span><strong>{completion}%</strong></article></section>
      <section className="return-work-list">
        {active.length ? active.map((event) => {
          const returning = event.status === "out";
          const checklist = returning ? event.return_checklist : event.checklist;
          const complete = checklist.filter((item) => item.done).length;
          const action = nextAction(event);
          const ActionIcon = action?.icon;
          return <article className="return-work" key={event.id}><header><span className={`return-stage-icon ${returning ? "returning" : "packing"}`}>{returning ? <Truck size={20} /> : <ClipboardCheck size={20} />}</span><div><div><h2>{event.title}</h2><StatusTag status={event.status} /></div><p>{formatDateLong(event.start_date)} · {event.start_time} · {event.location || "Location TBD"}</p></div><span className="check-count"><strong>{complete}/{checklist.length}</strong><small>{returning ? "returned" : "packed"}</small></span></header><div className="return-progress"><span style={{ width: `${checklist.length ? (complete / checklist.length) * 100 : 100}%` }} /></div><div className="return-check-grid">{checklist.map((item) => <label key={item.id} className={item.done ? "done" : ""}><input type="checkbox" checked={item.done} onChange={(change) => void toggle(event, item.item_id, item.phase, change.target.checked)} /><span><strong>{item.amount}x {titleCase(item.item_id)}</strong><small>{returning ? "Inspect and return to stock" : "Pack and verify"}</small></span><CheckCircle2 size={18} /></label>)}</div><footer><span>{returning ? "Completing this return restores inventory counts." : "All checks must be complete before the event can advance."}</span>{action && ActionIcon ? <button className="button button-primary" onClick={() => void advance(event, action.status)}><ActionIcon size={17} />{action.label}</button> : <span className="waiting-state">Complete the checklist to continue</span>}</footer></article>;
        }) : <EmptyState title="Operations are clear" message="There are no active packing or return checklists." />}
      </section>
      {returned.length ? <section className="return-work-list returned-history"><div className="section-title-row"><div><h2>Recently returned</h2><p>Leave a short note while the job is still fresh.</p></div></div>{returned.slice(0, 12).map((event) => <article className="return-work" key={event.id}><header><span className="return-stage-icon returned"><CheckCircle2 size={20} /></span><div><h2>{event.title}</h2><p>{formatDateLong(event.start_date)} · {event.location || "Location TBD"}</p></div><button className="button button-secondary" onClick={() => setReviewEvent(event)}>Review event</button></header></article>)}</section> : null}
      <EventReviewModal event={reviewEvent} onClose={() => setReviewEvent(null)} />
    </div>
  );
}
