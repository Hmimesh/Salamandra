import { ExternalLink, KeyRound, MessageSquareText } from "lucide-react";
import { useEffect } from "react";
import { PageHeader } from "../components/ui";

export function ContactPage() {
  useEffect(() => {
    document.title = "Contact | Salamandra";
  }, []);

  return (
    <div className="page support-page">
      <PageHeader title="Contact" description="Get help with private staging access or report a product problem." />
      <div className="support-grid">
        <section className="support-main">
          <h2>Private staging support</h2>
          <p>Use the channel that issued your staging account for access questions. Include the affected workspace and event name, but never include a password or session token.</p>
          <a className="contact-action" href="https://github.com/Hmimesh/Salamandra/issues" target="_blank" rel="noreferrer"><span><MessageSquareText size={19} /></span><span><strong>Report a product issue</strong><small>Open the Salamandra issue tracker</small></span><ExternalLink size={17} /></a>
        </section>
        <aside className="support-details" aria-label="Account help">
          <div><KeyRound size={20} /><span><strong>Workspace access</strong>Ask your workspace owner to create or disable team accounts.</span></div>
          <div><MessageSquareText size={20} /><span><strong>Useful details</strong>Share the page, action, and visible error message.</span></div>
        </aside>
      </div>
    </div>
  );
}
