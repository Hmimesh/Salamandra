import { ClipboardCheck, ShieldCheck } from "lucide-react";
import { useEffect } from "react";
import { PageHeader } from "../components/ui";

export function TermsPage() {
  useEffect(() => {
    document.title = "Terms | Salamandra";
  }, []);

  return (
    <div className="page legal-page">
      <PageHeader title="Terms of use" description="Terms for using the private Salamandra staging service." />
      <article className="legal-document">
        <aside className="legal-notice"><ShieldCheck size={20} aria-hidden="true" /><span><strong>Private staging</strong>Use test data only. This environment is not approved for live warehouse or customer records.</span></aside>
        <p className="legal-reviewed">Last reviewed September 3, 2026</p>
        <section><h2>Service scope</h2><p>Salamandra is currently a private staging product for evaluating event, inventory, checklist, dispatch, and return workflows. Access is limited to people authorized by the staging team or a workspace owner.</p></section>
        <section><h2>Acceptable use</h2><p>Use Salamandra only for lawful evaluation and operational testing. Do not attempt to access another organization, interfere with the service, probe credentials, or submit real payment, identity, medical, customer, or production warehouse data.</p></section>
        <section><h2>Operational responsibility</h2><p>Salamandra assists with event and inventory planning. Operators remain responsible for checking requirements, quantities, equipment condition, transport, site constraints, and safety before dispatch and during return.</p></section>
        <section><h2>Accounts and access</h2><p>Each person should use an individual account and keep their credentials private. Workspace owners are responsible for assigning appropriate access and disabling accounts or memberships that should no longer be active.</p></section>
        <section><h2>Staging availability</h2><p>The staging service may change, restart, or be reset as Salamandra is tested. Features and stored test data may change before a production release.</p></section>
        <footer className="legal-document-footer"><ClipboardCheck size={20} aria-hidden="true" /><span>Operators remain responsible for the final event and warehouse checks.</span></footer>
      </article>
    </div>
  );
}
