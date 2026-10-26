import { useEffect, useState } from 'react'
import { Power } from 'lucide-react'
import { api, onLive } from '../api.js'

const CONFIRM_SECONDS = 5

/**
 * Shut the Pi down safely from the dashboard. Shown only where the server allows it (the Pi's own
 * screen; see /api/system/shutdown). Confirmation unlocks after a 5-second countdown, then every
 * open dashboard shows that the Pi is shutting down.
 */
export default function ShutdownButton({ collapsed }) {
  const [allowed, setAllowed] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [down, setDown] = useState(false)

  useEffect(() => {
    api.shutdownAllowed().then((r) => setAllowed(r.allowed)).catch(() => setAllowed(false))
    return onLive('shutdown', () => setDown(true))
  }, [])

  return (
    <>
      {allowed && (
        <button type="button" onClick={() => setConfirming(true)} aria-label="Shut down the Raspberry Pi"
          data-tooltip={collapsed ? 'Shut down' : undefined}
          className="relative flex items-center gap-3 w-full px-3 py-2.5 rounded-lg text-sm text-red-300 hover:bg-red-900/40">
          <Power size={18} className="flex-shrink-0" />
          {!collapsed && <span>Shut down</span>}
        </button>
      )}
      {confirming && <ConfirmShutdown onCancel={() => setConfirming(false)} onDone={() => { setConfirming(false); setDown(true) }} />}
      {down && (
        <div className="fixed inset-0 z-[80] bg-dark flex flex-col items-center justify-center text-center text-white p-6">
          <Power size={48} className="text-red-400 mb-4" />
          <div className="text-2xl font-bold mb-2">The Raspberry Pi is shutting down</div>
          <div className="text-slate-300 max-w-md">
            Monitoring has stopped. Wait until the Pi's green activity light has stopped flashing
            (about 20–30 seconds) before unplugging the power.
          </div>
        </div>
      )}
    </>
  )
}

function ConfirmShutdown({ onCancel, onDone }) {
  const [left, setLeft] = useState(CONFIRM_SECONDS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (left <= 0) return undefined
    const t = setTimeout(() => setLeft((n) => n - 1), 1000)
    return () => clearTimeout(t)
  }, [left])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, busy])

  const confirm = async () => {
    setBusy(true)
    setError('')
    try {
      await api.shutdown()
      onDone()
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[70] p-4" onClick={busy ? undefined : onCancel}
      role="alertdialog" aria-modal="true" aria-label="Confirm shutting down">
      <div className="bg-white text-dark rounded-lg shadow-xl w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="font-bold text-alert mb-1">Shut down the Raspberry Pi?</div>
        <div className="text-sm text-dark2 mb-4">
          Monitoring stops: no readings are received, stored or checked for alerts, and the hotspot goes
          off. Saved data is kept. To start again, unplug the power and plug it back in.
        </div>
        {error && <div className="text-sm text-alert mb-3">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="px-4 py-1.5 border border-border rounded-md text-sm" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="px-4 py-1.5 bg-alert text-white rounded-md text-sm disabled:opacity-40 min-w-[130px]"
            disabled={left > 0 || busy} onClick={confirm}>
            {busy ? 'Shutting down…' : left > 0 ? `Shut down (${left})` : 'Shut down'}
          </button>
        </div>
      </div>
    </div>
  )
}
