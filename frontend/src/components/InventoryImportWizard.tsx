import { ArrowLeft, ArrowRight, Check, ChevronLeft, ChevronRight } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { Modal } from "./ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { titleCase } from "../lib/format";

type Candidate = { target: string; name: string; quantity: number; expected?: string; match: string; evidence: string };
type ImportRow = { row: number; name: string; quantity: number; error: string; type: string; action: string; candidates: Candidate[]; selected_target: string; cells: Record<string, string> };
type Category = { label: string; count: number; code: string | null; suggested: string | null; action: string; source: string | null; custom_label: string; remember: boolean };
export type ImportReview = Record<string, unknown> & { headers: string[]; suggested_mapping: Record<string, string>; rows: ImportRow[]; categories: Category[]; counts: { create: number; add: number; skip: number; unresolved: number }; total: number; can_manage_catalog: boolean; canonical_types: string[]; category_labels: Record<string, string> };
type CategoryChoice = { action: string; code?: string | null; label?: string; remember?: boolean };
type Decision = { action: string; target?: string; expected?: string };
const fields = [["name", "Item name"], ["count", "Quantity"], ["type", "Category"], ["manufacturer", "Manufacturer"], ["model", "Model"], ["location", "Location"], ["condition", "Condition"], ["info", "Notes"]];
const steps = ["Map columns", "Review categories", "Review duplicates", "Confirm import"];

export function InventoryImportWizard({ csv, initial, onClose }: { csv: string; initial: ImportReview; onClose: () => void }) {
  const { mutate } = useWorkspace();
  const [review, setReview] = useState(initial);
  const [mapping, setMapping] = useState(initial.suggested_mapping);
  const [choices, setChoices] = useState<Record<string, CategoryChoice>>({});
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [step, setStep] = useState(0);
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useRef(crypto.randomUUID());
  const heading = useRef<HTMLHeadingElement>(null);
  const body = { csv, scope: "shared", column_mapping: mapping, category_choices: choices, decisions };
  const categoryRows = review.categories.slice(page * 25, (page + 1) * 25);
  const rows = review.rows.slice(page * 25, (page + 1) * 25);
  const totalPages = Math.ceil((step === 1 ? review.categories.length : review.rows.length) / 25);

  function changed() { key.current = crypto.randomUUID(); setError(""); }
  useLayoutEffect(() => { heading.current?.focus(); }, [step]);
  function go(next: number) { setStep(next); setPage(0); }
  async function next() {
    setBusy(true); setError("");
    try {
      const result = await mutate<ImportReview>("/api/inventory/import.review", body);
      setReview(result);
      setChoices(Object.fromEntries(result.categories.map((group) => [group.label, choices[group.label] || { action: group.action, code: group.code }])));
      go(step + 1);
    } catch (e) { setError(e instanceof Error ? e.message : "Review failed."); }
    finally { setBusy(false); }
  }
  async function commit() {
    setBusy(true); setError("");
    try {
      await mutate("/api/inventory/import.commit", { ...body, idempotency_key: key.current }, { refresh: true, success: "Inventory import completed." });
      onClose();
    } catch (e) { setError(e instanceof Error ? e.message : "Import failed. Your stock was not partially imported."); }
    finally { setBusy(false); }
  }
  function chooseCategory(group: Category, update: Partial<CategoryChoice>) {
    changed(); setDecisions({});
    setChoices({ ...choices, [group.label]: { ...(choices[group.label] || { action: group.action, code: group.code }), ...update } });
  }
  function decide(row: ImportRow, value: string) {
    changed();
    const candidate = row.candidates.find((candidate) => candidate.target === value);
    setDecisions({ ...decisions, [row.row]: candidate ? { action: "add", target: value, expected: candidate.expected } : { action: value } });
  }
  return <Modal open title="Review inventory import" onClose={() => { if (!busy) onClose(); }} size="lg">
    <div className="import-wizard" aria-busy={busy}>
      <ol className="import-steps" aria-label="Import progress">{steps.map((label, index) => <li key={label} aria-current={index === step ? "step" : undefined}><span>{index + 1}</span>{label}</li>)}</ol>
      <h3 ref={heading} tabIndex={-1}>{steps[step]}</h3>
      {error ? <p role="alert" className="import-error">{error}</p> : null}
      {step === 0 ? <>
        <div className="csv-mapping-grid">{fields.map(([field, label]) => <label key={field}>{label}{["name", "count"].includes(field) ? " *" : ""}<select value={mapping[field] || ""} onChange={(event) => { changed(); setMapping({ ...mapping, [field]: event.target.value }); setChoices({}); setDecisions({}); }}><option value="">Ignore</option>{review.headers.map((header) => <option key={header} dir="auto">{header}</option>)}</select></label>)}</div>
        <div className="import-table import-raw-table" tabIndex={0} aria-label="CSV preview"><table><thead><tr>{review.headers.map(header => <th key={header} dir="auto">{header}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={row.row}>{review.headers.map(header => <td key={header} dir="auto">{row.cells[header]}</td>)}</tr>)}</tbody></table></div>
      </> : null}
      {step === 1 ? <div className="import-review-list">{categoryRows.map((group) => {
        const choice = choices[group.label] || { action: group.action, code: group.code };
        return <section key={group.label} className="import-review-row">
          <div><strong dir="auto">{group.label}</strong><small>{group.count} rows · {group.source === "workspace" ? "Workspace mapping" : group.suggested ? "Known category" : "Needs review"}</small></div>
          <label>Map <bdi>{group.label}</bdi><select value={choice.action === "map" ? choice.code || "" : choice.action} onChange={(event) => chooseCategory(group, ["ignore", "custom"].includes(event.target.value) ? { action: event.target.value, label: group.label, remember: false } : { action: "map", code: event.target.value })}>
            <option value="">Choose category</option>{review.canonical_types.map((code) => <option key={code} value={code}>{review.category_labels[code] || titleCase(code)}</option>)}{review.can_manage_catalog ? <option value="custom">Create custom category</option> : null}<option value="ignore">Skip these rows</option>
          </select></label>
          {choice.action === "custom" ? <label>New category name<input dir="auto" maxLength={200} value={choice.label || ""} onChange={(e) => chooseCategory(group, { label: e.target.value })} /></label> : null}
          {review.can_manage_catalog && choice.action !== "ignore" ? <label className="import-checkbox"><input type="checkbox" checked={choice.remember || false} onChange={(e) => chooseCategory(group, { remember: e.target.checked })} />Remember this workspace mapping</label> : null}
          {!review.can_manage_catalog && !group.suggested ? <p>Ask a workspace admin to create or map this category.</p> : null}
        </section>;
      })}</div> : null}
      {step === 2 ? <div className="import-review-list">{rows.map((row) => {
        const decision = decisions[row.row];
        const value = decision?.action === "add" ? decision.target : decision?.action || row.action;
        return <section className="import-review-row" key={row.row}>
          <div><strong dir="auto">{row.name || `Row ${row.row}`}</strong><small>Row {row.row} · Import quantity {row.quantity}</small></div>
          {row.error ? <p className="import-error">{row.error}</p> : null}
          {row.candidates.map((candidate) => <p key={candidate.target}><strong>{titleCase(candidate.match)} match:</strong> <bdi>{candidate.name}</bdi> · {candidate.quantity} available<br /><small>{candidate.evidence}</small></p>)}
          <label>Decision for row {row.row}<select value={value} onChange={(e) => decide(row, e.target.value)} disabled={row.action === "skip" && review.categories.find(g => g.label === (row.type || "other"))?.action === "ignore"}>
            <option value="review">Choose a decision</option>{!row.error ? <option value="new">Keep separate / create item</option> : null}{!row.error ? row.candidates.map((candidate) => <option key={candidate.target} value={candidate.target}>Add quantity to {candidate.name}</option>) : null}<option value="skip">Skip imported row</option>
          </select></label>
        </section>;
      })}</div> : null}
      {step === 3 ? <>
        <dl className="import-totals">{[["Rows parsed", review.total], ["New entries", review.counts.create], ["Rows adding quantity", review.counts.add], ["Skipped", review.counts.skip], ["Unresolved", review.counts.unresolved], ["New custom categories", review.categories.filter(g => g.action === "custom").length], ["Mappings to remember", review.categories.filter(g => g.remember).length]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
        {review.counts.unresolved ? <p role="alert" className="import-error">Go back and resolve categories or skip invalid rows before importing.</p> : null}
        <div className="import-table" tabIndex={0} aria-label="Final import preview"><table><thead><tr><th>Item</th><th>Quantity</th><th>Decision</th></tr></thead><tbody>{rows.map(row => <tr key={row.row}><td dir="auto">{row.name}</td><td>{row.quantity}</td><td>{row.action === "new" ? "Create" : titleCase(row.action)}{row.error ? <small>{row.error}</small> : null}</td></tr>)}</tbody></table></div>
      </> : null}
      {totalPages > 1 ? <nav className="import-pagination" aria-label="Review pages"><button className="icon-button" title="Previous page" aria-label="Previous page" disabled={!page} onClick={() => setPage(page - 1)}><ChevronLeft size={18} /></button><span>{page + 1} / {totalPages}</span><button className="icon-button" title="Next page" aria-label="Next page" disabled={page + 1 >= totalPages} onClick={() => setPage(page + 1)}><ChevronRight size={18} /></button></nav> : null}
      <div className="modal-actions"><button className="button button-secondary" disabled={busy} onClick={onClose}>Cancel</button>{step > 0 ? <button className="button button-secondary" disabled={busy} onClick={() => { changed(); go(step - 1); }}><ArrowLeft size={16} />Back</button> : null}
        {step < 3 ? <button className="button button-primary" disabled={busy || !mapping.name || !mapping.count} onClick={() => void next()}>Continue<ArrowRight size={16} /></button> : <button className="button button-primary" disabled={busy || review.counts.unresolved > 0 || !(review.counts.create + review.counts.add)} onClick={() => void commit()}><Check size={16} />{busy ? "Importing..." : "Import inventory"}</button>}
      </div>
    </div>
  </Modal>;
}
