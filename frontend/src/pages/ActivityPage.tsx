import { CheckCircle2, ClipboardCheck, PackageCheck, RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, PageHeader, StatusTag } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { allActivity, formatDateLong, formatTimeAgo, titleCase } from "../lib/format";

export function ActivityPage() {
  const { state, refresh } = useWorkspace();
  const [filter, setFilter] = useState("all");
  const activity = useMemo(() => {
    const entries = allActivity(state!.events.events);
    return filter === "all" ? entries : entries.filter((entry) => entry.action === filter);
  }, [filter, state]);

  return (
    <div className="page">
      <PageHeader title="Activity" description="A complete operational trail for plans, packing, dispatch, and returns." actions={<button className="button button-secondary" onClick={() => void refresh()}><RotateCcw size={17} />Refresh</button>} />
      <div className="toolbar-row">
        <div className="segmented-control" aria-label="Activity filter">
          {["all", "prepared", "checklist", "packed", "out", "returned"].map((value) => <button key={value} type="button" className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{titleCase(value)}</button>)}
        </div>
        <span className="result-count">{activity.length} updates</span>
      </div>

      <section className="data-section activity-page-list">
        {activity.length ? activity.map((entry, index) => {
          const Icon = entry.action === "returned" ? CheckCircle2 : entry.action === "checklist" ? ClipboardCheck : PackageCheck;
          return (
            <article className="activity-row" key={`${entry.event.id}-${entry.timestamp}-${index}`}>
              <span className={`activity-icon action-${entry.action}`}><Icon size={18} /></span>
              <div><strong>{entry.note}</strong><span>{entry.event.title} · {formatDateLong(entry.event.start_date)}</span></div>
              <StatusTag status={entry.event.status} />
              <time title={new Date(entry.timestamp).toLocaleString()}>{formatTimeAgo(entry.timestamp)}</time>
            </article>
          );
        }) : <EmptyState title="No matching activity" message="Try another filter or perform an event operation." />}
      </section>
    </div>
  );
}
