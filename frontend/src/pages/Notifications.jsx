import { useEffect, useRef, useState } from 'react'
import NotificationCard from '../components/NotificationCard.jsx'
import Pagination from '../components/Pagination.jsx'
import { MARK_COLORS } from '../components/TelemetryChart.jsx'
import { useNotifications } from '../hooks/useNotifications.js'
import DssGuide from '../components/DssGuide.jsx'
import { formatDbTime } from '../time.js'

const TYPE_OPTIONS = [
  { value: 'all', label: 'All alerts' },
  { value: 'threshold', label: 'Threshold breach' },
  { value: 'ml', label: 'Anomaly' },
]

// Values match the server's NOTIFICATION_PARAMS keys
const PARAM_OPTIONS = [
  { value: '', label: 'All parameters' },
  { value: 'flow', label: 'Flow Rate' },
  { value: 'level', label: 'Water Level' },
  { value: 'ph', label: 'pH' },
  { value: 'turbidity', label: 'Turbidity' },
  { value: 'temperature', label: 'Temperature' },
]

const SELECT = 'p-2 border border-border rounded-md text-sm bg-white'

export default function Notifications({ nodes, focus }) {
  const [nodeFilter, setNodeFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('all')
  const [guideOpen, setGuideOpen] = useState(false)   // the DSS Guide window
  const [paramFilter, setParamFilter] = useState('')
  const [highlightId, setHighlightId] = useState(null)
  const [seenFocus, setSeenFocus] = useState(null)
  const listTop = useRef(null)
  const scrolledTo = useRef(null)

  // A toast click: clear the filters (so the alert is guaranteed to be in the
  // list) during this render, so the very first request already asks for the
  // alert's page instead of fetching the filtered page first.
  if (focus && focus.nonce !== seenFocus) {
    setSeenFocus(focus.nonce)
    setNodeFilter('all')
    setTypeFilter('all')
    setParamFilter('')
    setHighlightId(focus.id)
    scrolledTo.current = null
  }

  const { rows, total, page, pages, pageSize, counts, loading, error, setPage, acknowledge, focusFound } = useNotifications({
    nodeId: nodeFilter === 'all' ? null : Number(nodeFilter),
    alertType: typeFilter,
    param: paramFilter || null,
  }, { focus })

  // Once the focused alert is on screen, scroll it into view (once per focus)
  useEffect(() => {
    if (highlightId == null || scrolledTo.current === highlightId) return
    if (!rows.some((r) => r.id === highlightId)) return
    scrolledTo.current = highlightId
    document.getElementById(`alert-${highlightId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [rows, highlightId])

  // Let the highlight fade after 4s. Kept apart from the effect above, whose
  // cleanup runs on every list refresh and would cancel this timer.
  useEffect(() => {
    if (highlightId == null) return undefined
    const t = setTimeout(() => setHighlightId(null), 4000)
    return () => clearTimeout(t)
  }, [highlightId])

  const nodeLabel = (id) => nodes.find((n) => n.node_id === id)?.label || `Node ${id}`
  const filtered = typeFilter !== 'all' || paramFilter !== ''

  const clearFilters = () => {
    setTypeFilter('all')
    setParamFilter('')
  }

  const goToPage = (p) => {
    setPage(p)
    listTop.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div>
      {/* Header counts follow the node + parameter filters; the list also follows the type filter */}
      <div className="flex flex-wrap justify-between items-center gap-3 bg-white border border-border rounded-lg px-5 py-3.5 mb-4">
        <div className="flex items-center gap-3">
          <div className="font-bold text-accentDeep text-base">Decision Support System</div>
          <button type="button" onClick={() => setGuideOpen(true)}
            className="px-3 py-1 border border-accentDeep text-accentDeep rounded-md text-xs hover:bg-accentDeep hover:text-white">
            Alert Guide
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-dark2">
          <div>Open alerts: <b className="text-dark">{counts?.active ?? '—'}</b></div>
          <div>
            Threshold breach: <b style={{ color: MARK_COLORS.threshold }}>{counts?.threshold ?? '—'}</b>
            {' '}· Anomaly: <b style={{ color: MARK_COLORS.model }}>{counts?.ml ?? '—'}</b>
          </div>
          <div>Latest: {counts?.latest_timestamp ? formatDbTime(counts.latest_timestamp, { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'}</div>
        </div>
      </div>

      <div ref={listTop} className="flex flex-wrap items-center gap-2.5 mb-4 scroll-mt-4">
        <select className={SELECT} aria-label="Node" value={nodeFilter} onChange={(e) => setNodeFilter(e.target.value)}>
          <option value="all">All nodes</option>
          {nodes.map((n) => <option key={n.node_id} value={n.node_id}>{n.label}</option>)}
        </select>
        <select className={SELECT} aria-label="Alert type" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          {TYPE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select className={SELECT} aria-label="Parameter" value={paramFilter} onChange={(e) => setParamFilter(e.target.value)}>
          {PARAM_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {filtered && (
          <button type="button" className="text-sm text-accentDeep underline"
            onClick={clearFilters}>
            Clear filters
          </button>
        )}
      </div>

      {error && <div className="mb-3 text-sm text-alert">Couldn't load alerts: {error}</div>}
      {focusFound === false && focus && seenFocus === focus.nonce && (
        <div className="mb-3 text-sm text-dark2">That alert is no longer active — it may already have been acknowledged.</div>
      )}

      {!loading && rows.length === 0 && (
        <div className="text-dark2 text-sm">
          {filtered ? 'No active alerts match these filters.' : 'No active alerts.'}
        </div>
      )}
      <div className={loading && rows.length ? 'opacity-60 transition-opacity' : undefined}>
        {rows.map((n) => (
          <NotificationCard key={n.id} n={n} nodeLabel={nodeLabel(n.node_id)} onAcknowledge={acknowledge}
            highlighted={n.id === highlightId} />
        ))}
      </div>

      <Pagination page={page} pages={pages} total={total} pageSize={pageSize} onChange={goToPage} />
      {guideOpen && <DssGuide onClose={() => setGuideOpen(false)} />}
    </div>
  )
}
