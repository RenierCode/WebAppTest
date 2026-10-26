import { MARK_COLORS } from './TelemetryChart.jsx'
import { Badge } from './FlagBadges.jsx'
import { formatDbTime } from '../time.js'
import { alertTitle } from '../alertTitle.js'
import DssDetails, { parseDss, SEVERITY_STYLE } from './DssDetails.jsx'

export default function NotificationCard({ n, nodeLabel, onAcknowledge, highlighted = false }) {
  // Independent flags -- a packet can trip both, and both badges show together
  const isThreshold = !!n.is_threshold
  const isAnomaly = !!n.is_anomaly
  const dss = parseDss(n)                 // DSS diagnosis of the ML flags (anomaly_engine.py + dss_rules.py)
  const severity = dss && SEVERITY_STYLE[dss.severity]
  const accentColor = severity ? severity.color : isThreshold ? MARK_COLORS.threshold : MARK_COLORS.model

  return (
    <div
      id={`alert-${n.id}`}
      className={`bg-white border border-border rounded-lg p-4 mb-3 scroll-mt-4 transition-shadow duration-700 ${
        highlighted ? 'ring-4 ring-accentSky/60 shadow-lg' : 'ring-0'}`}
      style={{ borderLeft: `5px solid ${accentColor}` }}
      aria-current={highlighted ? 'true' : undefined}
    >
      <div className="flex flex-wrap justify-between items-center gap-2 mb-1.5">
        <div className="flex flex-wrap items-center gap-2">
          {isThreshold && <Badge kind="threshold">THRESHOLD BREACH</Badge>}
          {isAnomaly && <Badge kind="model">ANOMALY</Badge>}
          {severity && (
            <span className="text-[11px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap"
              style={{ color: severity.color, borderColor: severity.color, background: severity.background }}>
              {dss.severity}
            </span>
          )}
          <b>{nodeLabel}</b> <span className="text-dark2">(node {n.node_id})</span>
        </div>
        <div className="text-xs text-dark2">{formatDbTime(n.timestamp)}</div>
      </div>
      <div className="font-semibold text-sm mb-1.5">{alertTitle(n)}</div>
      {dss && <div className="text-xs text-dark2 -mt-1 mb-1.5">Where: {dss.stage}</div>}
      <div className="font-mono text-xs bg-canvas2 px-2.5 py-1.5 rounded-md inline-block mb-2">
        pH: {n.ph_snapshot} | Turb: {n.turbidity_snapshot} NTU | Temp: {n.temp_snapshot}°C
      </div>
      <div className="text-sm text-dark leading-relaxed space-y-1">
        {messageLines(n.message).map((line, i) => (
          <div key={i}>{line.label && <b className="font-semibold">{line.label}: </b>}{line.text}</div>
        ))}
      </div>
      {dss && <DssDetails dss={dss} />}
      <button
        className={`mt-2.5 px-3.5 py-1.5 text-xs rounded-md border ${
          n.is_acknowledged
            ? 'bg-green-50 border-green-500 text-green-700 cursor-default'
            : 'bg-white border-accentDeep text-accentDeep cursor-pointer'
        }`}
        disabled={n.is_acknowledged}
        onClick={() => onAcknowledge(n.id)}
      >
        {n.is_acknowledged ? '✓ Acknowledged' : 'Acknowledge Alert'}
      </button>
    </div>
  )
}

// The engine joins every condition of one reading into a single message:
// "[Threshold - Elevated Turbidity] Turbidity 524.7 NTU exceeds ... [Threshold - Low Temperature ...] ..."
// Shown one condition per line, its tag as a bold label. A late LOG reading's closing
// "Delivered late: ..." note gets its own line too. Text without tags comes back as one line.
function messageLines(message) {
  return message
    .split(/\s+(?=\[(?:Threshold|ML Model) - )|\s+(?=Delivered late:)/)
    .map((part) => {
      const tag = part.match(/^\[(?:Threshold|ML Model) - ([^\]]+)\]\s*/)
      return tag ? { label: tag[1], text: part.slice(tag[0].length) } : { label: null, text: part }
    })
}
