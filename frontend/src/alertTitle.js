const PARAM_NAMES = { ph: 'pH', turbidity: 'Turbidity', temperature: 'Temperature', level: 'Water Level', flow: 'Flow' }

/**
 * An alert's title, naming the sensors when one reading crossed several thresholds. Alerts saved
 * before the engine did that read "Multiple Anomaly Conditions Detected (3)"; the sensors come
 * from their params ("level,ph,turbidity").
 */
export function sensorNames(params) {
  return (params || '').split(',').filter(Boolean).map((p) => PARAM_NAMES[p] || p)
}

/**
 * The pop-up toast's title. An ML alert with a DSS diagnosis is titled by its sensors
 * ("Anomaly: pH, Temperature, Turbidity"); the diagnosis goes in the toast's description.
 */
export function toastTitle(n) {
  const names = sensorNames(n.params)
  if (n.is_anomaly && n.dss && names.length) return `Anomaly: ${names.join(', ')}`
  return alertTitle(n)
}

export function alertTitle(n) {
  const title = n.title || ''
  // Several limits crossed at once: "Threshold breach: pH, Turbidity" (older alerts read "Multiple
  // Anomaly Conditions Detected (3)" or "Multiple Anomaly Conditions: ...")
  if (/^Multiple Anomaly Conditions/.test(title) && n.params) {
    const names = n.params.split(',').filter(Boolean).map((p) => PARAM_NAMES[p] || p)
    if (names.length) return `Threshold breach: ${names.join(', ')}`
  }
  return title
}
