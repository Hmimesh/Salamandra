import { Database, LockKeyhole, ShieldCheck } from "lucide-react";
import { useEffect } from "react";
import { PageHeader } from "../components/ui";

export function LegalPage() {
  useEffect(() => {
    document.title = "Legal and privacy | Salamandra";
    if (window.location.hash) document.querySelector(window.location.hash)?.scrollIntoView();
  }, []);

  return (
    <div className="page legal-page">
      <PageHeader title="Legal & privacy" description="Plain-language terms for the private Salamandra staging service." />
      <section className="legal-document">
        <header><ShieldCheck size={24} /><div><h2>Private staging notice</h2><p>Last reviewed September 3, 2026</p></div></header>
        <article id="terms"><h3>Terms of use</h3><p>Salamandra is currently a private staging product for evaluation and test data. Use only an account provided by, or a workspace created with permission from, the staging team. Do not enter real customer, payment, identity, medical, or production warehouse data.</p></article>
        <article><h3>Operational responsibility</h3><p>Salamandra assists with event and inventory operations, but operators remain responsible for checking plans, quantities, equipment condition, transport, and safety before dispatch.</p></article>
        <article id="privacy"><h3>Privacy policy</h3><p>Salamandra stores account, workspace, inventory, event, checklist, session, and history data needed to provide the service. Workspaces are separated by organization, and each person should use an individual account. Passwords are stored as one-way hashes and are never returned by the application.</p></article>
        <article><h3>Retention and connected services</h3><p>Staging data may be removed as the product changes. External calendar, spreadsheet, or CRM services receive data only after a workspace administrator configures and runs the relevant connection.</p></article>
        <div className="legal-principles"><div><LockKeyhole size={19} /><span><strong>Workspace-scoped access</strong>Events and inventory are isolated between organizations.</span></div><div><Database size={19} /><span><strong>Test data only</strong>Private staging is not approved for live warehouse records.</span></div></div>
      </section>
    </div>
  );
}
