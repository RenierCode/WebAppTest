// SQLite CURRENT_TIMESTAMP is UTC without a zone suffix ("2026-10-02 04:55:12");
// new Date() on that string would read it as local time.
export function parseDbTime(ts) {
  if (typeof ts !== 'string' || /[zZ]|[+-]\d\d:?\d\d$/.test(ts)) return new Date(ts).getTime()
  return new Date(ts.replace(' ', 'T') + 'Z').getTime()
}

// Times everywhere in the app use the 24-hour clock ("14:05", not "2:05 pm"), like the PDF and CSV reports
export const H24 = { hourCycle: 'h23' }

export function formatDbTime(ts, options) {
  if (!ts) return '—'
  return new Date(parseDbTime(ts)).toLocaleString([], { ...H24, ...options })
}

// A local calendar date from <input type="date"> ("2026-10-02") -> the UTC
// "YYYY-MM-DD HH:MM:SS" string the database compares against.
// endOfDay picks 23:59:59 local instead of 00:00:00.
export function localDateToDb(date, endOfDay = false) {
  if (!date) return undefined
  return msToDb(new Date(`${date}T${endOfDay ? '23:59:59' : '00:00:00'}`).getTime())
}

// A local minute from <input type="datetime-local"> ("2026-10-02T14:30") -> the same UTC
// database string. endOfMinute picks :59 instead of :00.
export function localMinuteToDb(value, endOfMinute = false) {
  if (!value) return undefined
  return msToDb(new Date(`${value.slice(0, 16)}:${endOfMinute ? '59' : '00'}`).getTime())
}

// Epoch ms -> UTC "YYYY-MM-DD HH:MM:SS"
export function msToDb(ms) {
  return new Date(ms).toISOString().slice(0, 19).replace('T', ' ')
}

// The local calendar day of a time, as <input type="date"> wants it ("2026-10-02")
export function localDay(ms = Date.now()) {
  const d = new Date(ms)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// How long ago, for people: "45 s", "12 min", "5 h", "10 days"
export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds))
  if (s < 90) return `${s} s`
  if (s < 90 * 60) return `${Math.round(s / 60)} min`
  if (s < 48 * 3600) return `${Math.round(s / 3600)} h`
  return `${Math.round(s / 86400)} days`
}
