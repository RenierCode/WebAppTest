import { createElement as h, useEffect, useRef } from 'react'
import { api, onLive } from '../api.js'
import { useToast } from '../components/Toasts.jsx'
import { Badge } from '../components/FlagBadges.jsx'
import { formatDbTime } from '../time.js'
import { toastTitle } from '../alertTitle.js'

// Only the newest few are worth a toast; the stack shows at most 3 anyway
const MAX_PER_CHECK = 3

/**
 * App-wide: whenever the server pushes that alerts changed (or the live socket reconnects),
 * fetches active alerts newer than the last one seen and raises one toast per alert, showing
 * its node, timestamp and what flagged it. Clicking a toast calls onOpen(alertId). The first
 * check only records a baseline, so loading the page doesn't announce existing alerts.
 */
export function useNewAlertToasts(nodes, onOpen) {
  const toast = useToast()
  const lastSeenId = useRef(null)
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes
  const onOpenRef = useRef(onOpen)
  onOpenRef.current = onOpen

  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const res = await api.notificationsList({ pageSize: MAX_PER_CHECK, sinceId: lastSeenId.current ?? undefined })
        if (cancelled) return
        if (lastSeenId.current == null) {
          lastSeenId.current = res.rows.reduce((max, r) => Math.max(max, r.id), 0)
          return
        }
        // Oldest first, so the newest ends up on top of the stack
        const fresh = res.rows.filter((r) => r.id > lastSeenId.current).sort((a, b) => a.id - b.id)
        const skipped = Math.max(0, (res.new_count ?? fresh.length) - fresh.length)
        fresh.forEach((n, i) => {
          toast({
            ...alertToast(n, nodesRef.current, i === 0 ? skipped : 0),
            onClick: () => onOpenRef.current?.(n.id),
          })
        })
        if (fresh.length) lastSeenId.current = Math.max(lastSeenId.current, ...fresh.map((r) => r.id))
      } catch {
        // Offline or server restarting; the reconnect ('open') checks again
      }
    }
    check()
    const offs = [onLive('notifications', check), onLive('open', check)]
    return () => { cancelled = true; offs.forEach((off) => off()) }
  }, [toast])
}

/** A toast's content for one alert row: its node, timestamp and what flagged it */
export function alertToast(n, nodes, skipped = 0) {
  const label = nodes.find((x) => x.node_id === n.node_id)?.label || `Node ${n.node_id}`
  return {
    key: `alert-${n.id}`,
    tone: n.is_threshold ? 'threshold' : 'model',
    title: toastTitle(n),
    message: alertDetails(n, label, skipped),
    compactText: `${label} · ${toastTitle(n)}`,
  }
}

function alertDetails(n, nodeLabel, skipped) {
  const row = (label, value) => h('div', { className: 'flex gap-1.5' },
    h('span', { className: 'w-[74px] shrink-0 text-dark2' }, label), value)
  return h('div', { className: 'mt-1 space-y-0.5' },
    row('Node', h('b', { className: 'text-dark font-semibold' }, nodeLabel)),
    row('Timestamp', formatDbTime(n.timestamp)),
    // From /api/notifications/at: raised earlier in an event this reading is still part of
    n.ongoing ? row('Status', h('span', { className: 'text-dark' }, 'Ongoing: still flagged at this reading')) : null,
    row('Type', h('span', { className: 'flex flex-wrap gap-1' },
      n.is_threshold ? h(Badge, { kind: 'threshold' }, 'Threshold breach') : null,
      n.is_anomaly ? h(Badge, { kind: 'model' }, 'Anomaly') : null)),
    // ML alerts are titled by their sensors; the DSS diagnosis ("Possible plant-wide upset: ...") goes here
    n.is_anomaly && n.dss ? row('Diagnosis', h('span', { className: 'text-dark' }, n.title)) : null,
    skipped > 0 ? h('div', { className: 'text-dark2 pt-0.5' }, `+${skipped} earlier alert${skipped > 1 ? 's' : ''} not shown`) : null,
  )
}
