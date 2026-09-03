import { Database, LockKeyhole } from "lucide-react";
import { useEffect } from "react";
import { PageHeader } from "../components/ui";

export function PrivacyPage() {
  useEffect(() => {
    document.title = "Privacy | Salamandra";
  }, []);

  return (
    <div className="page legal-page">
      <PageHeader title="Privacy" description="How data is handled in the private Salamandra staging service." />
      <article className="legal-document">
        <aside className="legal-notice"><Database size={20} aria-hidden="true" /><span><strong>Test data only</strong>Do not enter real customer, employee, payment, identity, medical, or production inventory information.</span></aside>
        <p className="legal-reviewed">Last reviewed September 3, 2026</p>
        <section><h2>Data Salamandra handles</h2><p>Salamandra stores the account, workspace, membership, inventory, event, checklist, session, movement, and history data needed to provide the service.</p></section>
        <section><h2>How data is used</h2><p>Data is used to authenticate people, keep organizations separate, calculate inventory availability, support event operations, record stock movements, and show an operational history.</p></section>
        <section><h2>Workspace separation</h2><p>Events, inventory, memberships, and operational records are scoped to their organization. Each person should use an individual account so actions can be attributed correctly.</p></section>
        <section><h2>Retention</h2><p>Staging data may be retained while the product is evaluated and may be removed as the environment is reset or the product changes. Do not rely on staging as a permanent system of record.</p></section>
        <section><h2>Connected services</h2><p>Calendar, spreadsheet, or CRM services receive data only after a workspace administrator explicitly configures and runs the relevant connection.</p></section>
        <footer className="legal-document-footer"><LockKeyhole size={20} aria-hidden="true" /><span>Passwords are stored as one-way hashes and are never returned by the application.</span></footer>
      </article>
    </div>
  );
}
