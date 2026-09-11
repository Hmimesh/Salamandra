import { useEffect, useRef, useState } from "react";
import { History } from "lucide-react";
import { apiRequest } from "../lib/api";
import type { EventRecord } from "../types";

type Suggestion = { capability: string; amount: number; minimum: number; maximum: number; evidence_count: number; reason: string };
type Result = { suggestions: Suggestion[]; evidence_count: number; warnings: string[]; dimensions: string[] };

export function HistoricalSuggestions({ event, onApply }: { event: EventRecord; onApply: (requirements: { capability: string; amount: number }[]) => void }) {
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [ignored, setIgnored] = useState(false);
  const reviewButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (ignored) reviewButton.current?.focus(); }, [ignored]);
  const features = JSON.stringify({ departments: [...new Set(event.capability_requirements.map(item => item.capability.split(".")[0]))].sort(), guest_count: event.attendee_count || null, duration_minutes: event.duration_minutes || null, venue_type: event.venue_kind || null });
  useEffect(() => {
    let active = true;
    setResult(null); setError(""); setIgnored(false);
    const timer = window.setTimeout(() => {
      void apiRequest<Result>("/api/events/learning/suggestions", { method: "POST", body: { features: JSON.parse(features) } })
        .then(value => { if (active) setResult(value); })
        .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "History is unavailable."); });
    }, 300);
    return () => { active = false; window.clearTimeout(timer); };
  }, [features]);
  if (ignored) return <section className="history-suggestions" aria-label="From your event history"><p>Suggestions ignored.</p><button ref={reviewButton} type="button" className="button button-secondary" onClick={() => setIgnored(false)}>Review suggestions</button></section>;
  return <section className="history-suggestions" aria-label="From your event history">
    <h3><History size={17} />From your event history</h3>
    {error ? <p role="status">{error}</p> : !result ? <p role="status">Checking completed events...</p> : <>
      <p>{result.suggestions.length ? `Based on ${result.evidence_count} similar past events` : "No similar completed events with repeated quantity evidence yet."}</p>
      {result.suggestions.length ? <p>{result.suggestions[0].reason}</p> : null}
      {result.suggestions.map((suggestion, index) => {
        const previous = event.capability_requirements.filter(item => item.capability === suggestion.capability).reduce((sum, item) => sum + item.amount, 0);
        return <label key={suggestion.capability}><span><strong>{suggestion.capability.replaceAll(".", " ")}</strong><small>{previous ? `${previous} currently planned` : "Suggested addition"} · {suggestion.evidence_count} events · observed {suggestion.minimum}–{suggestion.maximum}</small></span><input aria-label={`Suggested quantity for ${suggestion.capability}`} type="number" min={1} max={10000} value={suggestion.amount} onChange={change => setResult({ ...result, suggestions: result.suggestions.map((item, i) => i === index ? { ...item, amount: Number(change.target.value) } : item) })} /></label>;
      })}
      {result.warnings.map(warning => <p key={warning}>{warning}</p>)}
      <div className="composer-actions"><button className="button button-secondary" type="button" onClick={() => setIgnored(true)}>Ignore</button>{result.suggestions.length ? <button className="button button-primary" type="button" disabled={result.suggestions.some(item => !Number.isInteger(item.amount) || item.amount < 1 || item.amount > 10000)} onClick={() => { onApply(result.suggestions); setIgnored(true); }}>Apply to manual plan</button> : null}</div>
    </>}
  </section>;
}
