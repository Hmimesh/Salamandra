export type InventoryScope = "combined" | "shared" | "personal";

export interface Requirement {
  item_id: string;
  amount: number;
}

export interface InventoryItem {
  id: string;
  type: string;
  count: number;
  in_use_count: number;
  info: string;
  quality_score: number;
  requirements: Requirement[];
  class_id: string;
  manufacturer: string;
  model: string;
  condition: string;
  capabilities: string[];
  connectors: string[];
  attributes: Record<string, string | number | boolean | string[]>;
  preference_score: number;
  weight_kg: number;
}

export interface DependencyRule {
  capability: string;
  per_unit: number;
  fixed_amount: number;
  level: "required" | "recommended" | "optional";
  note: string;
}

export interface ItemClass {
  id: string;
  name: string;
  family: string;
  capabilities: string[];
  organization_id: string;
  aliases: string[];
  connectors: string[];
  dependency_rules: DependencyRule[];
  substitute_class_ids: string[];
  preference_weight: number;
  spare_factor: number;
  description: string;
  is_system: boolean;
}

export interface CapabilityRequirement {
  capability: string;
  amount: number;
  level: "required" | "recommended" | "optional";
  source: string;
  min_quality: number;
  required_connectors: string[];
  attributes: Record<string, string | number | boolean>;
}

export interface InventorySummary {
  unique_items: number;
  in_stock: number;
  in_use: number;
  requirements: number;
}

export interface InventoryCollection {
  items: InventoryItem[];
  summary: InventorySummary;
}

export interface UserAccount {
  id: string;
  name: string;
  email: string;
  role: string;
  organization_id: string;
  organization_name: string;
  title: string;
  warehouse: string;
  avatar_url: string;
  preferences: {
    theme: "system" | "light" | "dark";
    font_scale: "compact" | "comfortable" | "large";
    density: "compact" | "comfortable";
    show_progress: boolean;
  };
}

export interface PresenceAccount extends UserAccount {
  online: boolean;
}

export interface Organization {
  id: string;
  name: string;
  plan: string;
  seat_count: number;
  seat_limit: number;
  warehouse: string;
}

export interface Preset {
  id: string;
  name: string;
  type: string;
  default_count: number;
  requirements: Requirement[];
  tags: string[];
  description: string;
  class_id: string;
  manufacturer: string;
  model: string;
  capabilities: string[];
  connectors: string[];
  attributes: Record<string, string | number | boolean>;
  quality_score: number;
  preference_score: number;
  weight_kg: number;
}

export interface EventTemplate {
  id: string;
  name: string;
  category?: string;
  description: string;
  items: Requirement[];
}

export interface PlanLine {
  item_id: string;
  amount: number;
  available: number;
  missing: number;
  source: string;
  type: string | null;
  preset_id: string | null;
  reserved_elsewhere: number;
  conflict: boolean;
  capability: string;
  level: "required" | "recommended" | "optional";
  selected_class_id: string;
  score: number;
  reasons: string[];
  alternatives: Array<{
    item_id: string;
    class_id: string;
    score: number;
    available: number;
    reasons: string[];
  }>;
  substitution: boolean;
  required_amount: number;
  recommended_amount: number;
  optional_amount: number;
  required_missing: number;
  recommended_missing: number;
  optional_missing: number;
  components: Array<{
    capability: string;
    level: "required" | "recommended" | "optional";
    amount: number;
    missing: number;
    source: string;
  }>;
}

export interface EventPlan {
  requested_items: Requirement[];
  lines: PlanLine[];
  unresolved_items: string[];
  capability_requirements: CapabilityRequirement[];
  reallocations: Array<{
    event_id: string;
    event_title: string;
    removed: Record<string, number>;
    added: Record<string, number>;
    required_missing: number;
    reason: string;
  }>;
  allocation_updates: Array<{ event_id: string; plan: EventPlan }>;
  transport_summary: {
    payload_kg?: number;
    volume_m3?: number;
    vehicle_capability?: string;
    vehicle_label?: string;
    cart_count?: number;
    basis?: string;
  };
  is_ready: boolean;
  total_missing: number;
  recommended_missing: number;
}

export interface ChecklistItem {
  id: string;
  item_id: string;
  amount: number;
  phase: "pack" | "return";
  done: boolean;
}

export interface HistoryEntry {
  action: string;
  actor_id: string;
  note: string;
  timestamp: string;
}

export interface StockMovement {
  id: string;
  idempotency_key: string;
  organization_id: string;
  event_id: string;
  action: "dispatch" | "return";
  actor_id: string;
  lines: Array<{
    scope: "shared" | "personal";
    owner_user_id: string;
    item_id: string;
    amount: number;
  }>;
  created_at: string;
}

export interface EventRecord {
  id: string;
  title: string;
  description: string;
  start_date: string;
  start_time: string;
  duration_minutes: number;
  location: string;
  source_type: string;
  source_id: string;
  status: "planning" | "confirmed" | "packed" | "out" | "returned";
  organization_id: string;
  owner_id: string;
  assigned_user_ids: string[];
  requested_items: Requirement[];
  capability_requirements: CapabilityRequirement[];
  milestones: Array<{ time: string; label: string }>;
  attendee_count: number;
  event_size: "small" | "medium" | "large" | "festival";
  venue_kind: string;
  priority_score: number;
  plan: EventPlan;
  checklist: ChecklistItem[];
  return_checklist: ChecklistItem[];
  conflicts: PlanLine[];
  history: HistoryEntry[];
  movements: StockMovement[];
  sync_status: string;
  google_calendar_payload: Record<string, unknown>;
  created_at: string;
}

export interface EventDraft {
  event: EventRecord;
  learned_items: Requirement[];
}

export interface WorkspaceState {
  auth: {
    authenticated: boolean;
    user: UserAccount | null;
    users: UserAccount[];
    demo_available: boolean;
  };
  organization: Partial<Organization>;
  presence: PresenceAccount[];
  inventory: InventoryCollection;
  inventories: Record<InventoryScope, InventoryCollection>;
  presets: { presets: Preset[]; tags: string[] };
  templates: { templates: EventTemplate[]; kits: EventTemplate[] };
  item_classes: { classes: ItemClass[] };
  integrations: Record<"crm" | "google_sheets" | "excel", {
    id: string;
    name: string;
    provider: string;
    status: "not_configured" | "needs_credentials" | "ready";
    endpoint?: string;
    spreadsheet_id?: string;
    sheet_name?: string;
    credential_env?: string;
    credential_present?: boolean;
    last_sync?: string;
    last_action?: string;
    note: string;
  }>;
  events: {
    events: EventRecord[];
    learning_count: number;
    active_reservations: Record<string, number>;
  };
  google_calendar: {
    status: string;
    calendar_id: string;
    write_requires_approval: boolean;
  };
  sync: {
    status: string;
    account_id: string;
    google_calendar: string;
    pending_events: string[];
    last_sync: string;
    note: string;
  };
  save_path: string;
}

export interface StateEnvelope {
  state: WorkspaceState;
  [key: string]: unknown;
}
