import {
  CalendarSync,
  ChevronRight,
  Database,
  Download,
  FileSpreadsheet,
  Monitor,
  Moon,
  PlugZap,
  Rows3,
  ShieldCheck,
  Sun,
  Upload,
  UserRound,
} from "lucide-react";
import { type ChangeEvent, type FormEvent, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/ui";
import { useWorkspace } from "../context/WorkspaceContext";

function connectionLabel(status: string): string {
  if (status === "ready") return "Ready";
  if (status === "needs_credentials") return "Needs credentials";
  return "Not configured";
}

export function SettingsPage() {
  const { state, mutate, refresh, notify } = useWorkspace();
  const navigate = useNavigate();
  const importInput = useRef<HTMLInputElement>(null);
  const user = state!.auth.user!;
  const preferences = user.preferences;
  const integrations = state!.integrations;

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await mutate("/api/account/profile", { name: String(form.get("name") || ""), title: String(form.get("title") || ""), warehouse: String(form.get("warehouse") || "") }, { success: "Profile saved." }).catch(() => undefined);
  }

  async function savePreferences(update: Partial<typeof preferences>) {
    await mutate("/api/account/preferences", { ...preferences, ...update }, { success: "Workspace appearance updated." }).catch(() => undefined);
  }

  async function checkSync() {
    await mutate("/api/sync/run", {}, { success: "Sync status refreshed." }).catch(() => undefined);
  }

  async function configureCrm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await mutate("/api/integrations/configure", {
      integration_id: "crm",
      provider: String(form.get("provider") || "generic"),
      endpoint: String(form.get("endpoint") || ""),
    }, { success: "CRM connection settings saved." }).catch(() => undefined);
  }

  async function configureSheets(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    await mutate("/api/integrations/configure", {
      integration_id: "google_sheets",
      spreadsheet_id: String(form.get("spreadsheet_id") || ""),
      sheet_name: String(form.get("sheet_name") || "Inventory"),
    }, { success: "Google Sheets connection settings saved." }).catch(() => undefined);
  }

  async function exportInventory() {
    try {
      const response = await fetch("/api/inventory/export.csv", { credentials: "same-origin" });
      if (!response.ok) throw new Error("Inventory export failed.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "salamandra-inventory.csv";
      link.click();
      URL.revokeObjectURL(url);
      await refresh();
      notify("Excel-compatible inventory file downloaded.");
    } catch (error) {
      notify(error instanceof Error ? error.message : "Inventory export failed.");
    }
  }

  async function importInventory(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const csv = await file.text();
    await mutate<{ imported: number }>("/api/inventory/import.csv", { csv, scope: "shared" }, { success: "Shared inventory imported from CSV." }).catch(() => undefined);
    event.target.value = "";
  }

  return (
    <div className="page settings-page">
      <PageHeader title="Settings" description="Manage your account, workspace, integrations, and preferred way of working." />
      <div className="settings-grid">
        <form className="settings-section" onSubmit={saveProfile}><header><span><UserRound size={20} /></span><div><h2>Profile</h2><p>Visible to members of your workspace.</p></div></header><div className="form-stack"><label>Full name<input name="name" defaultValue={user.name} required /></label><label>Job title<input name="title" defaultValue={user.title} /></label><label>Primary warehouse<input name="warehouse" defaultValue={user.warehouse} /></label><label>Email<input value={user.email} disabled /></label></div><footer><span>Role: {user.role}</span><button className="button button-primary" type="submit">Save profile</button></footer></form>

        <section className="settings-section appearance-section"><header><span><Sun size={20} /></span><div><h2>Appearance</h2><p>Make the workspace comfortable for your shift and screen.</p></div></header><div className="preference-list"><div className="preference-row"><span><strong>Theme</strong><small>Follow your device or choose a fixed mode.</small></span><div className="segmented-control" aria-label="Theme">{(["system", "light", "dark"] as const).map((theme) => { const Icon = theme === "system" ? Monitor : theme === "light" ? Sun : Moon; return <button key={theme} type="button" className={preferences.theme === theme ? "active" : ""} aria-pressed={preferences.theme === theme} onClick={() => void savePreferences({ theme })}><Icon size={15} /><span>{theme}</span></button>; })}</div></div><div className="preference-row"><span><strong>Text size</strong><small>Changes labels, forms, tables, and navigation.</small></span><select value={preferences.font_scale} onChange={(event) => void savePreferences({ font_scale: event.target.value as typeof preferences.font_scale })}><option value="compact">Small</option><option value="comfortable">Comfortable</option><option value="large">Large</option></select></div><div className="preference-row"><span><strong>Information density</strong><small>Choose tighter lists or more breathing room.</small></span><div className="segmented-control compact-segments"><button type="button" className={preferences.density === "compact" ? "active" : ""} onClick={() => void savePreferences({ density: "compact" })}><Rows3 size={15} />Compact</button><button type="button" className={preferences.density === "comfortable" ? "active" : ""} onClick={() => void savePreferences({ density: "comfortable" })}>Comfortable</button></div></div><label className="toggle-row"><span><strong>Show operations progress</strong><small>Display level and completion momentum on the dashboard.</small></span><input type="checkbox" checked={preferences.show_progress} onChange={(event) => void savePreferences({ show_progress: event.target.checked })} /></label></div></section>

        <section className="settings-section"><header><span><ShieldCheck size={20} /></span><div><h2>Workspace</h2><p>Company access and membership.</p></div></header><div className="plan-display"><div><span>Workspace</span><strong>{state!.organization.name}</strong></div><span>{state!.organization.seat_count} active member{state!.organization.seat_count === 1 ? "" : "s"}</span></div><div className="settings-facts"><span><strong>Primary warehouse</strong>{state!.organization.warehouse}</span><span><strong>Your access</strong>{user.role}</span></div><footer><span>Owner and admin roles can add accounts.</span><button className="button button-secondary" onClick={() => navigate("/team")}>Manage team</button></footer></section>

        <section className="settings-section"><header><span><CalendarSync size={20} /></span><div><h2>Calendar & sync</h2><p>Keep local event operations ready for connected calendars.</p></div></header><div className="connection-row"><span className="connection-logo">G</span><div><strong>Google Calendar</strong><span>{state!.google_calendar.status === "not_connected" ? "Not connected" : state!.google_calendar.status}</span></div><span className="connection-status">Local only</span></div><p className="settings-note">{state!.sync.note}</p><div className="sync-facts"><span><strong>{state!.sync.pending_events.length}</strong>events waiting to sync</span><span><strong>{state!.sync.last_sync || "Never"}</strong>last sync</span></div><footer><span>OAuth connection will be enabled with production credentials.</span><button className="button button-secondary" onClick={() => void checkSync()}>Check sync</button></footer></section>

        <section className="settings-section integrations-section"><header><span><PlugZap size={20} /></span><div><h2>Business integrations</h2><p>Prepare customer, job, and inventory data for the systems your company already uses.</p></div></header><div className="integration-list"><form className="integration-block" onSubmit={configureCrm}><div className="integration-heading"><span className="integration-logo"><Database size={18} /></span><span><strong>CRM API</strong><small>{integrations.crm.note}</small></span><em className={`integration-status status-${integrations.crm.status}`}>{connectionLabel(integrations.crm.status)}</em></div><div className="integration-fields"><label>Provider<select name="provider" defaultValue={integrations.crm.provider || "generic"}><option value="generic">Generic REST API</option><option value="hubspot">HubSpot</option><option value="salesforce">Salesforce</option></select></label><label>API base URL<input name="endpoint" type="url" defaultValue={integrations.crm.endpoint || ""} placeholder="https://api.example.com" required /></label><label>Credential reference<input value={integrations.crm.credential_env || "SALAMANDRA_CRM_API_KEY"} disabled /></label><button className="button button-secondary" type="submit">Save connection</button></div></form><form className="integration-block" onSubmit={configureSheets}><div className="integration-heading"><span className="integration-logo sheets"><FileSpreadsheet size={18} /></span><span><strong>Google Sheets</strong><small>{integrations.google_sheets.note}</small></span><em className={`integration-status status-${integrations.google_sheets.status}`}>{connectionLabel(integrations.google_sheets.status)}</em></div><div className="integration-fields"><label>Spreadsheet ID<input name="spreadsheet_id" defaultValue={integrations.google_sheets.spreadsheet_id || ""} required /></label><label>Sheet name<input name="sheet_name" defaultValue={integrations.google_sheets.sheet_name || "Inventory"} required /></label><label>Credential reference<input value={integrations.google_sheets.credential_env || "GOOGLE_APPLICATION_CREDENTIALS"} disabled /></label><button className="button button-secondary" type="submit">Save connection</button></div></form><div className="integration-block excel-integration"><div className="integration-heading"><span className="integration-logo excel"><FileSpreadsheet size={18} /></span><span><strong>Excel / CSV</strong><small>{integrations.excel.note}</small></span><em className="integration-status status-ready">Ready</em></div><div className="integration-actions"><button className="button button-secondary" type="button" onClick={() => void exportInventory()}><Download size={16} />Export inventory</button><button className="button button-secondary" type="button" onClick={() => importInput.current?.click()}><Upload size={16} />Import to shared inventory</button><input ref={importInput} className="visually-hidden" type="file" accept=".csv,text/csv" onChange={(event) => void importInventory(event)} /></div></div></div></section>

        <section className="settings-section data-company-section"><header><span><Database size={20} /></span><div><h2>Data & company</h2><p>Support, policy, and staging data use.</p></div></header><button className="settings-nav-row" onClick={() => navigate("/contact")}><span><strong>Contact support</strong><small>Account and product assistance</small></span><ChevronRight size={18} /></button><button className="settings-nav-row" onClick={() => navigate("/terms")}><span><strong>Terms of use</strong><small>Service and operational responsibilities</small></span><ChevronRight size={18} /></button><button className="settings-nav-row" onClick={() => navigate("/privacy")}><span><strong>Privacy</strong><small>Data handling and connected services</small></span><ChevronRight size={18} /></button></section>
      </div>
    </div>
  );
}
