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
