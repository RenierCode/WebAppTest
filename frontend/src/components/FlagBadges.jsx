import { MarkSwatch, MARK_COLORS } from './TelemetryChart.jsx'

const PARAM_LABELS = { ph: 'pH', turbidity: 'Turbidity', temperature: 'Temp', level: 'Level', flow: 'Flow' }

export function paramList(flags) {
  return flags ? flags.split(',').map((f) => PARAM_LABELS[f] || f).join(', ') : ''
}

export function Badge({ kind, children }) {
  const color = MARK_COLORS[kind]
  return (
    <span
      className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full border whitespace-nowrap"
      style={{ color, borderColor: color, background: '#FFFFFF' }}
    >
      <MarkSwatch kind={kind} size={11} />
      {children}
    </span>
  )
}

/** What flagged one telemetry_logs row: rules, the ML model, or both. */
export default function FlagBadges({ row }) {
  if (!row.is_anomaly) return <span className="text-dark2">—</span>
  if (row.threshold_flags == null && row.ml_flags == null) {
    // Written before the per-parameter columns existed
    return <span className="text-dark2" title="Recorded before the alert type was stored">Alert</span>
  }
  return (
    <div className="flex flex-wrap gap-1">
      {row.threshold_flags && <Badge kind="threshold">Threshold breach: {paramList(row.threshold_flags)}</Badge>}
      {row.ml_flags && <Badge kind="model">Anomaly: {paramList(row.ml_flags)}</Badge>}
    </div>
  )
}
