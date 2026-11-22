import { useEffect, useState } from "react";
import { AtRisk, DataQuality, metrics, Overview, SegmentRow, TicketRow } from "../api";

const usd = (n: number) =>
  n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(0)}K` : `$${n.toFixed(0)}`;
const int = (n: number) => n.toLocaleString("en-US");

function Kpi({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {note && <div className="kpi-note">{note}</div>}
    </div>
  );
}

function RevenueBars({ rows }: { rows: SegmentRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.billed_usd));
  return (
    <div className="bars">
      {rows.map((r) => (
        <div className="bar-row" key={r.segment}>
          <div className="bar-label">
            {r.segment}
            <small>
              {int(r.customers)} customers · {int(r.accounts)} accounts
            </small>
          </div>
          <div className="bar-track" title={`billed ${usd(r.billed_usd)}, paid ${usd(r.paid_usd)}`}>
            <div className="bar paid" style={{ width: `${(r.paid_usd / max) * 100}%` }} />
            <div className="bar outstanding" style={{ width: `${(r.outstanding_usd / max) * 100}%` }} />
          </div>
          <div className="bar-value">
            {usd(r.paid_usd)} <small>+ {usd(r.outstanding_usd)} open</small>
          </div>
        </div>
      ))}
      <div className="legend">
        <span className="sw paid" /> paid <span className="sw outstanding" /> unpaid or overdue (USD, fixed FX)
      </div>
    </div>
  );
}

const RULE_KEYS: Record<string, keyof AtRisk["accounts"][number]> = {
  overdue: "r_overdue",
  ticket_load: "r_ticket_load",
  low_csat: "r_low_csat",
  revenue_drop: "r_revenue_drop",
};

export default function OverviewPage() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [rev, setRev] = useState<SegmentRow[]>([]);
  const [tix, setTix] = useState<TicketRow[]>([]);
  const [risk, setRisk] = useState<AtRisk | null>(null);
  const [dq, setDq] = useState<DataQuality | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([metrics.overview(), metrics.revenue(), metrics.openTickets(), metrics.atRisk(), metrics.dataQuality()])
      .then(([o, r, t, k, d]) => {
        setOv(o);
        setRev(r);
        setTix(t);
        setRisk(k);
        setDq(d);
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="error">{error} (run `uv run onboard run`, then start the API on :8003)</p>;
  if (!ov || !risk || !dq) return <p className="muted">Loading…</p>;
  const c = ov.counts;

  return (
    <div className="overview">
      <div className="kpis">
        <Kpi label="Customers (after dedup)" value={int(c.customers)} note={`from ${int(c.customer_records)} source records`} />
        <Kpi label="Accounts" value={int(c.accounts)} />
        <Kpi label="Invoices" value={int(c.invoices)} />
        <Kpi label="Tickets" value={int(c.tickets)} />
        <Kpi label="Accounts at risk" value={int(c.at_risk)} note={`${usd(risk.total.billed)} billed`} />
        <Kpi label="Rows quarantined" value={int(c.quarantined)} note="held back, with reasons" />
      </div>

      <div className="grid2">
        <section className="card">
          <h2>Revenue by segment</h2>
          <RevenueBars rows={rev} />
        </section>
        <section className="card">
          <h2>Open tickets per account</h2>
          <table>
            <thead>
              <tr>
                <th>Account</th>
                <th>Customer</th>
                <th>Open</th>
                <th>Oldest open</th>
              </tr>
            </thead>
            <tbody>
              {tix.map((t) => (
                <tr key={t.account_id}>
                  <td>
                    <code>{t.account_id}</code>
                  </td>
                  <td>
                    {t.full_name}
                    <small className="muted"> · {t.company_name ?? "-"} · {t.segment}</small>
                  </td>
                  <td>
                    {t.open_tickets} / {t.total_tickets}
                  </td>
                  <td>{t.oldest_open_days ?? "-"} days</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <section className="card">
        <h2>Accounts at risk</h2>
        <p className="muted">
          Flagged when {risk.min_rules} or more of these rules fire (as of {ov.as_of}):
        </p>
        <ul className="rules">
          {risk.rules.map((r) => (
            <li key={r.id}>
              <span className={`rule-tag r-${r.id}`}>{r.label}</span> {r.explain}{" "}
              <small className="muted">({risk.rule_counts[r.id] ?? 0} flagged accounts)</small>
            </li>
          ))}
        </ul>
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Customer</th>
              <th>Billed</th>
              <th>Why</th>
            </tr>
          </thead>
          <tbody>
            {risk.accounts.map((a) => (
              <tr key={a.account_id}>
                <td>
                  <code>{a.account_id}</code>
                  <small className="muted"> {a.plan}</small>
                </td>
                <td>
                  {a.full_name}
                  <small className="muted"> · {a.company_name ?? "-"} · {a.segment}</small>
                </td>
                <td>{usd(a.billed_usd)}</td>
                <td>
                  {risk.rules
                    .filter((r) => a[RULE_KEYS[r.id]])
                    .map((r) => (
                      <span key={r.id} className={`rule-tag r-${r.id}`} title={r.explain}>
                        {r.label}
                      </span>
                    ))}
                  <small className="muted">
                    {" "}
                    overdue {a.overdue_invoices} · open tickets {a.open_tickets} · CSAT{" "}
                    {a.csat_180 == null ? "-" : a.csat_180.toFixed(1)} · last 90d {usd(a.billed_last_90)} vs{" "}
                    {usd(a.billed_prev_90)}
                  </small>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Data quality</h2>
        <div className="grid2">
          <div>
            <h3>Quarantined rows by reason</h3>
            <table>
              <tbody>
                {dq.quarantine_by_reason.map((q) => (
                  <tr key={q.check_name + q.detail}>
                    <td>
                      <span className="pill">{q.check_name}</span> {q.detail}
                    </td>
                    <td className="num">{int(q.n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h3>Entity resolution</h3>
            <p>
              {int(dq.entity_resolution.records)} records → {int(dq.entity_resolution.customers)} customers;{" "}
              {int(dq.entity_resolution.links)} links recorded in merge history (lowest accepted score{" "}
              {dq.entity_resolution.min_score?.toFixed(2)}).
            </p>
          </div>
          <div>
            <h3>All checks</h3>
            <table>
              <thead>
                <tr>
                  <th>Table</th>
                  <th>Check</th>
                  <th>Action</th>
                  <th className="num">Rows</th>
                </tr>
              </thead>
              <tbody>
                {dq.checks.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <code>{r.table_key}</code>
                    </td>
                    <td>{r.check_name}</td>
                    <td className="muted">{r.severity}</td>
                    <td className="num">
                      {int(r.rows_affected)} <small className="muted">/ {int(r.rows_checked)}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <details>
          <summary>Sample quarantined rows (raw, as received)</summary>
          {dq.quarantine_sample.map((q, i) => (
            <pre key={i}>
              {q.table_key} row {q.row_number} [{q.check_name}] {q.detail}
              {"\n"}
              {q.raw_json}
            </pre>
          ))}
        </details>
      </section>
    </div>
  );
}
