import { useEffect, useMemo, useRef, useState } from "react";

// Terminal / Bloomberg-ish aesthetic borrowed from oracle-sentinel dashboard
const BLUE_BRIGHT = "#4da6ff";
const BLUE_MID = "#2d7fd4";
const BLUE_DIM = "#1a5a9e";
const BLUE_DARK = "#0d2847";
const ICE = "#c8ddf0";
const FROST = "#8badc4";
const SLATE = "#5a7184";
const RED_COLD = "#e05565";
const TEAL = "#4ecdc4";
const AMBER_COLD = "#d4a843";
const BG = "#080c12";
const BG_PANEL = "#0b1018";
const BORDER = "#141e2e";
const BORDER_LIGHT = "#1c2d42";
const GRID_LINE = "#0f1924";

const API = "/api";

function Styles() {
  return (
    <style>{`
      @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');
      @keyframes fadeInUp { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
      @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
      .panel { background: ${BG_PANEL}; border: 1px solid ${BORDER}; border-radius: 3px; overflow: hidden; }
      .panel-head { background: linear-gradient(90deg, ${BLUE_DARK}25, transparent 70%); border-bottom: 1px solid ${BORDER}; padding: 7px 14px; display: flex; align-items: center; gap: 8px; }
      .row-hover:hover { background: ${BLUE_DARK}18 !important; }
      .tab-btn { background: transparent; border: none; border-bottom: 2px solid transparent; color: ${SLATE}; font-family: 'JetBrains Mono', monospace; font-size: 13px; font-weight: 500; letter-spacing: 0.5px; padding: 10px 20px; cursor: pointer; transition: all 0.2s; }
      .tab-btn:hover { color: ${FROST}; background: ${BLUE_DARK}15; }
      .tab-btn.active { color: ${BLUE_BRIGHT}; border-bottom-color: ${BLUE_MID}; background: ${BLUE_DARK}20; }
      ::-webkit-scrollbar { width: 5px; } ::-webkit-scrollbar-track { background: ${BG}; } ::-webkit-scrollbar-thumb { background: ${BORDER_LIGHT}; border-radius: 3px; }
    `}</style>
  );
}

function Scanlines() {
  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background:
          "repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(13,40,71,0.02) 3px, rgba(13,40,71,0.02) 4px)",
        pointerEvents: "none",
        zIndex: 9999,
      }}
    />
  );
}

function Panel({ title, children, headerRight = null }) {
  return (
    <div className="panel">
      <div className="panel-head">
        <span
          style={{
            color: BLUE_BRIGHT,
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 12,
            fontWeight: 600,
            letterSpacing: "1.5px",
            textTransform: "uppercase",
          }}
        >
          {title}
        </span>
        {headerRight && <div style={{ marginLeft: "auto" }}>{headerRight}</div>}
      </div>
      <div style={{ padding: "10px 14px" }}>{children}</div>
    </div>
  );
}

function Metric({ label, value, color = BLUE_BRIGHT }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div
        style={{
          color,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 18,
          fontWeight: 700,
        }}
      >
        {value}
      </div>
      <div
        style={{
          color: SLATE,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12,
          letterSpacing: 1,
          marginTop: 3,
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
    </div>
  );
}

function Header() {
  return (
    <div
      style={{
        textAlign: "center",
        padding: "16px 0 8px",
        borderBottom: `1px solid ${BORDER}`,
      }}
    >
      <div
        style={{
          color: BLUE_BRIGHT,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 16,
          fontWeight: 700,
          letterSpacing: 4,
        }}
      >
        KALSHI SENTINEL
      </div>
      <div
        style={{
          color: SLATE,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 12,
          letterSpacing: 3,
          marginTop: 4,
        }}
      >
        NEWS-DRIVEN EVENT MARKET INTELLIGENCE v0.1
      </div>
    </div>
  );
}

function StatusBar({ statusOk }) {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const iv = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(iv);
  }, []);

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "7px 20px",
        background: `linear-gradient(90deg, ${BLUE_DARK}12, ${BG_PANEL}, ${BLUE_DARK}12)`,
        borderBottom: `1px solid ${BORDER}`,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 13,
      }}
    >
      <div style={{ display: "flex", gap: 28, alignItems: "center" }}>
        <span style={{ color: statusOk ? TEAL : RED_COLD, display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ animation: "pulse 2s infinite", fontSize: 12 }}>●</span>
          {statusOk ? "CONNECTED" : "OFFLINE"}
        </span>
        <span style={{ color: SLATE }}>
          ENV <span style={{ color: FROST }}>LOCAL</span>
        </span>
      </div>
      <div style={{ display: "flex", gap: 28 }}>
        <span style={{ color: SLATE }}>{time.toISOString().replace("T", " ").slice(0, 19)} UTC</span>
      </div>
    </div>
  );
}

function ColHeaders({ columns }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: columns.map((c) => c.w).join(" "),
        gap: 8,
        padding: "6px 10px",
        fontSize: 12,
        color: SLATE,
        letterSpacing: 1,
        fontWeight: 600,
        borderBottom: `1px solid ${BORDER}`,
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      {columns.map((c) => (
        <span key={c.l} style={{ textAlign: c.a || "left" }}>
          {c.l}
        </span>
      ))}
    </div>
  );
}

function PortfolioTab() {
  const [snap, setSnap] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    const load = async () => {
      try {
        setErr(null)
        const j = await fetch(API + '/status/positions_mtm').then(r => r.json())
        setSnap(j)
      } catch (e) {
        setErr(String(e))
      }
    }
    load()
    const iv = setInterval(load, 5000)
    return () => clearInterval(iv)
  }, [])

  const rows = snap?.rows || []
  const totals = snap?.totals

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 14 }}>
      <Panel title="POSITION MTM (APPROX)">
        {err && <pre style={{ color: RED_COLD, whiteSpace: 'pre-wrap' }}>{err}</pre>}
        {totals && (
          <div style={{ display: 'flex', gap: 18, fontSize: 12, color: SLATE, marginBottom: 10 }}>
            <span>COST ${(totals.cost_basis_cents/100).toFixed(2)}</span>
            <span>LIQ ${(totals.liq_value_cents/100).toFixed(2)}</span>
            <span>UNREAL ${(totals.unreal_pnl_cents/100).toFixed(2)}</span>
          </div>
        )}
        <ColHeaders columns={[{l:'TICKER',w:'220px'},{l:'SIDE',w:'55px'},{l:'QTY',w:'60px',a:'right'},{l:'AVG',w:'70px',a:'right'},{l:'COST',w:'80px',a:'right'},{l:'EXIT BID',w:'70px',a:'right'},{l:'EXIT ASK',w:'70px',a:'right'},{l:'LIQ',w:'80px',a:'right'},{l:'UNREAL',w:'80px',a:'right'}]} />
        <div style={{ maxHeight: '70vh', overflowY: 'auto' }}>
          {rows.map((r) => (
            <div key={r.ticker + ':' + (r.side || '')} className="row-hover" style={{ display:'grid', gridTemplateColumns:'220px 55px 60px 70px 80px 70px 70px 80px 80px', gap:8, padding:'8px 10px', borderBottom:`1px solid ${GRID_LINE}`, fontSize:12 }}>
              <span style={{ color: ICE, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{r.ticker}</span>
              <span style={{ color: r.side === 'yes' ? TEAL : RED_COLD }}>{String(r.side || '').toUpperCase()}</span>
              <span style={{ color: FROST, textAlign:'right' }}>{r.qty}</span>
              <span style={{ color: FROST, textAlign:'right' }}>{r.avg_entry_cents}</span>
              <span style={{ color: FROST, textAlign:'right' }}>{(r.cost_basis_cents/100).toFixed(2)}</span>
              <span style={{ color: TEAL, textAlign:'right' }}>{r.best_exit_bid}</span>
              <span style={{ color: AMBER_COLD, textAlign:'right' }}>{r.implied_exit_ask}</span>
              <span style={{ color: FROST, textAlign:'right' }}>{(r.liq_value_cents/100).toFixed(2)}</span>
              <span style={{ color: r.unreal_pnl_cents >= 0 ? TEAL : RED_COLD, textAlign:'right' }}>{(r.unreal_pnl_cents/100).toFixed(2)}</span>
            </div>
          ))}
          {rows.length === 0 && <div style={{ padding: 18, color: SLATE }}>No open positions.</div>}
        </div>
        <div style={{ marginTop: 8, color: SLATE, fontSize: 11, lineHeight: 1.5 }}>
          MTM is approximate: liquidation uses best bid on your position side (YES→best YES bid, NO→best NO bid). Ignores slippage.
        </div>
      </Panel>
    </div>
  )
}

function PaperTab() {
  const [resp, setResp] = useState(null)
  const [trades, setTrades] = useState([])
  const [err, setErr] = useState(null)
  const [hoursAhead, setHoursAhead] = useState(24)
  const [budget, setBudget] = useState(10)
  const [maxTrades, setMaxTrades] = useState(3)
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    setErr(null)
    try {
      const r = await fetch(API + '/paper/run_today', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hours_ahead: hoursAhead, budget_dollars: budget, max_trades: maxTrades })
      })
      const j = await r.json()
      setResp(j)
      const t = await fetch(API + '/paper/trades').then(x => x.json())
      setTrades(t)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    fetch(API + '/paper/trades').then(r => r.json()).then(setTrades).catch(() => {})
  }, [])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '360px 1fr', gap: 14 }}>
      <Panel title="PAPER RUNNER">
        {err && <pre style={{ color: RED_COLD, whiteSpace: 'pre-wrap' }}>{err}</pre>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, fontSize: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: SLATE }}>HOURS AHEAD</span>
            <input value={hoursAhead} onChange={e => setHoursAhead(Number(e.target.value))} style={{ width: 120, background: BG, border: `1px solid ${BORDER_LIGHT}`, color: ICE, padding: '4px 6px' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: SLATE }}>BUDGET ($)</span>
            <input value={budget} onChange={e => setBudget(Number(e.target.value))} style={{ width: 120, background: BG, border: `1px solid ${BORDER_LIGHT}`, color: ICE, padding: '4px 6px' }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ color: SLATE }}>MAX TRADES</span>
            <input value={maxTrades} onChange={e => setMaxTrades(Number(e.target.value))} style={{ width: 120, background: BG, border: `1px solid ${BORDER_LIGHT}`, color: ICE, padding: '4px 6px' }} />
          </div>
          <button onClick={run} disabled={busy} style={{ background: busy ? BORDER : `linear-gradient(135deg, ${BLUE_MID}, ${BLUE_DIM})`, border: 'none', borderRadius: 4, padding: '8px 10px', color: busy ? SLATE : ICE, fontFamily: "'JetBrains Mono', monospace", fontSize: 12, cursor: busy ? 'not-allowed' : 'pointer' }}>
            {busy ? 'RUNNING...' : 'RUN PAPER MODE'}
          </button>
        </div>
        {resp && (
          <pre style={{ marginTop: 12, color: FROST, fontSize: 12, whiteSpace: 'pre-wrap' }}>{JSON.stringify(resp, null, 2)}</pre>
        )}
      </Panel>

      <Panel title={`PROPOSED TRADES (${trades.length})`}>
        <ColHeaders columns={[{l:'TS',w:'160px'},{l:'TICKER',w:'200px'},{l:'SIDE',w:'60px'},{l:'PX',w:'60px',a:'right'},{l:'QTY',w:'60px',a:'right'},{l:'MAXLOSS',w:'80px',a:'right'},{l:'STATUS',w:'90px'}]} />
        <div style={{ maxHeight: '70vh', overflowY: 'auto' }}>
          {trades.map((t) => (
            <div key={t.id} className="row-hover" style={{ display: 'grid', gridTemplateColumns: '160px 200px 60px 60px 60px 80px 90px', gap: 8, padding: '8px 10px', borderBottom: `1px solid ${GRID_LINE}`, fontSize: 12 }}>
              <span style={{ color: SLATE, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.ts}</span>
              <span style={{ color: ICE, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.ticker}</span>
              <span style={{ color: t.side === 'YES' ? TEAL : RED_COLD }}>{t.side}</span>
              <span style={{ color: FROST, textAlign: 'right' }}>{t.limit_price_cents}</span>
              <span style={{ color: FROST, textAlign: 'right' }}>{t.contracts}</span>
              <span style={{ color: AMBER_COLD, textAlign: 'right' }}>{(t.estimated_max_loss_cents/100).toFixed(2)}</span>
              <span style={{ color: BLUE_BRIGHT }}>{t.status}</span>
            </div>
          ))}
          {trades.length === 0 && <div style={{ padding: 18, color: SLATE }}>No paper trades proposed yet.</div>}
        </div>
      </Panel>
    </div>
  )
}

function TradesTab() {
  const [orders, setOrders] = useState([])
  const [fills, setFills] = useState([])
  const [err, setErr] = useState(null)

  useEffect(() => {
    const load = async () => {
      try {
        setErr(null)
        const o = await fetch(API + '/kalshi/portfolio/orders?limit=50').then(r => r.json())
        const f = await fetch(API + '/kalshi/portfolio/fills?limit=50').then(r => r.json())
        setOrders(o.orders || [])
        setFills(f.fills || [])
      } catch (e) {
        setErr(String(e))
      }
    }
    load()
    const iv = setInterval(load, 5000)
    return () => clearInterval(iv)
  }, [])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
      <Panel title={`ORDERS (${orders.length})`}>
        {err && <pre style={{ color: RED_COLD, whiteSpace: 'pre-wrap' }}>{err}</pre>}
        <ColHeaders columns={[{l:'TIME',w:'160px'},{l:'TICKER',w:'210px'},{l:'SIDE',w:'50px'},{l:'ACT',w:'55px'},{l:'PX',w:'50px',a:'right'},{l:'FILL',w:'55px',a:'right'},{l:'REM',w:'55px',a:'right'},{l:'STATUS',w:'90px'}]} />
        <div style={{ maxHeight: '70vh', overflowY: 'auto' }}>
          {orders.map((o) => (
            <div key={o.order_id} className="row-hover" style={{ display:'grid', gridTemplateColumns:'160px 210px 50px 55px 50px 55px 55px 90px', gap:8, padding:'8px 10px', borderBottom:`1px solid ${GRID_LINE}`, fontSize:12 }}>
              <span style={{ color: SLATE, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{(o.created_time || '').replace('T',' ').replace('Z','')}</span>
              <span style={{ color: ICE, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{o.ticker}</span>
              <span style={{ color: o.side === 'yes' ? TEAL : RED_COLD }}>{String(o.side || '').toUpperCase()}</span>
              <span style={{ color: FROST }}>{String(o.action || '').toUpperCase()}</span>
              <span style={{ color: FROST, textAlign:'right' }}>{o.side === 'yes' ? o.yes_price : o.no_price}</span>
              <span style={{ color: TEAL, textAlign:'right' }}>{o.fill_count}</span>
              <span style={{ color: SLATE, textAlign:'right' }}>{o.remaining_count}</span>
              <span style={{ color: BLUE_BRIGHT }}>{String(o.status || '').toUpperCase()}</span>
            </div>
          ))}
          {orders.length === 0 && <div style={{ padding: 18, color: SLATE }}>No orders yet.</div>}
        </div>
      </Panel>

      <Panel title={`FILLS (${fills.length})`}>
        <ColHeaders columns={[{l:'TIME',w:'160px'},{l:'TICKER',w:'210px'},{l:'SIDE',w:'50px'},{l:'ACT',w:'55px'},{l:'PX',w:'50px',a:'right'},{l:'QTY',w:'55px',a:'right'},{l:'COST',w:'70px',a:'right'},{l:'FEES',w:'60px',a:'right'}]} />
        <div style={{ maxHeight: '70vh', overflowY: 'auto' }}>
          {fills.map((x) => (
            <div key={x.fill_id || x.trade_id || Math.random()} className="row-hover" style={{ display:'grid', gridTemplateColumns:'160px 210px 50px 55px 50px 55px 70px 60px', gap:8, padding:'8px 10px', borderBottom:`1px solid ${GRID_LINE}`, fontSize:12 }}>
              <span style={{ color: SLATE, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{(x.executed_time || '').replace('T',' ').replace('Z','')}</span>
              <span style={{ color: ICE, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{x.ticker}</span>
              <span style={{ color: x.side === 'yes' ? TEAL : RED_COLD }}>{String(x.side || '').toUpperCase()}</span>
              <span style={{ color: FROST }}>{String(x.action || '').toUpperCase()}</span>
              <span style={{ color: FROST, textAlign:'right' }}>{x.side === 'yes' ? x.yes_price : x.no_price}</span>
              <span style={{ color: TEAL, textAlign:'right' }}>{x.count}</span>
              <span style={{ color: AMBER_COLD, textAlign:'right' }}>{x.cost}</span>
              <span style={{ color: SLATE, textAlign:'right' }}>{x.fees}</span>
            </div>
          ))}
          {fills.length === 0 && <div style={{ padding: 18, color: SLATE }}>No fills yet.</div>}
        </div>
      </Panel>
    </div>
  )
}

function AuditTab() {
  const [items, setItems] = useState([])
  useEffect(() => {
    const load = () => fetch(API + '/audit').then(r => r.json()).then(setItems).catch(() => {})
    load()
    const iv = setInterval(load, 5000)
    return () => clearInterval(iv)
  }, [])

  return (
    <Panel title={`AUDIT LOG (${items.length})`}>
      <ColHeaders columns={[{l:'TS',w:'160px'},{l:'LVL',w:'60px'},{l:'COMP',w:'110px'},{l:'MSG',w:'1fr'}]} />
      <div style={{ maxHeight: '70vh', overflowY: 'auto' }}>
        {items.map((it) => (
          <div key={it.id} className="row-hover" style={{ display: 'grid', gridTemplateColumns: '160px 60px 110px 1fr', gap: 8, padding: '8px 10px', borderBottom: `1px solid ${GRID_LINE}`, fontSize: 12 }}>
            <span style={{ color: SLATE, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.ts}</span>
            <span style={{ color: it.level === 'ERROR' ? RED_COLD : (it.level === 'WARN' ? AMBER_COLD : TEAL) }}>{it.level}</span>
            <span style={{ color: FROST }}>{it.component}</span>
            <span style={{ color: ICE, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.message}</span>
          </div>
        ))}
        {items.length === 0 && <div style={{ padding: 18, color: SLATE }}>No audit entries yet.</div>}
      </div>
    </Panel>
  )
}

export default function KalshiSentinelDashboard() {
  const [tab, setTab] = useState("markets");
  const [status, setStatus] = useState(null);
  const [markets, setMarkets] = useState([]);
  const [cursor, setCursor] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);

  const statusOk = !!status && typeof status.exchange_active === "boolean";

  useEffect(() => {
    fetch(API + "/kalshi/exchange/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch((e) => setErr(String(e)));
  }, []);

  const loadMarkets = async ({ reset } = { reset: true }) => {
    setLoading(true);
    setErr(null);
    try {
      const url = new URL(window.location.origin + API + "/kalshi/markets");
      url.searchParams.set("limit", "50");
      if (!reset && cursor) url.searchParams.set("cursor", cursor);
      const res = await fetch(url.pathname + url.search);
      const json = await res.json();
      setCursor(json.cursor || null);
      setMarkets((prev) => (reset ? json.markets || [] : [...prev, ...(json.markets || [])]));
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadMarkets({ reset: true });
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return markets;
    return markets.filter((m) => (m.title || "").toLowerCase().includes(q) || (m.ticker || "").toLowerCase().includes(q));
  }, [markets, query]);

  return (
    <div style={{ background: BG, minHeight: "100vh", color: FROST, fontFamily: "'JetBrains Mono', monospace" }}>
      <Styles />
      <Scanlines />
      <Header />
      <StatusBar statusOk={statusOk} />

      {/* Top metrics */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 1, background: BORDER, borderBottom: `1px solid ${BORDER}` }}>
        {[{ label: "Markets (loaded)", value: markets.length, color: BLUE_BRIGHT },
          { label: "Trading Active", value: status?.trading_active ? "YES" : "NO", color: status?.trading_active ? TEAL : AMBER_COLD },
          { label: "Exchange Active", value: status?.exchange_active ? "YES" : "NO", color: status?.exchange_active ? TEAL : RED_COLD },
          { label: "Signals", value: "—", color: AMBER_COLD },
          { label: "PnL", value: "—", color: AMBER_COLD },
        ].map((m) => (
          <div key={m.label} style={{ background: BG_PANEL, padding: "14px 8px" }}>
            <Metric label={m.label} value={m.value} color={m.color} />
          </div>
        ))}
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: `1px solid ${BORDER}`, background: BG_PANEL }}>
        {["markets", "signals", "portfolio", "log", "paper", "trades", "audit"].map((t) => (
          <button key={t} className={`tab-btn ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t.toUpperCase()}
          </button>
        ))}
        <div style={{ marginLeft: "auto", padding: "10px 20px", fontSize: 12, color: SLATE, display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: statusOk ? TEAL : RED_COLD, animation: "pulse 2s infinite", fontSize: 12 }}>●</span>
          {statusOk ? "LIVE" : "OFFLINE"}
        </div>
      </div>

      <div style={{ padding: 14 }}>
        {err && (
          <Panel title="ERROR">
            <pre style={{ color: RED_COLD, margin: 0, whiteSpace: "pre-wrap" }}>{err}</pre>
          </Panel>
        )}

        {tab === "markets" && (
          <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 420px" : "1fr", gap: 14 }}>
            <Panel
              title="MARKETS"
              headerRight={
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="search title/ticker"
                    style={{
                      background: BG,
                      border: `1px solid ${BORDER_LIGHT}`,
                      borderRadius: 4,
                      padding: "6px 10px",
                      color: ICE,
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 12,
                      width: 220,
                      outline: "none",
                    }}
                  />
                  <button
                    onClick={() => loadMarkets({ reset: true })}
                    style={{
                      background: `linear-gradient(135deg, ${BLUE_MID}, ${BLUE_DIM})`,
                      border: "none",
                      borderRadius: 4,
                      padding: "6px 10px",
                      color: ICE,
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 12,
                      cursor: "pointer",
                    }}
                  >
                    REFRESH
                  </button>
                </div>
              }
            >
              <ColHeaders
                columns={[
                  { l: "TICKER", w: "170px" },
                  { l: "TITLE", w: "1fr" },
                  { l: "YES BID", w: "80px", a: "right" },
                  { l: "YES ASK", w: "80px", a: "right" },
                  { l: "CLOSE", w: "160px" },
                ]}
              />
              <div style={{ maxHeight: "65vh", overflowY: "auto" }}>
                {filtered.map((m, i) => (
                  <div
                    key={m.ticker || i}
                    className="row-hover"
                    onClick={() => setSelected(m)}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "170px 1fr 80px 80px 160px",
                      gap: 8,
                      alignItems: "center",
                      padding: "8px 10px",
                      fontSize: 12,
                      borderBottom: `1px solid ${GRID_LINE}`,
                      cursor: "pointer",
                      animation: `fadeInUp 0.25s ease-out ${Math.min(i * 0.01, 0.3)}s both`,
                    }}
                  >
                    <span style={{ color: FROST, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.ticker}</span>
                    <span style={{ color: ICE, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.title}</span>
                    <span style={{ color: TEAL, textAlign: "right" }}>{typeof m.yes_bid === "number" ? (m.yes_bid / 100).toFixed(2) : "—"}</span>
                    <span style={{ color: AMBER_COLD, textAlign: "right" }}>{typeof m.yes_ask === "number" ? (m.yes_ask / 100).toFixed(2) : "—"}</span>
                    <span style={{ color: SLATE, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.close_time || "—"}</span>
                  </div>
                ))}
                {filtered.length === 0 && <div style={{ padding: 18, color: SLATE }}>No markets loaded yet.</div>}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 10px 0" }}>
                <div style={{ color: SLATE, fontSize: 12 }}>{loading ? "Loading..." : cursor ? "More available" : "End"}</div>
                <button
                  disabled={!cursor || loading}
                  onClick={() => loadMarkets({ reset: false })}
                  style={{
                    background: !cursor || loading ? BORDER : `linear-gradient(135deg, ${BLUE_DARK}, ${BLUE_DIM})`,
                    border: "none",
                    borderRadius: 4,
                    padding: "6px 10px",
                    color: !cursor || loading ? SLATE : ICE,
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: 12,
                    cursor: !cursor || loading ? "not-allowed" : "pointer",
                  }}
                >
                  LOAD MORE
                </button>
              </div>
            </Panel>

            {selected && (
              <Panel title="MARKET DETAIL" headerRight={<button className="tab-btn" onClick={() => setSelected(null)}>CLOSE</button>}>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", color: ICE, fontSize: 12, lineHeight: 1.6, maxHeight: "70vh", overflowY: "auto" }}>
                  {JSON.stringify(selected, null, 2)}
                </pre>
              </Panel>
            )}
          </div>
        )}

        {tab === "paper" && (
          <PaperTab />
        )}

        {tab === "trades" && (
          <TradesTab />
        )}

        {tab === "audit" && (
          <AuditTab />
        )}

        {tab === "portfolio" && (
          <PortfolioTab />
        )}

        {tab !== "markets" && tab !== "paper" && tab !== "audit" && tab !== "trades" && tab !== "portfolio" && (
          <Panel title="NOT IMPLEMENTED YET">
            <div style={{ color: SLATE, fontSize: 12, lineHeight: 1.7 }}>
              This tab is a placeholder. Next we will implement:
              <div style={{ marginTop: 8, color: ICE }}>
                - Signals (probability + edge vs market)
                <br />- Portfolio (paper mode + later guarded execution)
              </div>
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}
