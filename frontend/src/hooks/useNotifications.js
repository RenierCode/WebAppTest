import { useCallback, useEffect, useRef, useState } from 'react'
import { api, onLive } from '../api.js'

const EMPTY = { total: 0, page: 1, pages: 1, rows: [], counts: null }

/**
 * One server-side page of active alerts, reloaded whenever the server pushes that alerts
 * changed, so new alerts and other operators' acknowledgements show up without a reload.
 * filters: { nodeId, alertType, param } -- changing any of them returns to page 1.
 * focus: { id, nonce } -- asks the server for the page holding that alert,
 * once per nonce; focusFound then says whether it was found.
 */
export function useNotifications(filters, { pageSize = 10, focus = null } = {}) {
  const { nodeId, alertType, param } = filters
  // The page is stored with the filters it belongs to, so a filter change
  // reads as page 1 on the very same render (no extra fetch of the old page).
  const filterKey = `${nodeId}|${alertType}|${param}`
  const [pageState, setPageState] = useState({ filterKey, page: 1 })
  const page = pageState.filterKey === filterKey ? pageState.page : 1
  const setPage = useCallback((p) => setPageState({ filterKey, page: p }), [filterKey])
  const [data, setData] = useState(EMPTY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [focusFound, setFocusFound] = useState(null)
  const focusDone = useRef(null) // nonce of the focus request already answered
  // Only the newest request may update state, so a slow reload can't
  // overwrite the page the user just switched to.
  const latestRequest = useRef(0)

  const load = useCallback(async () => {
    const id = ++latestRequest.current
    const focusing = focus && focus.nonce !== focusDone.current ? focus : null
    try {
      const res = await api.notificationsList({ nodeId, alertType, param, page, pageSize, focusId: focusing?.id })
      if (id !== latestRequest.current) return
      setData(res)
      setError('')
      if (focusing) {
        focusDone.current = focusing.nonce
        setFocusFound(res.focus_found)
      }
      // The server clamps a page past the end (e.g. after acknowledging the last alert on it)
      if (res.page !== page) setPage(res.page)
    } catch (e) {
      if (id === latestRequest.current) setError(e.message)
    } finally {
      if (id === latestRequest.current) setLoading(false)
    }
  }, [nodeId, alertType, param, page, pageSize, setPage, focus])

  useEffect(() => {
    setLoading(true)
    load()
    const offs = [onLive('notifications', load), onLive('open', load)]
    return () => offs.forEach((off) => off())
  }, [load])

  // Optimistic update while the PUT is in flight; the reload then drops the
  // row and pulls the next alert up into this page.
  const acknowledge = useCallback(async (id) => {
    setData((d) => ({ ...d, rows: d.rows.map((n) => (n.id === id ? { ...n, is_acknowledged: 1 } : n)) }))
    try {
      await api.acknowledgeNotification(id)
      return true
    } catch {
      return false // the reload below resyncs the row
    } finally {
      load()
    }
  }, [load])

  return { ...data, page, setPage, pageSize, loading, error, acknowledge, focusFound }
}
