export function TankGauge({ level, minLevel, maxLevel }) {
  const danger = level > maxLevel || level < minLevel
  return (
    <div className="flex items-center gap-5 justify-center">
      <div className="relative w-[70px] h-[150px] border-[3px] border-dark2 rounded-md overflow-hidden bg-slate-100">
        <div
          className="absolute bottom-0 left-0 w-full transition-all duration-500"
          style={{ height: `${level}%`, background: danger ? '#DC2626' : '#0284C7' }}
        />
      </div>
      <div>
        <div className="text-3xl font-extrabold" style={{ color: danger ? '#DC2626' : '#0F172A' }}>
          {level.toFixed(1)}%
        </div>
        <div className="text-xs text-dark2">Overflow &gt;{maxLevel}% · Dry-run &lt;{minLevel}%</div>
      </div>
    </div>
  )
}

export function TachoGauge({ flow, maxFlow = 150 }) {
  const pct = Math.min(1, flow / maxFlow)
  const angle = -120 + pct * 240
  const r = 50, cx = 60, cy = 60
  const arcPoints = []
  for (let a = -120; a <= 120; a += 6) {
    const rad = (a * Math.PI) / 180
    arcPoints.push([cx + r * Math.sin(rad), cy - r * Math.cos(rad)])
  }
  const path = arcPoints.reduce(
    (acc, p, i) => acc + (i === 0 ? `M ${p[0]} ${p[1]} ` : `L ${p[0]} ${p[1]} `),
    ''
  )
  const needleRad = (angle * Math.PI) / 180
  const nx = cx + (r - 8) * Math.sin(needleRad)
  const ny = cy - (r - 8) * Math.cos(needleRad)

  return (
    <div className="flex flex-col items-center">
      <svg width="120" height="90" viewBox="0 0 120 75">
        <path d={path} fill="none" stroke="#e2e8f0" strokeWidth="8" strokeLinecap="round" />
        <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="#1E3A8A" strokeWidth="3" strokeLinecap="round" />
        <circle cx={cx} cy={cy} r="4" fill="#1E3A8A" />
      </svg>
      <div className="text-2xl font-extrabold text-accentDeep -mt-2">{flow.toFixed(1)} L/min</div>
    </div>
  )
}
