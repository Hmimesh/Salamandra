import {
  ArrowRight,
  Boxes,
  CalendarCheck2,
  ClipboardCheck,
  History,
  PackageCheck,
  ShieldCheck,
  Truck,
} from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router-dom";

const workflow = [
  { number: "01", title: "Brief to plan", copy: "Describe the event. Build a schedule and equipment plan against stock you really own.", icon: CalendarCheck2 },
  { number: "02", title: "Stock to dispatch", copy: "Allocate by capability, resolve conflicts, pack to a checklist, and dispatch once.", icon: Truck },
  { number: "03", title: "Return to ready", copy: "Check every line back in, record what changed, and leave inventory ready for the next job.", icon: ClipboardCheck },
];

export function LandingPage() {
  useEffect(() => {
    document.title = "Salamandra | Event Operations and Inventory";
  }, []);

  return (
    <main>
      <section className="public-hero">
        <div className="public-container public-hero-inner">
          <div className="public-hero-copy">
            <h1>Every event ready.<br />Every item accounted for.</h1>
            <p>Plan the job, allocate real stock, catch conflicts, and run every return from one operations workspace.</p>
            <div className="public-hero-actions">
              <Link className="button button-primary public-cta" to="/register">Create workspace<ArrowRight size={18} /></Link>
              <Link className="button button-secondary public-cta" to="/login">Sign in</Link>
            </div>
          </div>
          <p className="product-preview-note">Product preview using a test workspace</p>
          <div className="product-frame" id="product">
            <img
              src="/assets/salamandra-dashboard-empty.png"
              width="1440"
              height="980"
              alt="Salamandra workspace showing event setup, inventory readiness, and the operations navigation"
              fetchPriority="high"
            />
          </div>
        </div>
      </section>

      <section className="workflow-band" id="workflow" aria-labelledby="workflow-title">
        <div className="public-container">
          <h2 id="workflow-title">A clear workflow for event operations</h2>
          <div className="workflow-steps">
            {workflow.map(({ number, title, copy, icon: Icon }) => (
              <article key={number}>
                <span className="workflow-number">{number}</span>
                <Icon size={27} strokeWidth={1.6} aria-hidden="true" />
                <h3>{title}</h3>
                <p>{copy}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="operations-proof" aria-labelledby="operations-title">
        <div className="public-container operations-proof-grid">
          <div className="operations-proof-copy">
            <h2 id="operations-title">Plan around the equipment you actually have.</h2>
            <p>Salamandra connects the event brief to shared and personal inventory. Missing capability stays visible instead of becoming imaginary stock.</p>
            <ul>
              <li><Boxes size={20} aria-hidden="true" /><span><strong>One inventory</strong>Audio, video, furniture, hospitality, transport, site equipment, and your own classes.</span></li>
              <li><ShieldCheck size={20} aria-hidden="true" /><span><strong>Conflicts before show day</strong>Overlapping events do not quietly book the same equipment twice.</span></li>
              <li><PackageCheck size={20} aria-hidden="true" /><span><strong>Controlled movements</strong>Retries and double-clicks do not duplicate dispatches or returns.</span></li>
              <li><History size={20} aria-hidden="true" /><span><strong>A useful history</strong>See who planned, packed, dispatched, and returned each job.</span></li>
            </ul>
          </div>
          <div className="operations-sequence" aria-label="Salamandra workflow">
            {['Plan', 'Allocate', 'Pack', 'Dispatch', 'Return', 'History'].map((step, index) => (
              <div key={step}><span>{String(index + 1).padStart(2, '0')}</span><strong>{step}</strong>{index < 5 ? <ArrowRight size={17} aria-hidden="true" /> : null}</div>
            ))}
          </div>
        </div>
      </section>

      <section className="public-final-cta">
        <div className="public-container">
          <div><h2>Set up the workspace before the next load-in.</h2><p>Start empty, add real inventory, invite the crew, and create the first event.</p></div>
          <Link className="button button-primary public-cta" to="/register">Create workspace<ArrowRight size={18} /></Link>
        </div>
      </section>
    </main>
  );
}
