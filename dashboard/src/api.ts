export type Profile = {
  column: string;
  dtype: string;
  null_rate: number;
  distinct_count: number;
  samples: string[];
  pattern: string;
};

export type Rejection = { target: string; method: string; reason: string };

export type Suggestion = {
  column: string;
  target: string | null;
  confidence: number;
  method: string;
  status: string;
  reason: string;
  rejected: Rejection[];
};

export type SuggestionDoc = {
  table_key: string;
  entity: string;
  fingerprint: string;
  provider: string;
  profiles: Profile[];
  suggestions: Suggestion[];
  stats: Record<string, number>;
  confirmed: boolean;
};

export type TableInfo = {
  key: string;
  entity: string;
  kind: string;
  fingerprint: string;
  confirmed: boolean;
  provider: string;
  stats: Record<string, number>;
};

export type Field = { name: string; description: string; entities: string[] };

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const api = {
  tables: () => get<TableInfo[]>("/api/tables"),
  fields: () => get<Field[]>("/api/fields"),
  suggestions: (key: string) => get<SuggestionDoc>(`/api/tables/${key}/suggestions`),
  confirm: async (key: string, mappings: { column: string; target: string }[]) => {
    const r = await fetch(`/api/tables/${key}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings, confirmed_by: "dashboard" }),
    });
    return { ok: r.ok, body: await r.json() };
  },
};

export type Overview = {
  as_of: string;
  counts: Record<string, number>;
  last_run: { run_id: string; finished_at: string; total_s: number } | null;
};
export type SegmentRow = {
  segment: string;
  customers: number;
  accounts: number;
  paid_usd: number;
  billed_usd: number;
  outstanding_usd: number;
};
export type TicketRow = {
  account_id: string;
  full_name: string;
  company_name: string | null;
  segment: string;
  open_tickets: number;
  total_tickets: number;
  oldest_open_days: number | null;
};
export type RiskRule = { id: string; label: string; explain: string };
export type RiskAccount = {
  account_id: string;
  full_name: string;
  company_name: string | null;
  segment: string;
  plan: string;
  billed_usd: number;
  outstanding_usd: number;
  overdue_invoices: number;
  open_tickets: number;
  csat_180: number | null;
  billed_last_90: number;
  billed_prev_90: number;
  rules_fired: number;
  r_overdue: boolean;
  r_ticket_load: boolean;
  r_low_csat: boolean;
  r_revenue_drop: boolean;
};
export type AtRisk = {
  min_rules: number;
  rules: RiskRule[];
  total: { n: number; billed: number };
  rule_counts: Record<string, number>;
  accounts: RiskAccount[];
};
export type DataQuality = {
  checks: { table_key: string; check_name: string; severity: string; rows_affected: number; rows_checked: number }[];
  quarantine_by_reason: { check_name: string; detail: string; n: number }[];
  quarantine_sample: { table_key: string; row_number: number; row_key: string | null; check_name: string; detail: string; raw_json: string }[];
  entity_resolution: { records: number; customers: number; merged_customers: number; links: number; min_score: number };
};

export const metrics = {
  overview: () => get<Overview>("/api/metrics/overview"),
  revenue: () => get<SegmentRow[]>("/api/metrics/revenue_by_segment"),
  openTickets: () => get<TicketRow[]>("/api/metrics/open_tickets?limit=12"),
  atRisk: () => get<AtRisk>("/api/metrics/at_risk?limit=25"),
  dataQuality: () => get<DataQuality>("/api/metrics/data_quality"),
};
