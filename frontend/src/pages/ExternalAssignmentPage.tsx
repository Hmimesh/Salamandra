import { useEffect, useLayoutEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { BrandMark } from "../components/BrandMark";

type Assignment = { event: string; venue: string; status: string; person: string; role: string; call_at: string; release_at: string; timezone: string; notes: string; milestones: Record<string, string>; equipment: { name: string; quantity: number }[]; updates: { at: string; message: string }[] };
const labels: Record<string, string> = { prepare_at: "Prepare", pack_by: "Pack by", standby_at: "Standby", dispatch_at: "Dispatch", load_in_at: "Load in", setup_at: "Setup", teardown_at: "Teardown", return_due_at: "Return due" };
export function ExternalAssignmentPage() {
  const [token, setToken] = useState(() => location.hash.slice(1));
  useLayoutEffect(() => {
    function captureFragment() {
      if (!location.hash) return;
      const credential = location.hash.slice(1);
      history.replaceState(history.state, "", location.pathname + location.search);
      setToken(credential);
    }
    captureFragment();
    window.addEventListener("hashchange", captureFragment);
    return () => window.removeEventListener("hashchange", captureFragment);
  }, []);
  const [data, setData] = useState<Assignment | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let live = true;
    const controller = new AbortController();
    let pending = false;
    async function load() {
      if (pending || document.hidden) return;
      if (!token) {
        setData(null);
        setError("Reopen the original assignment link to view this assignment.");
        return;
      }
      pending = true;
      try {
        const response = await fetch("/api/external/assignment", { credentials: "omit", headers: { Authorization: `Bearer ${token}` }, signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("This assignment link is unavailable or has expired.");
        const next = await response.json() as Assignment;
        if (live) { setData(next); setError(""); }
      } catch (e) { if (live) { setData(null); setError(e instanceof Error ? e.message : "Assignment unavailable."); } }
      finally { pending = false; }
    }
    void load(); const timer = window.setInterval(() => void load(), 30000);
    document.addEventListener("visibilitychange", load);
    return () => { live = false; controller.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", load); };
  }, [token, revision]);
  const date = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: data?.timezone || "Asia/Jerusalem" }).format(new Date(value));
  return <main className="external-assignment"><header className="section-title-row"><BrandMark /><button className="icon-button" title="Refresh assignment" aria-label="Refresh assignment" onClick={() => setRevision(v => v + 1)}><RefreshCw size={18} /></button></header>{error ? <p role="alert">{error}</p> : !data ? <p role="status">Loading assignment…</p> : <>
    <h1 dir="auto">{data.event}</h1><p dir="auto">{data.venue}</p><h2 dir="auto">{data.person} · {data.role}</h2><dl><dt>Call</dt><dd>{date(data.call_at)}</dd><dt>Release</dt><dd>{date(data.release_at)}</dd><dt>Timezone</dt><dd>{data.timezone}</dd></dl>
    {data.notes ? <section><h2>Your notes</h2><p dir="auto">{data.notes}</p></section> : null}<section><h2>Logistics</h2><ol className="logistics-timeline">{Object.entries(data.milestones).map(([key, value]) => <li key={key}><strong>{labels[key] || "Milestone"}</strong><span>{date(value)}</span></li>)}</ol></section>
    <section><h2>Your equipment</h2>{data.equipment.length ? data.equipment.map((item, index) => <p dir="auto" key={index}>{item.quantity} × {item.name}</p>) : <p>No equipment shared with this assignment.</p>}</section>
    <section><h2>Latest updates</h2>{data.updates.map((update, index) => <p key={index}><time>{date(update.at)}</time> · {update.message}</p>)}</section>
  </>}</main>;
}
