import { useEffect } from 'react'
import { QRCodeSVG } from 'qrcode.react'

/**
 * Pop-up for one reference: a QR code of its link, so someone at the kiosk (which may have no
 * internet on the hotspot) can open the source on their phone. ref: { title, publisher, url, kind }.
 */
export default function ReferenceQr({ reference, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
      onClick={onClose}
      role="dialog" aria-modal="true" aria-label={`Reference: ${reference.title}`}
    >
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 flex flex-col items-center"
        onClick={(e) => e.stopPropagation()}>
        <div className="self-stretch flex justify-between items-start gap-3 mb-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-dark2">
            {reference.kind === 'incident' ? 'Similar situation' : 'Reference'}
            {reference.scope === 'national' ? ' · National (Philippines)' : reference.scope ? ' · International' : ''}
          </span>
          <button type="button" onClick={onClose} aria-label="Close"
            className="text-dark2 hover:text-dark text-xl leading-none px-1">×</button>
        </div>
        <div className="self-stretch font-semibold text-dark text-sm">{reference.title}</div>
        <div className="self-stretch text-xs text-dark2 mb-4">{reference.publisher}</div>
        <QRCodeSVG value={reference.url} size={220} fgColor="#0F172A" />
        <div className="font-mono text-xs text-dark2 mt-3.5 bg-canvas2 px-3 py-2 rounded-md break-all text-center">
          {reference.url}
        </div>
        <div className="text-xs text-dark2 mt-3 text-center max-w-[320px]">
          Scan with your phone camera to open it. It needs mobile data or internet: the
          WastewaterHub hotspot doesn't connect to the internet.
        </div>
      </div>
    </div>
  )
}
