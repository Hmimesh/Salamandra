import { useEffect, useState } from "react";
import { History, RefreshCw } from "lucide-react";
import { apiRequest } from "../lib/api";

type Comparison = { capability: string; suggested: number; applied: number; final: number; outcome: string };
type Summary = { eligible_events: number; sessions: number; counts: Record<string, number>; outcomes: Record<string, number>; recent: { session_id: string; event_id: string | null; title: string; action: string | null; comparisons: Comparison[] }[] };

export function LearningSummary() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setError("");
    void apiRequest<Summary>("/api/learning/summary").then(value => { if (active) setSummary(value); })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "Learning summary is unavailable."); });
    return () => { active = false; };
  }, [attempt]);
  const counts = summary?.counts || {};
  const completed = (counts.applied || 0) + (counts.applied_with_edits || 0) + (counts.ignored || 0);
  return <section className="settings-section" aria-labelledby="learning-heading">
    <header><span><History size={20} /></span><div><h2 id="learning-heading">Learning</h2><p>Historical suggestions and operator responses.</p></div></header>
    {error ? <p role="alert">{error}</p> : null}
    {!summary ? <p role="status">{error ? "Summary unavailable." : "Loading learning summary..."}</p> : <>
      <dl className="learning-metrics">
        <div><dt>Eligible completed events</dt><dd>{summary.eligible_events}</dd></div>
        <div><dt>Suggestion sessions</dt><dd>{summary.sessions}</dd></div>
        <div><dt>Applied unchanged</dt><dd>{counts.applied || 0} of {completed} responses</dd></div>
        <div><dt>Applied with edits</dt><dd>{counts.applied_with_edits || 0} of {completed} responses</dd></div>
        <div><dt>Ignored</dt><dd>{counts.ignored || 0} of {completed} responses</dd></div>
      </dl>
      <h3>Returned comparisons</h3>
      <dl className="learning-metrics">{Object.entries(summary.outcomes).map(([outcome, count]) => <div key={outcome}><dt>{outcome.replaceAll("_", " ")}</dt><dd>{count} of {Object.values(summary.outcomes).reduce((sum, value) => sum + value, 0)} compared suggestions</dd></div>)}</dl>
      {!Object.keys(summary.outcomes).length ? <p>No returned comparisons yet.</p> : null}
      <p className="settings-note">Applied counts describe draft acceptance. Returned comparisons describe later recorded requirements, not event success.</p>
      {summary.sessions === 0 ? <p>No suggestion sessions yet.</p> : <><h3>Recent outcomes</h3><ul className="learning-outcomes">{summary.recent.map((item, index) => <li key={item.session_id}>
        <strong>{item.title || `Unsaved draft · ${index + 1}`}</strong>
        <p>{item.action?.replaceAll("_", " ") || "Awaiting response"}</p>
        {item.comparisons.map(comparison => <p key={comparison.capability}>{comparison.capability.replaceAll(".", " ")}: {comparison.suggested} suggested → {comparison.applied} applied → {comparison.final} final · {comparison.outcome.replaceAll("_", " ")}</p>)}
      </li>)}</ul></>}
    </>}
    <footer><button type="button" className="button button-secondary" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={16} />Refresh learning summary</button></footer>
  </section>;
}
