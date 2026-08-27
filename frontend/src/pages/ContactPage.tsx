import { Clock3, Copy, Mail, MapPin } from "lucide-react";
import { useWorkspace } from "../context/WorkspaceContext";
import { PageHeader } from "../components/ui";

export function ContactPage() {
  const { notify } = useWorkspace();
  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
    notify("Email address copied.");
  }
  return <div className="page support-page"><PageHeader title="Contact support" description="Practical help for event preparation, inventory, returns, and account access." /><div className="support-grid"><section className="support-main"><h2>Talk to operations</h2><p>Include the event name, affected equipment, and whether the issue blocks dispatch or return.</p><div className="contact-row"><span><Mail size={19} /></span><div><strong>Operations desk</strong><a href="mailto:ops@salamandra.local">ops@salamandra.local</a></div><button className="icon-button" title="Copy operations email" aria-label="Copy operations email" onClick={() => void copy("ops@salamandra.local")}><Copy size={17} /></button></div><div className="contact-row"><span><Mail size={19} /></span><div><strong>Account support</strong><a href="mailto:support@salamandra.local">support@salamandra.local</a></div><button className="icon-button" title="Copy support email" aria-label="Copy support email" onClick={() => void copy("support@salamandra.local")}><Copy size={17} /></button></div></section><aside className="support-details"><div><Clock3 size={20} /><span><strong>Response target</strong>Under 4 business hours</span></div><div><MapPin size={20} /><span><strong>Show-day priority</strong>Dispatch blockers are triaged first</span></div></aside></div></div>;
}

