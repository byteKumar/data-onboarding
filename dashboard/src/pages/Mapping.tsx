import { useEffect, useMemo, useState } from "react";
import { api, Field, SuggestionDoc, TableInfo } from "../api";

type Row = { column: string; target: string; accepted: boolean };

// Rules and stored mappings above this confidence start accepted; LLM suggestions never do.
const AUTO_ACCEPT = 0.8;

function sourceLabel(method: string) {
  if (method === "store") return "stored";
  if (method.startsWith("rule")) return "rule";
  if (method === "llm") return "LLM";
  return "none";
}

export default function MappingPage() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [fields, setFields] = useState<Field[]>([]);
  const [key, setKey] = useState<string>("");
  const [doc, setDoc] = useState<SuggestionDoc | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [message, setMessage] = useState<string>("");
  const [serverRejections, setServerRejections] = useState<Record<string, string>>({});
  const [error, setError] = useState<string>("");

  const loadTables = () =>
    api.tables().then((t) => {
      setTables(t);
      if (!key && t.length) setKey(t[0].key);
    });

  useEffect(() => {
    loadTables().catch((e) => setError(String(e)));
    api.fields().then(setFields).catch((e) => setError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!key) return;
    setMessage("");
    setServerRejections({});
    api
      .suggestions(key)
      .then((d) => {
        setDoc(d);
        setRows(
          d.suggestions.map((s) => ({
            column: s.column,
            target: s.target ?? "ignore",
            accepted: s.method !== "llm" && s.target !== null && s.confidence >= AUTO_ACCEPT,
          })),
        );
      })
      .catch((e) => setError(String(e)));
  }, [key]);

  const allowed = useMemo(
    () => fields.filter((f) => !doc || f.entities.includes("*") || f.entities.includes(doc.entity)),
    [fields, doc],
  );
  const profiles = useMemo(() => Object.fromEntries((doc?.profiles ?? []).map((p) => [p.column, p])), [doc]);
  const pending = rows.filter((r) => !r.accepted).length;

  const update = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const submit = async () => {
    if (!doc) return;
    const res = await api.confirm(
      doc.table_key,
      rows.map((r) => ({ column: r.column, target: r.target })),
    );
    if (res.ok) {
      setMessage(`Saved. Fingerprint ${res.body.fingerprint}: next export with these columns reuses this mapping.`);
      setServerRejections({});
      loadTables();
    } else {
      const det = res.body.detail ?? {};
      const rej: Record<string, string> = {};
      for (const r of det.rejected ?? []) rej[r.column] = rej[r.column] ? `${rej[r.column]}; ${r.reason}` : r.reason;
      setServerRejections(rej);
      setMessage(`Not saved: ${Object.keys(rej).length} mapping(s) rejected by validators.`);
    }
  };

  if (error) return <p className="error">{error} (is the API running on :8003?)</p>;

  return (
    <div className="mapping">
      <aside>
        <h2>Tables</h2>
        <ul>
          {tables.map((t) => (
            <li key={t.key} className={t.key === key ? "sel" : ""} onClick={() => setKey(t.key)}>
              <strong>{t.key}</strong>
              <span className={t.confirmed ? "pill ok" : "pill warn"}>{t.confirmed ? "confirmed" : "review"}</span>
              <small>
                {t.stats.resolved_by_store === t.stats.columns
                  ? `${t.entity} · ${t.stats.columns} columns from the stored mapping`
                  : `${t.entity} · rules ${t.stats.resolved_by_rules} · LLM ${t.stats.resolved_by_llm} · open ${t.stats.unresolved}`}
              </small>
            </li>
          ))}
        </ul>
      </aside>
      <section>
        {doc && (
          <>
            <h2>
              {doc.table_key} <small>({doc.entity} rows, suggestions from rules{doc.provider !== "none" ? ` + ${doc.provider} LLM` : ""})</small>
            </h2>
            <table>
              <thead>
                <tr>
                  <th>Source column</th>
                  <th>Profile</th>
                  <th>Proposed field</th>
                  <th>Confidence</th>
                  <th>From</th>
                  <th>Validator</th>
                  <th>Accept</th>
                </tr>
              </thead>
              <tbody>
                {doc.suggestions.map((s, i) => {
                  const p = profiles[s.column];
                  const row = rows[i];
                  if (!row) return null;
                  const changed = row.target !== (s.target ?? "ignore");
                  const serverErr = serverRejections[s.column];
                  return (
                    <tr key={s.column} className={row.accepted ? "" : "todo"}>
                      <td>
                        <code>{s.column}</code>
                      </td>
                      <td className="profile">
                        <span className="pill">{p?.pattern}</span> null {Math.round((p?.null_rate ?? 0) * 100)}% ·{" "}
                        {p?.distinct_count} distinct
                        <div className="samples">{p?.samples.slice(0, 3).join(" | ")}</div>
                      </td>
                      <td>
                        <select value={row.target} onChange={(e) => update(i, { target: e.target.value, accepted: true })}>
                          {allowed.map((f) => (
                            <option key={f.name} value={f.name} title={f.description}>
                              {f.name}
                            </option>
                          ))}
                        </select>
                        {changed && <div className="fixed">fixed by you</div>}
                      </td>
                      <td>{s.target ? s.confidence.toFixed(2) : "-"}</td>
                      <td>
                        <span className={`pill src-${sourceLabel(s.method)}`}>{sourceLabel(s.method)}</span>
                      </td>
                      <td className="validator">
                        {serverErr ? (
                          <span className="bad">rejected: {serverErr}</span>
                        ) : s.target ? (
                          <span className="good">passed</span>
                        ) : (
                          <span className="muted">{s.reason}</span>
                        )}
                        {s.rejected.length > 0 && (
                          <details>
                            <summary>{s.rejected.length} refused</summary>
                            {s.rejected.map((r, k) => (
                              <div key={k}>
                                {r.method} → {r.target}: {r.reason}
                              </div>
                            ))}
                          </details>
                        )}
                      </td>
                      <td>
                        <input type="checkbox" checked={row.accepted} onChange={(e) => update(i, { accepted: e.target.checked })} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="actions">
              <button onClick={() => setRows((rs) => rs.map((r) => ({ ...r, accepted: true })))}>Accept all</button>
              <button className="primary" disabled={pending > 0} onClick={submit}>
                Confirm mapping {pending > 0 ? `(${pending} to review)` : ""}
              </button>
              {message && <span className="msg">{message}</span>}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
