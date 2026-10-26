import {
  ResponsiveContainer, ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts'
import { useEffect, useRef, useState } from 'react'
import { H24, parseDbTime } from '../time.js'

export const MARK_COLORS = { threshold: '#D03B3B', model: '#4A3AA7' }
const LINE_COLOR = '#0284C7'

// Which system_thresholds columns bound each charted field. floor is the
// fixed lower bound for a field with no min column (turbidity: 0 NTU).
const LIMIT_KEYS = {
  ph: { min: 'min_ph', max: 'max_ph' },
  turbidity: { max: 'max_turbidity', floor: 0 },
  temperature: { min: 'min_temp', max: 'max_temp' },
}

/** The node's acceptable range for a field, e.g. { lo: 7, hi: 8.5 }; either end may be null. */
function rangeFor(field, limits) {
  const keys = LIMIT_KEYS[field]
  return {
    lo: (keys.min && limits?.[keys.min] != null) ? limits[keys.min] : (keys.floor ?? null),
    hi: (keys.max && limits?.[keys.max] != null) ? limits[keys.max] : null,
  }
}

function hasFlag(list, field) {
  return !!list && list.split(',').includes(field)
}

/**
 * Per-field flags for one telemetry row. The threshold side is checked
 * against the node's current limits, so the dots always agree with the
 * dashed limit lines, even for readings stored before the limits changed (the
 * stored threshold_flags reflect the limits at ingest time). The model side
 * comes from ml_flags, since the model's verdict can't be recomputed here.
 */
function flagsFor(row, range, field) {
  const v = row[field]
  const outside = v != null && ((range.lo != null && v < range.lo) || (range.hi != null && v > range.hi))
  return { threshold: outside, model: hasFlag(row.ml_flags, field) }
}

function formatLimit(v) {
  return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(2)))
}

function formatTime(t, withDate) {
  return new Date(t).toLocaleString([], withDate
    ? { ...H24, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { ...H24, hour: '2-digit', minute: '2-digit' })
}

// Drawn first so a threshold dot can sit inside it when both layers fired.
function ModelMark({ cx, cy, size = 1 }) {
  if (cx == null || cy == null) return null
  const r = 7 * size
  return (
    <path d={`M${cx},${cy - r}L${cx + r},${cy}L${cx},${cy + r}L${cx - r},${cy}Z`}
      fill="#FFFFFF" fillOpacity={0.6} stroke={MARK_COLORS.model} strokeWidth={size < 1 ? 1.5 : 2} />
  )
}

function ThresholdMark({ cx, cy, size = 1 }) {
  if (cx == null || cy == null) return null
  return <circle cx={cx} cy={cy} r={4.5 * size} fill={MARK_COLORS.threshold} stroke="#FFFFFF" strokeWidth={size < 1 ? 1 : 1.5} />
}

export function MarkSwatch({ kind, size = 14 }) {
  const c = size / 2
  return (
    <svg width={size} height={size} aria-hidden="true" className="shrink-0">
      {kind === 'model'
        ? <ModelMark cx={c} cy={c} />
        : <ThresholdMark cx={c} cy={c} />}
    </svg>
  )
}

// Not named `label`: Recharts injects its own `label` prop (the hovered X
// value, i.e. the raw epoch-ms timestamp) into custom tooltip content.
function ChartTooltip({ active, payload, unit, fieldLabel, decimals, rangeText }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="bg-white border border-border rounded-md shadow-sm px-3 py-2 text-xs text-dark2">
      <div className="mb-1">{new Date(p.t).toLocaleString([], H24)}</div>
      <div className="text-sm text-dark">
        {fieldLabel} <b>{p.value.toFixed(decimals)}{unit}</b>
      </div>
      {p.threshold && (
        <div className="flex items-center gap-1.5 mt-1"><MarkSwatch kind="threshold" size={12} />Threshold breach (normal: {rangeText}{unit})</div>
      )}
      {p.model && (
        <div className="flex items-center gap-1.5 mt-1"><MarkSwatch kind="model" size={12} />Anomaly (unusual change)</div>
      )}
    </div>
  )
}

// The ML models compare each reading with this median (their "dev" feature); they only start
// judging a sensor once it has 30 readings, so say so while it's still filling up.
function BaselineText({ baseline, decimals, unit }) {
  if (baseline.median == null) return null
  return (
    <span className="text-xs text-dark2"
      title="Anomaly checks compare each new reading with this normal level, learned from the current batch of water">
      · normal level <b className="text-dark">{baseline.median.toFixed(decimals)}{unit}</b>
      {baseline.ready ? '' : ` · anomaly check starting (${Math.min(baseline.readings, 30)} of 30 readings)`}
    </span>
  )
}

/**
 * rows: telemetry_logs rows (already filtered to the selected timeframe)
 * field: 'ph' | 'turbidity' | 'temperature'
 * limits: this node's system_thresholds row (drawn as dashed limit lines)
 * show: { threshold: bool, model: bool } -- which flag markers to draw
 * clamp: optional [min, max] the Y axis never goes past (e.g. pH 0-14)
 * baseline: optional { median, readings, ready } from /api/telemetry/baseline -- the ML models'
 *   batch median for this sensor, shown next to the current value (the expanded view passes it)
 * zoomable: pinch (or mouse wheel) to zoom the time range, drag to pan, double-tap to reset (expanded view)
 */
export default function TelemetryChart({ rows, field, limits, show, label, unit = '', decimals = 2, clamp, height = 240, baseline, zoomable = false }) {
  const range = rangeFor(field, limits)
  const rangeText = range.hi == null ? `≥ ${formatLimit(range.lo)}`
    : range.lo == null ? `≤ ${formatLimit(range.hi)}`
    : `${formatLimit(range.lo)}–${formatLimit(range.hi)}`
  const allData = rows.map((r) => {
    const f = flagsFor(r, range, field)
    return {
      t: parseDbTime(r.timestamp), value: r[field], threshold: f.threshold, model: f.model,
      // Marker series: the value where flagged, null elsewhere (no dot drawn)
      thresholdValue: f.threshold ? r[field] : null,
      modelValue: f.model ? r[field] : null,
    }
  })
  const fullT0 = allData.length ? allData[0].t : 0
  const fullT1 = allData.length ? allData[allData.length - 1].t : 0

  // Zoom: the visible time window, or null for everything. Only the expanded chart allows it.
  const [view, setView] = useState(null)
  const plotRef = useRef(null)
  const zoom = useZoomGestures(plotRef, zoomable && allData.length > 1, fullT0, fullT1, view, setView)
  const visible = view
    ? allData.filter((d, i) => (d.t >= view[0] && d.t <= view[1])
        // one point either side, so the line runs to the edges
        || (allData[i + 1] && allData[i + 1].t >= view[0] && d.t < view[0])
        || (allData[i - 1] && allData[i - 1].t <= view[1] && d.t > view[1]))
    : allData
  const data = visible
  const thresholdCount = data.filter((d) => d.threshold).length
  const modelCount = data.filter((d) => d.model).length
  const latest = allData.length ? allData[allData.length - 1] : null
  // A few hundred full-size markers in a ~450px plot merge into a solid band
  // that hides the line, so shrink them as the window fills up.
  const markSize = data.length > 300 ? 0.45 : data.length > 120 ? 0.7 : 1

  // Dashed lines only for configured limits, not for a fixed floor (turbidity 0)
  const keys = LIMIT_KEYS[field]
  const lines = [
    keys.min && range.lo != null && { y: range.lo, text: `min ${formatLimit(range.lo)}`, position: 'insideBottomRight' },
    range.hi != null && { y: range.hi, text: `max ${formatLimit(range.hi)}`, position: 'insideTopRight' },
  ].filter(Boolean)

  // Fit the data and both limit lines, with a little headroom.
  const values = data.filter((d) => !view || (d.t >= view[0] && d.t <= view[1])).map((d) => d.value)
    .concat(view ? [] : [range.lo, range.hi].filter((v) => v != null))
  let lo = values.length ? Math.min(...values) : 0
  let hi = values.length ? Math.max(...values) : 1
  const pad = Math.max((hi - lo) * 0.08, 0.1)
  lo -= pad
  hi += pad
  if (clamp) {
    lo = Math.max(lo, clamp[0])
    hi = Math.min(hi, clamp[1])
  }
  const t0 = view ? view[0] : fullT0
  const t1 = view ? view[1] : fullT1
  const withDate = t1 - t0 > 20 * 3600 * 1000
  const xTicks = t1 > t0 ? [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(t0 + f * (t1 - t0))) : [t0]

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1 mb-2">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 min-w-0">
          <span className="text-sm font-semibold text-dark">{label}{unit && <span className="font-normal text-dark2"> ({unit.trim()})</span>}</span>
          {latest && Number.isFinite(latest.value) && (
            <span className="flex items-baseline gap-1.5" title="Latest reading">
              <span className={`text-xl font-extrabold ${latest.threshold ? 'text-alert' : 'text-dark'}`}>
                {latest.value.toFixed(decimals)}
              </span>
              <span className={`text-xs ${latest.threshold ? 'text-alert' : 'text-dark2'}`}>
                {latest.threshold ? 'now · threshold breach' : 'now'}
              </span>
              {latest.model && (
                <span className="flex items-center gap-1 text-xs text-dark2"><MarkSwatch kind="model" size={11} />Anomaly</span>
              )}
            </span>
          )}
          {baseline && <BaselineText baseline={baseline} decimals={decimals} unit={unit} />}
        </div>
        <div className="flex items-center gap-3 text-xs text-dark2">
          {(range.lo != null || range.hi != null) && (
            <span className="flex items-center gap-1" title="Acceptable range for this node (dashed lines)">
              <span className="inline-block w-3 border-t border-dashed border-slate-400" />
              {rangeText}
            </span>
          )}
          {show.threshold && (
            <span className="flex items-center gap-1"><MarkSwatch kind="threshold" size={11} />{thresholdCount}</span>
          )}
          {show.model && (
            <span className="flex items-center gap-1"><MarkSwatch kind="model" size={11} />{modelCount}</span>
          )}
        </div>
      </div>
      {zoomable && allData.length > 1 && (
        <div className="flex items-center justify-between gap-2 text-xs text-dark2 mb-1.5">
          <span>{view
            ? `Zoomed in: ${formatTime(t0, withDate)} – ${formatTime(t1, withDate)}`
            : 'Pinch to zoom in · drag to move · double-tap to reset'}</span>
          {view && (
            <button type="button" onClick={() => setView(null)}
              className="min-h-[36px] px-3 rounded-md border border-accentDeep text-accentDeep">Reset zoom</button>
          )}
        </div>
      )}
      <div ref={plotRef} style={{ height, touchAction: zoomable ? 'none' : undefined }} {...zoom}>
        {allData.length === 0 ? (
          <div className="h-full flex items-center justify-center text-xs text-dark2 border border-dashed border-border rounded-md">
            No readings in this timeframe
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
              <CartesianGrid stroke="#F1F5F9" vertical={false} />
              <XAxis
                dataKey="t" type="number" domain={[t0, t1]} allowDataOverflow ticks={xTicks} interval="preserveStartEnd"
                tickFormatter={(t) => formatTime(t, withDate)} tick={{ fontSize: 11, fill: '#64748B' }}
                stroke="#CBD5E1" minTickGap={24}
              />
              <YAxis
                domain={[lo, hi]} allowDataOverflow tick={{ fontSize: 11, fill: '#64748B' }} stroke="#CBD5E1"
                tickFormatter={(v) => Number(v.toFixed(field === 'turbidity' ? 0 : 1))} width={48}
              />
              {lines.map((l) => (
                <ReferenceLine
                  key={l.text} y={l.y} stroke="#94A3B8" strokeDasharray="4 4" ifOverflow="extendDomain"
                  label={{ value: l.text, position: l.position, fontSize: 10, fill: '#64748B' }}
                />
              ))}
              <Tooltip
                content={<ChartTooltip unit={unit} fieldLabel={label} decimals={decimals} rangeText={rangeText} />}
                cursor={{ stroke: '#94A3B8', strokeWidth: 1 }}
              />
              {/* Marker layers: invisible lines that only draw a dot where flagged.
                  Always mounted and toggled with `hide`, so switching them never
                  rebuilds the chart or touches the data line or axes. */}
              <Line dataKey="modelValue" stroke="none" dot={<ModelMark size={markSize} />} activeDot={false}
                legendType="none" tooltipType="none" isAnimationActive={false} hide={!show.model} />
              <Line dataKey="thresholdValue" stroke="none" dot={<ThresholdMark size={markSize} />} activeDot={false}
                legendType="none" tooltipType="none" isAnimationActive={false} hide={!show.threshold} />
              {/* Data line last so it stays visible on top of dense markers */}
              <Line
                type="monotone" dataKey="value" stroke={LINE_COLOR} strokeWidth={2} dot={false}
                activeDot={{ r: 4, fill: LINE_COLOR, stroke: '#FFFFFF', strokeWidth: 2 }}
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}

const MIN_SPAN_MS = 60 * 1000          // never zoom in past one minute (four readings)

/**
 * Pinch-to-zoom on the time axis for a touch screen, plus mouse-wheel zoom, one-finger drag to pan
 * while zoomed, and double-tap to reset. view is [from, to] in ms or null (whole range).
 */
function useZoomGestures(ref, enabled, fullT0, fullT1, view, setView) {
  const gesture = useRef(null)
  const lastTap = useRef(0)
  const state = useRef({ fullT0, fullT1, view })
  state.current = { fullT0, fullT1, view }

  // Where along the plot (0..1) a screen x falls; the y axis takes ~48 px on the left
  const fraction = (clientX) => {
    const box = ref.current.getBoundingClientRect()
    const left = box.left + 40, width = Math.max(box.width - 52, 1)
    return Math.min(1, Math.max(0, (clientX - left) / width))
  }
  const apply = (from, span) => {
    const { fullT0: a, fullT1: b } = state.current
    span = Math.min(Math.max(span, MIN_SPAN_MS), b - a)
    from = Math.min(Math.max(from, a), b - span)
    setView(span >= b - a ? null : [from, from + span])
  }
  const current = () => state.current.view || [state.current.fullT0, state.current.fullT1]

  // Mouse wheel: zoom around the pointer (a native listener, so the page doesn't scroll instead)
  useEffect(() => {
    const el = ref.current
    if (!enabled || !el) return undefined
    const onWheel = (e) => {
      e.preventDefault()
      const [v0, v1] = current()
      const f = fraction(e.clientX)
      const span = (v1 - v0) * (e.deltaY > 0 ? 1.25 : 0.8)
      apply(v0 + f * (v1 - v0) - f * span, span)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [enabled]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!enabled) return {}
  const distance = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY)

  return {
    onTouchStart: (e) => {
      const [v0, v1] = current()
      if (e.touches.length === 2) {
        const mid = (e.touches[0].clientX + e.touches[1].clientX) / 2
        gesture.current = { kind: 'pinch', d0: distance(e.touches), v0, v1, f: fraction(mid) }
      } else if (e.touches.length === 1) {
        const now = Date.now()
        if (now - lastTap.current < 300) { setView(null); lastTap.current = 0; gesture.current = null; return }
        lastTap.current = now
        gesture.current = { kind: 'pan', x0: e.touches[0].clientX, v0, v1 }
      }
    },
    onTouchMove: (e) => {
      const g = gesture.current
      if (!g) return
      if (g.kind === 'pinch' && e.touches.length === 2) {
        const span = (g.v1 - g.v0) * (g.d0 / Math.max(distance(e.touches), 1))
        const anchor = g.v0 + g.f * (g.v1 - g.v0)          // the time under the fingers stays put
        apply(anchor - g.f * span, span)
      } else if (g.kind === 'pan' && e.touches.length === 1 && state.current.view) {
        const width = Math.max(ref.current.getBoundingClientRect().width - 52, 1)
        const shift = ((e.touches[0].clientX - g.x0) / width) * (g.v1 - g.v0)
        apply(g.v0 - shift, g.v1 - g.v0)
      }
    },
    onTouchEnd: (e) => { if (e.touches.length === 0) gesture.current = null },
  }
}
