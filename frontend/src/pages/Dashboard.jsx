import { useEffect, useState } from 'react'
import { TankGauge, TachoGauge } from '../components/Gauges.jsx'
import TelemetryChart, { MarkSwatch } from '../components/TelemetryChart.jsx'
import { api } from '../api.js'
import { H24, formatDbTime, formatDuration, localDateToDb, localDay, msToDb, parseDbTime } from '../time.js'
import { useIsMobile } from '../hooks/useIsMobile.js'

export default function Dashboard({ nodeContext, nodes, liveByNode, thresholdsByNode, nodeStatus, onExpandedChange, onOpenNode, pinned, onUnpin }) {
  if (nodeContext === 'all') {
    return <AllNodesGrid nodes={nodes} liveByNode={liveByNode} nodeStatus={nodeStatus} onOpenNode={onOpenNode} />
  }
  return <NodeDashboard nodeId={Number(nodeContext)} nodes={nodes} live={liveByNode[nodeContext]}
    limits={thresholdsByNode[nodeContext]} status={nodeStatus?.[nodeContext]} onExpandedChange={onExpandedChange}
    pinned={pinned?.node_id === Number(nodeContext) ? pinned : null} onUnpin={onUnpin} />
}

// Inline dashboard charts are a quick-glance overview, not the analysis
// view -- capped to a fixed point count (not a time window) so they never
// look overcrowded regardless of how dense the data is. Click a chart to
// open the full expanded view with real timeframe controls (see
// ExpandedChartModal below).
const INLINE_RANGE = '1h'   // fixed underlying fetch window for the compact view; no longer user-facing
const MAX_INLINE_POINTS = 50
const TIMEFRAME_MS = { '1h': 3600 * 1000, '6h': 6 * 3600 * 1000, '24h': 24 * 3600 * 1000 }

function NodeDashboard({ nodeId, nodes, live, limits, status, onExpandedChange, pinned, onUnpin }) {
  // Which alert markers the charts show; remembered by this screen across restarts and power-offs
  const [showMarks, setShowMarks] = useState(() => {
    try {
      return { threshold: true, model: true, ...JSON.parse(localStorage.getItem('argus-chart-markers') || '{}') }
    } catch {
      return { threshold: true, model: true }
    }
  })
  useEffect(() => {
    try { localStorage.setItem('argus-chart-markers', JSON.stringify(showMarks)) } catch { /* private mode */ }
  }, [showMarks])
  const [history, setHistory] = useState([])
  const [now, setNow] = useState(new Date())
  const [expandedField, setExpandedField] = useState(null)   // null | 'ph' | 'turbidity' | 'temperature'
  const node = nodes.find((n) => n.node_id === nodeId)

  // Tell the app while the expanded chart is open (toasts shrink out of its way); also on leaving
  useEffect(() => {
    onExpandedChange?.(expandedField != null)
    return () => onExpandedChange?.(false)
  }, [expandedField, onExpandedChange])

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])

  // The last hour, or -- when the node has been quiet longer than that -- its last
  // MAX_INLINE_POINTS readings, so the charts open on the latest data either way
  // A pinned reading is treated as "now": the hour leading up to it, thinned to about MAX_INLINE_POINTS
  useEffect(() => {
    let cancelled = false
    if (pinned) {
      const t = parseDbTime(pinned.timestamp)
      api.telemetryHistoryRange(nodeId, msToDb(t - TIMEFRAME_MS[INLINE_RANGE]), msToDb(t), MAX_INLINE_POINTS)
        .then((rows) => {
          // Thinning may have dropped the pinned reading itself; it's the one the charts are about
          if (!rows.some((r) => r.id === pinned.id)) {
            rows = [...rows, pinned].sort((a, b) => parseDbTime(a.timestamp) - parseDbTime(b.timestamp))
          }
          if (!cancelled) setHistory(rows)
        }).catch(() => {})
      return () => { cancelled = true }
    }
    api.telemetryHistory(nodeId, INLINE_RANGE).then(async (rows) => {
      if (!rows.length) {
        rows = (await api.telemetryRows({ nodeId, pageSize: MAX_INLINE_POINTS })).rows.reverse()
      }
      if (!cancelled) setHistory(rows)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [nodeId, pinned?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Splice the live WebSocket tail onto the fetched history so charts move
  // in real time without waiting for the next timeframe re-fetch.
  useEffect(() => {
    if (live && !pinned) setHistory((prev) => (prev.some((r) => r.id === live.id) ? prev : [...prev, live].slice(-500)))
  }, [live]) // eslint-disable-line react-hooks/exhaustive-deps

  const latest = pinned || live || history[history.length - 1]
  const pinnedTime = pinned ? parseDbTime(pinned.timestamp) : null
  // Only the most recent N points are actually drawn inline -- `history`
  // itself stays larger so the volume total above still reflects the full
  // fetched window, not just what's visible on the small chart.
  // The hour leading up to the NEWEST reading (not up to page load): when the page opened during a
  // gap and fell back to older readings, those drop out as soon as fresh ones arrive, instead of
  // stretching the chart from yesterday to today
  const newest = history.length ? parseDbTime(history[history.length - 1].timestamp) : 0
  const inlineRows = pinned ? history
    : history.filter((r) => parseDbTime(r.timestamp) >= newest - 3600 * 1000).slice(-MAX_INLINE_POINTS)

  if (!latest || !limits) {
    return <div className="text-dark2 text-sm">Waiting for telemetry from node {nodeId}…</div>
  }

  return (
    <div>
      <Ribbon nodeLabel={node?.label || `Node ${nodeId}`} now={now} online={!!status?.is_online}
        at={pinned ? formatDbTime(pinned.timestamp) : null}
        offlineFor={status?.seconds_since_last_reading == null ? null
          : status.seconds_since_last_reading + (now - status.receivedAt) / 1000} />

      {pinned && (
        <div className="flex flex-wrap items-center justify-between gap-3 bg-sky-50 border border-accentSky text-dark rounded-lg px-4 py-2.5 mb-4 text-sm"
          role="status">
          <div>
            Showing a past reading from <b>{formatDbTime(pinned.timestamp)}</b>, not live data.
            Charts show the hour leading up to it.
          </div>
          <button type="button" onClick={onUnpin}
            className="px-3 py-1 border border-accentDeep text-accentDeep rounded-md bg-white">Back to live</button>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 md:gap-4 mb-5">
        <Card title="Fluid Level Tracker">
          <TankGauge level={latest.water_level} minLevel={limits.min_level} maxLevel={limits.max_level} />
        </Card>
        <Card title="Discharge Velocity">
          <TachoGauge flow={latest.flowrate} maxFlow={Math.max(limits.max_flow * 1.15, latest.flowrate * 1.05, 0.1)} />
        </Card>
        <Card title="Volume (from node)">
          {/* Exactly the node's own running total from its latest packet -- same number as
              the ESP32's OLED. It's NOT summed from history, so it moves with the node:
              it resets to 0 after 60 s with no flow. null = a row from before this column existed. */}
          <div className="text-4xl font-extrabold">
            {latest.volume_total == null ? '—' : `${Number(latest.volume_total).toFixed(2)} L`}
          </div>
          <div className="text-xs text-dark2 mt-1">Running total reported by the node · resets after 60 s with no flow</div>
        </Card>
      </div>

      <div className="bg-white border border-border rounded-lg p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Alert markers to show on the charts">
            <span className="text-xs uppercase tracking-wide text-dark2 mr-1">Show on charts</span>
            <MarkToggle kind="threshold" label="Threshold breach" on={showMarks.threshold}
              onClick={() => setShowMarks((m) => ({ ...m, threshold: !m.threshold }))} />
            <MarkToggle kind="model" label="Anomaly" on={showMarks.model}
              onClick={() => setShowMarks((m) => ({ ...m, model: !m.model }))} />
          </div>
          <div className="text-xs text-dark2">Tap a chart to enlarge it and pick a time range</div>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <ClickableChart onExpand={() => setExpandedField('ph')}>
            <TelemetryChart rows={inlineRows} field="ph" limits={limits} show={showMarks}
              label="pH" clamp={[0, 14]} />
          </ClickableChart>
          <ClickableChart onExpand={() => setExpandedField('turbidity')}>
            <TelemetryChart rows={inlineRows} field="turbidity" limits={limits} show={showMarks}
              label="Turbidity" unit=" NTU" decimals={1} clamp={[0, Infinity]} />
          </ClickableChart>
          <ClickableChart onExpand={() => setExpandedField('temperature')}>
            <TelemetryChart rows={inlineRows} field="temperature" limits={limits} show={showMarks}
              label="Temperature" unit="°C" />
          </ClickableChart>
        </div>
      </div>

      <MlReasoning row={latest} />

      {expandedField && (
        <ExpandedChartModal
          nodeId={nodeId} field={expandedField} limits={limits} live={live}
          showMarks={showMarks} onClose={() => setExpandedField(null)} asOf={pinnedTime}
        />
      )}
    </div>
  )
}

// Wraps a chart to make it clickable without interfering with Recharts'
// own mouse handling (hover tooltips, etc.) -- the click handler sits on
// a transparent overlay-free wrapper, and a visible hint only shows on
// hover so the inline view doesn't look like a button by default.
function ClickableChart({ onExpand, children }) {
  return (
    <button
      type="button"
      onClick={onExpand}
      className="text-left rounded-lg -m-1 p-1 transition-colors hover:bg-canvas2 focus:outline-none focus:ring-2 focus:ring-accentDeep group relative"
      title="Click to expand"
    >
      {children}
      <span className="absolute top-0 right-1 text-[10px] text-dark2 opacity-0 group-hover:opacity-100 transition-opacity">
        ⤢ Expand
      </span>
    </button>
  )
}

// Field metadata shared between the inline chart calls and the expanded
// modal, so the two stay in sync (same label/unit/decimals/clamp) without
// duplicating it in two places.
const FIELD_META = {
  ph: { label: 'pH', clamp: [0, 14] },
  turbidity: { label: 'Turbidity', unit: ' NTU', decimals: 1, clamp: [0, Infinity] },
  temperature: { label: 'Temperature', unit: '°C' },
}

const todayLocal = () => localDay()

// asOf (ms): a pinned past reading, treated as "now" -- 1H/6H/24H count back from it, the dates
// default to its day, and live readings aren't added
function ExpandedChartModal({ nodeId, field, limits, live, showMarks, onClose, asOf }) {
  const isMobile = useIsMobile()
  const [range, setRange] = useState('1h')          // '1h' | '6h' | '24h' | 'dates'
  const [fromDate, setFromDate] = useState(() => localDay(asOf ?? Date.now()))
  const [toDate, setToDate] = useState(() => localDay(asOf ?? Date.now()))
  const [history, setHistory] = useState([])
  const [loading, setLoading] = useState(true)
  const meta = FIELD_META[field]
  const datesInvalid = range === 'dates' && (!fromDate || !toDate || fromDate > toDate)

  useEffect(() => {
    if (datesInvalid) return undefined
    let cancelled = false
    setLoading(true)
    // Picked days in local time, whole days, as the database's UTC timestamps; nothing after asOf.
    // (Same-format UTC strings, so they compare as text.)
    const asOfDb = asOf != null ? msToDb(asOf) : null
    let request
    if (range === 'dates') {
      const end = localDateToDb(toDate, true)
      request = api.telemetryHistoryRange(nodeId, localDateToDb(fromDate), asOfDb && asOfDb < end ? asOfDb : end)
    } else if (asOfDb) {
      request = api.telemetryHistoryRange(nodeId, msToDb(asOf - TIMEFRAME_MS[range]), asOfDb)
    } else {
      request = api.telemetryHistory(nodeId, range)
    }
    request.then((rows) => {
      if (!cancelled) { setHistory(rows); setLoading(false) }
    }).catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [nodeId, range, fromDate, toDate, datesInvalid, asOf])

  // Keep the expanded view live too, same splicing pattern as the main dashboard -- except for a
  // date range that ends before today, which shouldn't grow, or a pinned past reading
  useEffect(() => {
    if (!live || asOf != null || (range === 'dates' && toDate < todayLocal())) return
    setHistory((prev) => (prev.some((r) => r.id === live.id) ? prev : [...prev, live].slice(-2000)))
  }, [live]) // eslint-disable-line react-hooks/exhaustive-deps

  // The ML models' current batch median for each sensor, refreshed with every new packet
  const [baselines, setBaselines] = useState({})
  useEffect(() => {
    let cancelled = false
    api.telemetryBaseline(nodeId).then((b) => { if (!cancelled) setBaselines(b) }).catch(() => {})
    return () => { cancelled = true }
  }, [nodeId, live])

  // Close on Escape, for keyboard users
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    // pb-14 keeps a strip free at the bottom for the minimized toasts, so they never cover the chart
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4 pb-14"
      onClick={onClose}
      role="dialog" aria-modal="true" aria-label={`${meta.label} expanded chart`}
    >
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-5xl max-h-full overflow-y-auto p-3 md:p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
          <h2 className="text-lg font-bold text-dark">{meta.label}</h2>
          <div className="flex flex-wrap items-center gap-2 md:gap-3">
            <div className="flex gap-1.5">
              {['1h', '6h', '24h', 'dates'].map((r) => (
                <button
                  key={r}
                  className={`px-3 py-1 rounded-full border text-xs ${
                    range === r ? 'bg-accentDeep text-white border-accentDeep' : 'bg-white text-dark2 border-border'
                  }`}
                  onClick={() => setRange(r)}
                >
                  {r === 'dates' ? 'DATES' : r.toUpperCase()}
                </button>
              ))}
            </div>
            {range === 'dates' && (
              <div className="flex items-center gap-1.5 text-xs text-dark2">
                <input type="date" aria-label="From date" value={fromDate} max={toDate || undefined}
                  onChange={(e) => setFromDate(e.target.value)}
                  className="p-1 border border-border rounded-md text-xs bg-white" />
                <span>to</span>
                <input type="date" aria-label="To date" value={toDate} min={fromDate || undefined}
                  onChange={(e) => setToDate(e.target.value)}
                  className="p-1 border border-border rounded-md text-xs bg-white" />
              </div>
            )}
            <button
              type="button" onClick={onClose} aria-label="Close"
              className="text-dark2 hover:text-dark text-xl leading-none px-1"
            >
              ×
            </button>
          </div>
        </div>
        {datesInvalid ? (
          <div className="h-[480px] flex items-center justify-center text-sm text-alert">Pick a From date on or before the To date.</div>
        ) : loading && history.length === 0 ? (
          <div className="h-[480px] flex items-center justify-center text-sm text-dark2">Loading…</div>
        ) : (
          <TelemetryChart key={`${range}|${fromDate}|${toDate}`}
            rows={history} field={field} limits={limits} show={showMarks} height={isMobile ? 300 : 480} zoomable
            label={meta.label} unit={meta.unit} decimals={meta.decimals} clamp={meta.clamp}
            baseline={asOf == null ? baselines[field] : undefined}
          />
        )}
      </div>
    </div>
  )
}

// Each card opens that node's own dashboard (same as picking it in the sidebar)
function AllNodesGrid({ nodes, liveByNode, nodeStatus, onOpenNode }) {
  const anyOnline = Object.values(nodeStatus || {}).some((s) => s.is_online)
  return (
    <div>
      <Ribbon nodeLabel="All Nodes Overview" now={new Date()} online={anyOnline} />
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
        {nodes.map((n) => {
          const latest = liveByNode[n.node_id]
          return (
            <button key={n.node_id} type="button" onClick={() => onOpenNode?.(String(n.node_id))}
              aria-label={`Open ${n.label}'s dashboard`}
              className="text-left bg-white border border-border rounded-lg p-4 cursor-pointer transition-shadow hover:shadow-md hover:border-accentSky focus:outline-none focus-visible:ring-2 focus-visible:ring-accentSky">
              <div className="flex justify-between items-center mb-2.5">
                <b>{n.label}</b>
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ background: !nodeStatus?.[n.node_id]?.is_online ? '#94a3b8' : (latest?.is_anomaly ? '#DC2626' : '#22c55e') }}
                  title={nodeStatus?.[n.node_id]?.is_online ? 'Online' : 'Offline'}
                />
              </div>
              {latest ? (
                <div className="grid grid-cols-2 gap-2 text-sm text-dark2">
                  <div>pH <b className="text-dark">{latest.ph}</b></div>
                  <div>Turbidity <b className="text-dark">{latest.turbidity} NTU</b></div>
                  <div>Temp <b className="text-dark">{latest.temperature}°C</b></div>
                  <div>Level <b className="text-dark">{latest.water_level}%</b></div>
                  <div>Flow <b className="text-dark">{latest.flowrate} L/min</b></div>
                  <div>Status <b className="text-dark">{latest.is_anomaly ? 'Alert' : 'Normal'}</b></div>
                </div>
              ) : (
                <div className="text-sm text-dark2">Waiting for telemetry…</div>
              )}
              <div className="text-xs text-accentSky mt-3">Open dashboard →</div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// at: a pinned past reading's date and time, shown in place of the clock
function Ribbon({ nodeLabel, now, online, offlineFor, at }) {
  return (
    <div className="flex flex-wrap justify-between items-center gap-x-4 gap-y-1 bg-white border border-border rounded-lg px-4 md:px-5 py-3 md:py-3.5 mb-4 md:mb-5">
      <div className="font-bold text-accentDeep text-base">{nodeLabel}</div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-dark2">
        <div>{at || now.toLocaleTimeString([], H24)}</div>
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${online ? 'bg-accentSky animate-pulse' : 'bg-alert'}`} />
          {online ? 'Receiving live data'
            : offlineFor != null ? `No data for ${formatDuration(offlineFor)}`
            : 'Waiting for the first reading'}
        </div>
      </div>
    </div>
  )
}

function MarkToggle({ kind, label, on, onClick }) {
  return (
    <button
      type="button" aria-pressed={on} onClick={onClick}
      title={on ? `Hide points flagged by ${label.toLowerCase()}` : `Show points flagged by ${label.toLowerCase()}`}
      className={`flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs transition-colors ${
        on ? 'bg-canvas2 border-dark2 text-dark' : 'bg-white border-border text-dark2 opacity-60'
      }`}
    >
      <MarkSwatch kind={kind} size={12} />
      {label}
      <span className={`ml-0.5 text-[10px] font-semibold ${on ? 'text-accentDeep' : 'text-dark2'}`}>{on ? 'ON' : 'OFF'}</span>
    </button>
  )
}

const REASONING_SENSORS = [
  { key: 'ph', label: 'pH', unit: '', decimals: 2 },
  { key: 'turbidity', label: 'Turbidity', unit: ' NTU', decimals: 1 },
  { key: 'temperature', label: 'Temperature', unit: ' °C', decimals: 2 },
]

const signed = (v, decimals) => (v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(decimals)}`)

// Empirical rule: the share of normal readings expected within 1 / 2 / 3 standard deviations of the mean
const EMPIRICAL_BANDS = [[1, '68 %'], [2, '95 %'], [3, '99.7 %']]

function EmpiricalRule({ d, unit, decimals }) {
  if (d.z == null || d.mean == null) return '—'
  const band = EMPIRICAL_BANDS.find(([k]) => Math.abs(d.z) <= k)
  const outlier = !band
  const agrees = outlier === Boolean(d.flagged)
  // one more decimal than the reading, so the small numbers in the working stay readable
  const n = (v) => Number(v).toFixed(decimals + 1)
  return (
    <div className="text-xs leading-relaxed">
      <div className="text-dark2">z = (value − mean) ÷ sd</div>
      <div>= ({Number(d.value).toFixed(decimals)} − {n(d.mean)}) ÷ {n(d.sd)}{unit}</div>
      <div className={`font-semibold ${outlier ? 'text-alert' : 'text-dark'}`}>= {signed(d.z, 2)} σ</div>
      <div className="mt-0.5">
        {outlier
          ? 'Beyond 3σ, outside the 99.7 % of normal readings: an outlier'
          : `Within ${band[0]}σ, where ${band[1]} of normal readings fall: not an outlier`}
      </div>
      {d.score != null && (
        <div className={agrees ? 'text-dark2' : 'text-alert'}>{agrees ? 'Agrees' : 'Disagrees'} with the model</div>
      )}
    </div>
  )
}

// The ML's working for one reading (telemetry_logs.ml_details, written by anomaly_engine.py): per
// sensor the batch median, dev, step, model score and z, and why it was or wasn't flagged. Rows from
// before the engine saved this have none, and the card stays hidden.
function MlReasoning({ row }) {
  let details = null
  try { details = JSON.parse(row?.ml_details || 'null') } catch { /* malformed: show nothing */ }
  if (!details) return null
  const cell = 'px-2.5 py-2 border-b border-border whitespace-nowrap'
  return (
    <div className="bg-white border border-border rounded-lg p-4 mt-5">
      <h3 className="text-xs uppercase tracking-wide text-dark2 mb-1">Why this reading is or isn't an anomaly</h3>
      <div className="text-xs text-dark2 mb-3">
        Reading at {formatDbTime(row.timestamp)}. The model scores how far the reading is from its batch median (dev)
        and how much it changed in the last 2 minutes (step); a score below 0 is an anomaly. For comparison, the
        empirical rule measures how many standard deviations (σ) the reading is from the batch mean: 68 / 95 / 99.7 %
        of normal readings fall within 1 / 2 / 3σ, so beyond 3σ counts as an outlier.
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-canvas2 text-dark2 text-[11px] uppercase">
              {['Sensor', 'Value', 'Batch median', 'Dev', 'Step (2 min)', 'Model score', 'Verdict', 'Empirical rule (3σ)'].map((h) => (
                <th key={h} className="text-left px-2.5 py-2 whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {REASONING_SENSORS.filter((s) => details[s.key]).map((s) => {
              const d = details[s.key]
              const judged = d.score != null
              return (
                <tr key={s.key} className="align-top">
                  <td className={`${cell} font-semibold`}>{s.label}</td>
                  <td className={cell}>{Number(d.value).toFixed(s.decimals)}{s.unit}</td>
                  <td className={cell}>{Number(d.median).toFixed(s.decimals)}{s.unit}</td>
                  <td className={cell}>{judged ? signed(d.dev, s.decimals) : '—'}</td>
                  <td className={cell}>{judged ? signed(d.step, s.decimals) : '—'}</td>
                  <td className={`${cell} ${judged && d.score < 0 ? 'text-alert font-semibold' : ''}`}>
                    {judged ? d.score.toFixed(3) : '—'}
                  </td>
                  <td className="px-2.5 py-2 border-b border-border min-w-[260px]">
                    <span className={`font-semibold ${d.flagged ? 'text-alert' : 'text-dark'}`}>
                      {d.flagged ? 'Anomaly' : judged ? 'Not an anomaly' : 'Not judged yet'}
                    </span>
                    <div className="text-xs text-dark2 mt-0.5 whitespace-normal">{d.why}</div>
                  </td>
                  <td className="px-2.5 py-2 border-b border-border min-w-[230px]">
                    <EmpiricalRule d={d} unit={s.unit} decimals={s.decimals} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Card({ title, children }) {
  return (
    <div className="bg-white border border-border rounded-lg p-4">
      <h3 className="text-xs uppercase tracking-wide text-dark2 mb-3">{title}</h3>
      {children}
    </div>
  )
}
