const BASE = '' // same-origin: server.py serves this SPA and the API together

async function request(method, path, body) {
  const res = await fetch(BASE + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `${method} ${path} failed (${res.status})`)
  }
  return res.json()
}

export const api = {
  telemetryLatest: (nodeId) => request('GET', `/api/telemetry/latest?node_id=${nodeId}`),
  telemetryHistory: (nodeId, timeframe) =>
    request('GET', `/api/telemetry/history?node_id=${nodeId}&timeframe=${timeframe}`),
  // A chosen date range (UTC database timestamps), thinned to about maxPoints readings
  telemetryHistoryRange: (nodeId, start, end, maxPoints = 2000) => request('GET',
    `/api/telemetry/history?node_id=${nodeId}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&max_points=${maxPoints}`),
  telemetryBaseline: (nodeId) => request('GET', `/api/telemetry/baseline?node_id=${nodeId}`),
  telemetryRows: ({ nodeId, start, end, flag = 'all', page = 1, pageSize = 50 }) => {
    const params = new URLSearchParams({ page, page_size: pageSize, flag })
    if (nodeId != null) params.set('node_id', nodeId)
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    return request('GET', `/api/telemetry/rows?${params.toString()}`)
  },
  notificationsList: ({ nodeId, alertType = 'all', param, page = 1, pageSize = 10, sinceId, focusId }) => {
    const params = new URLSearchParams({ alert_type: alertType, page, page_size: pageSize })
    if (nodeId != null) params.set('node_id', nodeId)
    if (param) params.set('param', param)
    if (sinceId != null) params.set('since_id', sinceId)
    if (focusId != null) params.set('focus_id', focusId)
    return request('GET', `/api/notifications?${params.toString()}`)
  },
  // The alerts one reading raised (by node + its UTC timestamp), acknowledged or not
  notificationsAt: (nodeId, timestamp) =>
    request('GET', `/api/notifications/at?node_id=${nodeId}&timestamp=${encodeURIComponent(timestamp)}`),
  notificationsSummary: () => request('GET', '/api/notifications/summary'),
  acknowledgeNotification: (id) => request('PUT', `/api/notifications/acknowledge/${id}`),
  generateReport: (payload) => request('POST', '/api/reports/generate', payload),
  exportCsv: (payload) => request('POST', '/api/telemetry/export-csv', payload),
  reportDownloadUrl: (filename) => `/api/reports/download/${encodeURIComponent(filename)}`,
  reportViewUrl: (filename) => `/api/reports/view/${encodeURIComponent(filename)}`,
  reportPreview: (filename) => request('GET', `/api/reports/preview/${encodeURIComponent(filename)}`),
  reportFiles: () => request('GET', '/api/reports/files'),
  dssGuide: () => request('GET', '/api/dss/guide'),
  deleteReports: (filenames) => request('POST', '/api/reports/delete', { filenames }),
  reportPages: (filename) => request('GET', `/api/reports/pages/${encodeURIComponent(filename)}`),
  reportPageUrl: (filename, page) => `/api/reports/page/${encodeURIComponent(filename)}/${page}`,
  hotspot: () => request('GET', '/api/hotspot'),
  thresholdsGet: () => request('GET', '/api/admin/thresholds'),
  thresholdsPut: (payload) => request('PUT', '/api/admin/thresholds', payload),
  mlConfigGet: () => request('GET', '/api/admin/ml-config'),
  mlConfigPut: (payload) => request('PUT', '/api/admin/ml-config', payload),
  nodesGet: () => request('GET', '/api/admin/nodes'),
  nodesPut: (payload) => request('PUT', '/api/admin/nodes', payload),
  diagnostics: () => request('GET', '/api/admin/diagnostics'),
  // This browser's clock, so a Pi without internet time can set its clock from a phone
  syncTime: () => request('POST', '/api/system/time', { client_ms: Date.now() }),
  timeStatus: () => request('GET', '/api/system/time'),
  // Safe shutdown: allowed only from the Pi's own screen
  shutdownAllowed: () => request('GET', '/api/system/shutdown'),
  shutdown: () => request('POST', '/api/system/shutdown'),
  nodeStatus: () => request('GET', '/api/nodes/status'),
}

const liveListeners = new Map()   // event type -> Set of callbacks

/**
 * Subscribes to one kind of server push on the shared live socket; returns an unsubscribe.
 * Types: 'telemetry' (data = new row), 'notifications' (alerts added or acknowledged),
 * 'node_status' (data = nodes that went online/offline), and 'open' -- fired on every
 * (re)connect, so listeners can re-fetch whatever they may have missed while it was down.
 */
export function onLive(type, callback) {
  if (!liveListeners.has(type)) liveListeners.set(type, new Set())
  liveListeners.get(type).add(callback)
  return () => liveListeners.get(type).delete(callback)
}

function emitLive(type, data) {
  liveListeners.get(type)?.forEach((callback) => callback(data))
}

/**
 * Opens /ws/telemetry/live once for the whole app and hands each push to its onLive()
 * listeners. The server sends data only when something changes, so nothing in the app
 * polls on a timer. Reconnects automatically with backoff if the socket drops (e.g.
 * server.py restarts) -- the kiosk display should never need a manual page reload to
 * recover a live feed.
 */
export function startLiveSocket() {
  let socket
  let stopped = false
  let backoffMs = 1000

  function open() {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${proto}://${window.location.host}/ws/telemetry/live`)
    socket.onopen = () => { backoffMs = 1000; emitLive('open') }
    socket.onmessage = (evt) => {
      try {
        const payload = JSON.parse(evt.data)
        emitLive(payload.type, payload.data)
      } catch { /* ignore malformed frames */ }
    }
    socket.onclose = () => {
      if (stopped) return
      setTimeout(open, backoffMs)
      backoffMs = Math.min(backoffMs * 2, 15000)
    }
    socket.onerror = () => socket.close()
  }

  open()
  return () => { stopped = true; socket && socket.close() }
}
