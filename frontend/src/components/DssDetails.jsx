import { useState } from 'react'
import ReferenceQr from './ReferenceQr.jsx'

export const SEVERITY_STYLE = {
  CRITICAL: { color: '#DC2626', background: '#FEF2F2' },
  WARNING: { color: '#B45309', background: '#FFFBEB' },
}

/** A notification's dss column (JSON from dss_rules.py), or null for older/threshold alerts. */
export function parseDss(n) {
  if (!n.dss) return null
  try { return JSON.parse(n.dss) } catch { return null }
}

/**
 * The DSS diagnosis inside an alert card: hedged "most common reasons", possible impact and
 * recommended actions, each citing its references, plus similar real incidents. Every citation
 * opens a QR code of the source so it can be read on a phone.
 */
export default function DssDetails({ dss }) {
  const [open, setOpen] = useState(false)
  const [shown, setShown] = useState(null)       // the reference whose QR pop-up is open

  // Number references in order of first citation, national (Philippine) ones first; alerts saved
  // before references had a scope count as international
  const cited = []
  const cite = (id) => { if (!cited.includes(id)) cited.push(id) }
  for (const section of [dss.causes, dss.impact, dss.actions]) section.forEach((item) => item.refs.forEach(cite))
  dss.incidents.forEach(cite)
  const isNational = (id) => dss.references[id]?.scope === 'national'
  const order = [...cited.filter(isNational), ...cited.filter((id) => !isNational(id))]
  const number = (id) => order.indexOf(id) + 1

  const Cite = ({ id }) => (
    <button type="button" onClick={() => setShown(dss.references[id])}
      title={`${dss.references[id].title} (tap for a QR code)`}
      className="ml-1 text-[11px] font-semibold text-accentSky hover:underline align-super">[{number(id)}]</button>
  )
  const List = ({ heading, items }) => (
    <div className="mt-2.5">
      <div className="text-xs font-semibold text-dark uppercase tracking-wide mb-1">{heading}</div>
      <ul className="list-disc pl-5 space-y-1 text-sm text-dark leading-relaxed">
        {items.map((item, i) => (
          <li key={i}>{item.text}{item.refs.map((id) => <Cite key={id} id={id} />)}</li>
        ))}
      </ul>
    </div>
  )

  return (
    <div className="mt-2">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="text-xs font-semibold text-accentDeep hover:underline">
        {open ? '▾ Hide reasons and recommended actions' : '▸ Show most common reasons and recommended actions'}
      </button>
      {open && (
        <div className="mt-2 border border-border rounded-md p-3 bg-canvas2">
          <div className="text-xs text-dark2 bg-white border border-border rounded px-2.5 py-1.5">
            This is an automated suggestion from the sensor pattern, not a confirmed diagnosis. The list
            below shows the most common reasons for this pattern; check on site before acting.
            Tap a [number] to get a QR code of the source.
          </div>
          <List heading="Most common reasons" items={dss.causes} />
          <List heading="Possible impact" items={dss.impact} />
          <List heading="Recommended actions" items={dss.actions} />
          {dss.incidents.length > 0 && (
            <div className="mt-2.5">
              <div className="text-xs font-semibold text-dark uppercase tracking-wide mb-1">Similar situations</div>
              <ul className="pl-0 space-y-1 text-sm">
                {dss.incidents.map((id) => (
                  <li key={id}>
                    <button type="button" onClick={() => setShown(dss.references[id])}
                      className="text-left text-accentSky hover:underline">
                      [{number(id)}] {dss.references[id].title}
                    </button>
                    <span className="text-xs text-dark2"> · {dss.references[id].publisher}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {[['National references (Philippines)', order.filter(isNational)],
            ['International references', order.filter((id) => !isNational(id))]].map(([heading, ids]) => ids.length > 0 && (
            <div key={heading} className="mt-3 pt-2 border-t border-border">
              <div className="text-xs font-semibold text-dark uppercase tracking-wide mb-1">{heading}</div>
              <ol className="space-y-0.5 text-xs">
                {ids.map((id) => (
                  <li key={id}>
                    <button type="button" onClick={() => setShown(dss.references[id])}
                      className="text-left text-accentSky hover:underline">
                      [{number(id)}] {dss.references[id].title}
                    </button>
                    <span className="text-dark2"> · {dss.references[id].publisher}</span>
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
      )}
      {shown && <ReferenceQr reference={shown} onClose={() => setShown(null)} />}
    </div>
  )
}
