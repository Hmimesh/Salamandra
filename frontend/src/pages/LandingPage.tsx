import {
  ArrowRight,
  Boxes,
  CalendarDays,
  Check,
  ClipboardCheck,
  FileClock,
  PackageOpen,
  RotateCcw,
  ShieldCheck,
  Truck,
} from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { BrandMark } from "../components/BrandMark";

const stages = [
  {
    title: "Plan",
    copy: "Describe the event, venue, schedule, requirements, exclusions, and operational needs.",
    benefit: "A clear brief keeps the warehouse and event team working from the same requirements.",
    icon: CalendarDays,
  },
  {
    title: "Allocate",
    copy: "Match the job against real available inventory and surface shortages or overlapping-event conflicts before show day.",
    benefit: "Scarce equipment goes where it matters without quietly booking the same item twice.",
    icon: Boxes,
  },
  {
    title: "Pack",
    copy: "Turn the approved allocation into a practical packing and checklist workflow.",
    benefit: "Every physical item appears once, with required and optional quantities kept clear.",
    icon: PackageOpen,
  },
  {
    title: "Dispatch",
    copy: "Record what actually left the warehouse and prevent duplicate dispatch movements.",
    benefit: "Retries and double-clicks cannot consume the same stock twice.",
    icon: Truck,
  },
  {
    title: "Return",
    copy: "Check equipment back in, record quantity or condition changes, and restore availability correctly.",
    benefit: "Returned stock is reconciled against the exact equipment that left the warehouse.",
    icon: RotateCcw,
  },
  {
    title: "History",
    copy: "Keep a clear operational record of who planned, packed, dispatched, and returned each job.",
    benefit: "Teams can trace decisions and movements without reconstructing the event afterward.",
    icon: FileClock,
  },
];

const operationalBenefits = [
  { title: "One inventory", copy: "Shared stock, personal equipment, custom departments, and real availability.", icon: Boxes },
  { title: "Conflict detection", copy: "Overlapping events do not quietly reserve the same equipment twice.", icon: ShieldCheck },
  { title: "Controlled movements", copy: "Dispatch and return actions are recorded once, even when retried.", icon: Truck },
  { title: "Useful history", copy: "See who planned, packed, dispatched, and returned each job.", icon: ClipboardCheck },
];

export function LandingPage() {
  const location = useLocation();
  const [selectedStage, setSelectedStage] = useState(0);
  const stageButtons = useRef<Array<HTMLButtonElement | null>>([]);
  const selected = stages[selectedStage];
  const SelectedIcon = selected.icon;

  useEffect(() => {
    document.title = "Salamandra | Event Operations and Inventory";
  }, []);

  useEffect(() => {
    if (!location.hash) return;
    const target = document.getElementById(location.hash.slice(1));
    if (!target) return;
    window.requestAnimationFrame(() => target.scrollIntoView({ behavior: "auto", block: "start" }));
  }, [location.hash]);

  function moveStage(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % stages.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + stages.length) % stages.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = stages.length - 1;
    else return;
    event.preventDefault();
    setSelectedStage(next);
    stageButtons.current[next]?.focus();
  }

  return (
    <main>
      <section className="public-hero" aria-labelledby="hero-title">
        <img className="hero-brand-art" src="/assets/salamandra-mark.png" alt="" aria-hidden="true" />
        <div className="public-container public-hero-inner">
          <div className="public-hero-copy">
            <h1 id="hero-title">Every event ready.<br /><span>Every item accounted for.</span></h1>
            <p>Plan the job, allocate real stock, catch conflicts, and run every return from one operations workspace.</p>
            <div className="public-hero-actions">
              <Link className="button button-primary public-cta" to="/register">Create workspace<ArrowRight size={18} /></Link>
              <Link className="button button-secondary public-cta" to="/#workflow">See how it works</Link>
            </div>
            <ul className="hero-proof" aria-label="Salamandra operational capabilities">
              <li><Check size={16} />Real inventory</li>
              <li><Check size={16} />Conflict detection</li>
              <li><Check size={16} />Controlled movements</li>
            </ul>
          </div>
          <div className="product-preview" id="product">
            <p>Product preview using a test workspace</p>
            <div className="product-frame">
              <img
                src="/assets/salamandra-dashboard-preview.png"
                width="1440"
                height="980"
                alt="Salamandra workspace showing empty event and inventory states with setup actions"
                fetchPriority="high"
              />
            </div>
          </div>
        </div>
      </section>

      <section className="workflow-band" id="workflow" aria-labelledby="workflow-title">
        <div className="public-container">
          <div className="workflow-heading">
            <span>The workflow</span>
            <h2 id="workflow-title">From brief to return</h2>
            <p>Salamandra connects every operational step with the equipment you actually have.</p>
          </div>
          <div className="workflow-tabs" role="tablist" aria-label="Event operations workflow">
            {stages.map(({ title, icon: Icon }, index) => (
              <button
                key={title}
                ref={(button) => { stageButtons.current[index] = button; }}
                id={`workflow-tab-${index}`}
                type="button"
                role="tab"
                aria-selected={selectedStage === index}
                aria-controls="workflow-detail"
                tabIndex={selectedStage === index ? 0 : -1}
                onClick={() => setSelectedStage(index)}
                onKeyDown={(event) => moveStage(event, index)}
              >
                <span><Icon size={20} aria-hidden="true" /></span>
                <strong>{title}</strong>
                <ArrowRight size={16} aria-hidden="true" />
              </button>
            ))}
          </div>
          <div
            className="workflow-detail"
            id="workflow-detail"
            role="tabpanel"
            tabIndex={0}
            aria-labelledby={`workflow-tab-${selectedStage}`}
          >
            <span className="workflow-detail-icon"><SelectedIcon size={28} aria-hidden="true" /></span>
            <div><h3>{selected.title}</h3><p>{selected.copy}</p></div>
            <aside><strong>Why it matters</strong><p>{selected.benefit}</p></aside>
          </div>
        </div>
      </section>

      <section className="operations-proof" aria-labelledby="operations-title">
        <div className="public-container">
          <div className="operations-proof-heading">
            <span>Built for real operations</span>
            <h2 id="operations-title">Plan around the equipment you actually have.</h2>
            <p>Audio, video, furniture, hospitality, transport, site infrastructure, and organization-defined equipment all belong in the same operational picture.</p>
          </div>
          <div className="operations-benefits">
            {operationalBenefits.map(({ title, copy, icon: Icon }) => (
              <article key={title}><Icon size={25} aria-hidden="true" /><h3>{title}</h3><p>{copy}</p></article>
            ))}
          </div>
        </div>
      </section>

      <section className="public-final-cta">
        <div className="public-container">
          <BrandMark />
          <div><h2>Set up the workspace before the next load-in.</h2><p>Start empty, add real inventory, invite the crew, and create the first event.</p></div>
          <Link className="button public-cta" to="/register">Create workspace<ArrowRight size={18} /></Link>
        </div>
      </section>
    </main>
  );
}
