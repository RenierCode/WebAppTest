import { useEffect, useRef, useState, useCallback } from 'react'
import Sidebar, { NAV_ITEMS, SYSTEM_ITEMS } from './components/Sidebar.jsx'
import { Menu } from 'lucide-react'
import logo from './assets/logo.svg'
import { useIsMobile } from './hooks/useIsMobile.js'
import Dashboard from './pages/Dashboard.jsx'
import Notifications from './pages/Notifications.jsx'
import HistoricalLogs from './pages/HistoricalLogs.jsx'
import Reports from './pages/Reports.jsx'
//import Admin from './pages/Admin.jsx'
import About from './pages/About.jsx'
import Preferences from './pages/Preferences.jsx'
import { api, onLive, startLiveSocket } from './api.js'
import { alertToast, useNewAlertToasts } from './hooks/useNewAlertToasts.js'
import { formatDbTime } from './time.js'
import { useToast, useToastCompact, useToastDismiss } from './components/Toasts.jsx'

export default function App() {
  const [collapsed, setCollapsed] = useState(true)      // sidebar starts closed (icons only); ☰ opens it
  const isMobile = useIsMobile()                       // phone: top bar + slide-in menu
  const [menuOpen, setMenuOpen] = useState(false)
  const [view, setView] = useState('dashboard')
  const [nodes, setNodes] = useState([])
  const [nodeContext, setNodeContext] = useState('1')
  const [thresholdsByNode, setThresholdsByNode] = useState({})
  const [liveByNode, setLiveByNode] = useState({})       // node_id -> latest telemetry row
  const [diagnostics, setDiagnostics] = useState(null)
  const [nodeStatus, setNodeStatus] = useState({})       // node_id -> {is_online, seconds_since_last_reading, last_seen}
  // Alert to jump to on the Notifications page; nonce makes a repeat click on the same alert count again
  const [focusAlert, setFocusAlert] = useState(null)
  // The Pi's clock was just set from a phone (see /api/system/time): shown until dismissed
  const [clockNotice, setClockNotice] = useState(null)

  useEffect(() => {
    try {
      const preferences = JSON.parse(localStorage.getItem('argus-preferences') || '{}')
      document.documentElement.dataset.textSize = preferences.textSize || 'normal'
      document.documentElement.classList.toggle('high-contrast', Boolean(preferences.highContrast))
      document.documentElement.classList.toggle('reduced-motion', Boolean(preferences.reducedMotion))
    } catch {
      document.documentElement.dataset.textSize = 'normal'
    }
  }, [])

  const refreshNodes = useCallback(() => {
    api.nodesGet().then((rows) => {
      setNodes(rows)
      if (rows.length && !rows.find((n) => String(n.node_id) === nodeContext) && nodeContext !== 'all') {
        setNodeContext(String(rows[0].node_id))
      }
    }).catch(() => {})
  }, [nodeContext])

  // With a single node the sidebar hides the node selector, so never leave the dashboard on the
  // All Nodes overview with no way back
  useEffect(() => {
    if (nodes.length === 1 && nodeContext === 'all') setNodeContext(String(nodes[0].node_id))
  }, [nodes, nodeContext])

  const refreshThresholds = useCallback(() => {
    api.thresholdsGet().then((rows) => {
      setThresholdsByNode(Object.fromEntries(rows.map((r) => [String(r.node_id), r])))
    }).catch(() => {})
  }, [])

  const openAlert = useCallback((id) => {
    setView('notifications')
    setFocusAlert({ id, nonce: Date.now() })
  }, [])
  useNewAlertToasts(nodes, openAlert)
  // Consumed once you leave the page, so coming back doesn't jump to it again
  useEffect(() => { if (view !== 'notifications') setFocusAlert(null) }, [view])

  // A past reading picked in Historical Logs, shown on the dashboard instead of the live values
  // until dismissed, the page is left, or another node is picked
  const [pinnedReading, setPinnedReading] = useState(null)
  const openReading = useCallback((row) => {
    setNodeContext(String(row.node_id))
    setPinnedReading(row)
    setView('dashboard')
  }, [])
  useEffect(() => {
    if (view !== 'dashboard' || (pinnedReading && nodeContext !== String(pinnedReading.node_id))) setPinnedReading(null)
  }, [view, nodeContext]) // eslint-disable-line react-hooks/exhaustive-deps

  // The alerts the pinned reading raised, as toasts that stay up until it's unpinned
  const toast = useToast()
  const dismissToast = useToastDismiss()
  useEffect(() => {
    if (!pinnedReading) return undefined
    let cancelled = false
    let keys = []
    api.notificationsAt(pinnedReading.node_id, pinnedReading.timestamp).then((rows) => {
      if (cancelled) return
      rows.forEach((n) => {
        const content = { ...alertToast(n, nodes), key: `pinned-alert-${n.id}` }
        keys.push(content.key)
        toast({ ...content, sticky: true, onClick: () => openAlert(n.id) })
      })
    }).catch(() => {})
    return () => { cancelled = true; keys.forEach(dismissToast); keys = [] }
  }, [pinnedReading?.id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Full toasts only on the main dashboard; on other pages and over the expanded chart
  // they shrink to one line in the bottom-right corner, which that dialog keeps free
  const [chartExpanded, setChartExpanded] = useState(false)
  const setToastCompact = useToastCompact()
  useEffect(() => {
    setToastCompact(view !== 'dashboard' || chartExpanded)
  }, [view, chartExpanded, setToastCompact])

  useEffect(() => { refreshNodes() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { refreshThresholds() }, [refreshThresholds])
  useEffect(() => {
    api.diagnostics().then(setDiagnostics).catch(() => setDiagnostics(null))
  }, [view])

  // Real connectivity, not assumption: /api/nodes/status derives online/offline from how
  // recently each node actually transmitted. Fetched on every (re)connect; after that the
  // server pushes a node only when it crosses the offline limit, and a node's own packets
  // mark it online right away. receivedAt lets the "no packets in Ns" counter keep counting
  // on this browser's clock, which may not match the Pi's.
  const withReceivedAt = (row) => ({ ...row, receivedAt: Date.now() })
  const refreshNodeStatus = useCallback(() => {
    api.nodeStatus().then((rows) => {
      setNodeStatus(Object.fromEntries(rows.map((r) => [String(r.node_id), withReceivedAt(r)])))
    }).catch(() => {})
  }, [])

  // The newest stored reading per node, so the dashboard opens on the latest data instead of
  // waiting for the next packet. Re-run on reconnect to pick up anything missed meanwhile.
  const refreshLatest = useCallback((nodeRows) => {
    nodeRows.forEach((n) => {
      api.telemetryLatest(n.node_id).then((row) => {
        setLiveByNode((prev) => {
          const current = prev[String(n.node_id)]
          return current && current.id >= row.id ? prev : { ...prev, [String(n.node_id)]: row }
        })
      }).catch(() => {})   // 404: this node has no readings yet
    })
  }, [])
  useEffect(() => { refreshLatest(nodes) }, [nodes, refreshLatest])

  // One shared WebSocket for the whole app -- Dashboard, All-Nodes grid, alerts and the
  // sidebar heartbeat all read off the same live feed instead of each polling on a timer.
  // The socket handlers below are set up once, so they reach the current state through refs
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  const refreshNodesRef = useRef(refreshNodes)
  refreshNodesRef.current = refreshNodes
  useEffect(() => {
    const offs = [
      onLive('open', () => {
        // Every (re)connect: send this browser's clock; the server only uses it when the Pi has
        // no internet time and is over a minute off. A notice of a recent correction is shown too.
        api.syncTime().catch(() => {})
        api.timeStatus().then((s) => {
          const sync = s.last_phone_sync
          if (sync && Date.now() - Date.parse(sync.at.replace(' ', 'T') + 'Z') < 10 * 60 * 1000) setClockNotice(sync)
        }).catch(() => {})
        refreshNodesRef.current()
        refreshThresholds()
        refreshNodeStatus()
        refreshLatest(nodesRef.current)
      }),
      onLive('telemetry', (row) => {
        const key = String(row.node_id)
        // A late reading the node stored while out of contact (LOG packet) is saved at its older
        // measurement time; it belongs in the history, not on the gauges as the current value
        setLiveByNode((prev) => (prev[key] && prev[key].timestamp > row.timestamp ? prev : { ...prev, [key]: row }))
        setNodeStatus((prev) => ({
          ...prev,
          [key]: withReceivedAt({ ...prev[key], node_id: row.node_id,
            last_seen: prev[key]?.last_seen > row.timestamp ? prev[key].last_seen : row.timestamp,
            seconds_since_last_reading: 0, is_online: true }),
        }))
        // A node the engine just auto-registered: load its name and limits
        if (!nodesRef.current.some((n) => n.node_id === row.node_id)) {
          refreshNodesRef.current()
          refreshThresholds()
        }
      }),
      onLive('clock', setClockNotice),
      onLive('node_status', (rows) => {
        setNodeStatus((prev) => ({
          ...prev,
          ...Object.fromEntries(rows.map((r) => [String(r.node_id), withReceivedAt({ ...prev[String(r.node_id)], ...r })])),
        }))
      }),
    ]
    const close = startLiveSocket()
    return () => { offs.forEach((off) => off()); close() }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        collapsed={collapsed} setCollapsed={setCollapsed}
        view={view} setView={setView}
        nodes={nodes} nodeContext={nodeContext} setNodeContext={setNodeContext}
        diagnostics={diagnostics} nodeStatus={nodeStatus}
        isMobile={isMobile} mobileOpen={menuOpen} onMobileClose={() => setMenuOpen(false)}
      />
      <div className="flex-1 min-w-0 flex flex-col">
      {isMobile && (
        <div className="flex items-center gap-3 bg-dark text-white px-3 py-2.5 flex-shrink-0">
          <button type="button" onClick={() => setMenuOpen(true)} aria-label="Open menu" className="p-1.5 -ml-1">
            <Menu size={22} />
          </button>
          <img src={logo} alt="" className="w-6 h-6 object-contain" />
          <span className="font-extrabold tracking-wider text-sm">ARGUS</span>
          <span className="text-slate-300 text-sm truncate">
            · {[...NAV_ITEMS, ...SYSTEM_ITEMS].find((i) => i.key === view)?.label}
          </span>
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-3 md:p-6">
        {clockNotice && (
          <div className="flex items-start justify-between gap-3 bg-sky-50 border border-accentSky text-dark rounded-lg px-4 py-2.5 mb-4 text-sm"
            role="status">
            <div>
              <b>Pi clock corrected from a phone</b> at {formatDbTime(clockNotice.at)}: it was{' '}
              {clockNotice.description}. Readings from now on have the right time; readings saved
              before this keep the old timestamps.
            </div>
            <button type="button" onClick={() => setClockNotice(null)} aria-label="Dismiss"
              className="text-dark2 hover:text-dark text-lg leading-none px-1">×</button>
          </div>
        )}
        {view === 'dashboard' && (
          <Dashboard nodeContext={nodeContext} nodes={nodes} liveByNode={liveByNode} onOpenNode={setNodeContext}
            thresholdsByNode={thresholdsByNode} nodeStatus={nodeStatus} onExpandedChange={setChartExpanded}
            pinned={pinnedReading} onUnpin={() => setPinnedReading(null)} />
        )}
        {view === 'notifications' && <Notifications nodes={nodes} focus={focusAlert} />}
        {view === 'history' && <HistoricalLogs nodes={nodes} liveByNode={liveByNode} onOpenReading={openReading} />}
        {view === 'reports' && <Reports nodes={nodes} />}
        {/* {view === 'admin' && <Admin nodes={nodes} refreshNodes={refreshNodes} />} */}
        {view === 'about' && <About />}
        {view === 'preferences' && <Preferences />}
      </div>
      </div>
    </div>
  )
}
