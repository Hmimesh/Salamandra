import { Mail, MapPin, Plus, ShieldCheck, Users } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Avatar, EmptyState, Modal, PageHeader } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";
import { titleCase } from "../lib/format";

export function TeamPage() {
  const { state, mutate } = useWorkspace();
  const [inviteOpen, setInviteOpen] = useState(false);
  const canInvite = ["owner", "admin"].includes(state!.auth.user!.role);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await mutate("/api/team/invite", {
        name: String(form.get("name") || ""),
        email: String(form.get("email") || ""),
        password: String(form.get("password") || ""),
        role: String(form.get("role") || "technician"),
        title: String(form.get("title") || "Event Operations"),
        warehouse: String(form.get("warehouse") || state!.auth.user!.warehouse),
      }, { success: "Team member added." });
      setInviteOpen(false);
    } catch {
      // The workspace provider reports the API message.
    }
  }

  return (
    <div className="page team-page">
      <PageHeader title="Team" description="See who is available, where they work, and what role they hold." actions={canInvite ? <button className="button button-primary" onClick={() => setInviteOpen(true)}><Plus size={17} />Add team member</button> : undefined} />
      <section className="team-overview"><div><span className="summary-icon"><Users size={20} /></span><span><small>Workspace members</small><strong>{state!.organization.seat_count}</strong></span></div><div><small>Online now</small><strong>{state!.presence.filter((member) => member.online).length}</strong></div><div><small>Primary warehouse</small><strong>{state!.organization.warehouse}</strong></div><div><small>Your access</small><strong>{titleCase(state!.auth.user!.role)}</strong></div></section>
      <section className="data-section team-directory">
        <div className="section-title-row"><div><h2>Workspace members</h2><p>Accounts are isolated to {state!.organization.name}.</p></div><span className="result-count">{state!.presence.length} members</span></div>
        <div className="team-table">
          {state!.presence.map((member) => <article className="team-member" key={member.id}><div className="member-identity"><span className="presence-avatar"><Avatar user={member} size="lg" /><i className={member.online ? "online" : "offline"} /></span><span><strong>{member.name}</strong><small>{member.title || titleCase(member.role)}</small></span></div><span className={`role-label role-${member.role}`}><ShieldCheck size={15} />{titleCase(member.role)}</span><span><Mail size={15} />{member.email}</span><span><MapPin size={15} />{member.warehouse || "No warehouse"}</span><span className={member.online ? "member-online" : "member-offline"}>{member.online ? "Online" : "Offline"}</span></article>)}
        </div>
        {state!.presence.length === 1 ? <EmptyState title="You're the only person in this workspace" message="Add individual accounts when the rest of the crew is ready." action={canInvite ? <button className="button button-primary" type="button" onClick={() => setInviteOpen(true)}>Invite teammate</button> : undefined} /> : null}
      </section>

      <Modal open={inviteOpen} title="Add team member" description={`Create an account inside ${state!.organization.name}.`} onClose={() => setInviteOpen(false)}>
        <form className="form-stack" onSubmit={invite}><div className="form-grid"><label>Full name<input name="name" placeholder="Jordan Lee" required /></label><label>Work email<input name="email" type="email" placeholder="jordan@company.com" required /></label><label>Role<select name="role" defaultValue="technician"><option value="technician">Technician</option><option value="producer">Producer</option><option value="operator">Operator</option><option value="admin">Admin</option></select></label><label>Temporary password<input name="password" type="password" minLength={10} autoComplete="new-password" required /></label><label>Job title<input name="title" placeholder="Audio Technician" /></label><label>Warehouse<input name="warehouse" defaultValue={state!.auth.user!.warehouse} /></label></div><div className="modal-actions"><button className="button button-secondary" type="button" onClick={() => setInviteOpen(false)}>Cancel</button><button className="button button-primary" type="submit">Add to workspace</button></div></form>
      </Modal>
    </div>
  );
}
