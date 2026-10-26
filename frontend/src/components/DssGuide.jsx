import { useEffect, useState } from 'react'
import { api } from '../api.js'
import DssDetails, { SEVERITY_STYLE } from './DssDetails.jsx'

/**
 * Every condition the system can alert on -- threshold limits and ML diagnoses -- with its most
 * common reasons, impact, recommended actions and references (served from dss_rules.py, the same
 * rules the alerts use). Opened from the Alerts page.
 */
const SENSOR_BUTTONS = [['all', 'All'], ['ph', 'pH'], ['temperature', 'Temperature'], ['turbidity', 'Turbidity'],
  ['level', 'Water level'], ['flow', 'Flow']]

export default function DssGuide({ onClose }) {
  const [entries, setEntries] = useState(null)
  const [error, setError] = useState('')
  const [sensor, setSensor] = useState('all')       // touch screen: sensor buttons, no typing

  useEffect(() => {
    api.dssGuide().then(setEntries).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    const onKey = (e) => {
      // A reference's QR pop-up sits on top and closes itself first
      if (e.key === 'Escape' && !document.querySelector('[aria-label^="Reference:"]')) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const shown = (entries || []).filter((e) => sensor === 'all' || e.sensors.includes(sensor))
  const groups = [...new Set(shown.map((e) => e.group))]

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={onClose}
      role="dialog" aria-modal="true" aria-label="Alert Guide">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl h-full max-h-[92vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3 p-5 border-b border-border">
          <div>
            <div className="text-lg font-bold text-accentDeep">Alert Guide</div>
            <div className="text-xs text-dark2">
              Every condition the system alerts on, with its most common reasons, possible impact and
              recommended actions. Tap a [number] for a QR code of the source.
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
            className="text-dark2 hover:text-dark text-xl leading-none px-1">×</button>
        </div>
        <div className="px-5 pt-3 flex flex-wrap gap-2" role="group" aria-label="Show conditions for">
          {SENSOR_BUTTONS.map(([key, label]) => (
            <button key={key} type="button" onClick={() => setSensor(key)} aria-pressed={sensor === key}
              className={`min-h-[44px] px-4 rounded-full border text-sm font-medium ${
                sensor === key ? 'bg-accentDeep text-white border-accentDeep' : 'bg-white text-dark2 border-border'}`}>
              {label}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-5 pt-3">
          {error ? (
            <div className="text-sm text-alert">{error}</div>
          ) : !entries ? (
            <div className="text-sm text-dark2">Loading…</div>
          ) : shown.length === 0 ? (
            <div className="text-sm text-dark2">No conditions for this sensor.</div>
          ) : groups.map((group) => (
            <div key={group} className="mb-5">
              <div className="text-xs font-semibold uppercase tracking-wide text-dark2 mb-2">{group}</div>
              {shown.filter((e) => e.group === group).map((e) => {
                const severity = e.severity && SEVERITY_STYLE[e.severity]
                return (
                  <div key={e.name} className="border border-border rounded-lg p-3.5 mb-2.5"
                    style={{ borderLeft: `5px solid ${severity ? severity.color : '#94A3B8'}` }}>
                    <div className="flex flex-wrap items-center gap-2">
                      <b className="text-sm text-dark">{e.name}</b>
                      {severity && (
                        <span className="text-[11px] font-bold px-2 py-0.5 rounded-full border"
                          style={{ color: severity.color, borderColor: severity.color, background: severity.background }}>
                          {e.severity}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-dark2 mt-1">Where: {e.stage}</div>
                    <div className="text-xs text-dark2">Triggered when: {e.trigger}</div>
                    <DssDetails dss={e} />
                  </div>
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
