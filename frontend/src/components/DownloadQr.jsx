import { useEffect, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { api } from '../api.js'

/**
 * The two-scan phone handoff for a file the server wrote to REPORTS_DIR: a QR code holds either
 * Wi-Fi details or a link, so the phone scans twice -- join the hotspot, then open the download
 * link (it only resolves on that network). Used by the PDF report and the CSV export.
 */
export default function DownloadQr({ downloadUrl, filename, title = 'Download the file' }) {
  const [hotspot, setHotspot] = useState(null)   // { ssid, qr }: the network the phone must join first

  useEffect(() => {
    api.hotspot().then(setHotspot).catch(() => setHotspot(null))
  }, [])

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-5">
      <QrStep step={1} title="Join the hotspot">
        {hotspot?.qr ? (
          <>
            <QRCodeSVG value={hotspot.qr} size={180} fgColor="#0F172A" />
            <Caption>Scan with the phone camera and tap <b>Join</b> for <b>{hotspot.ssid}</b>.
              If the phone warns there is no internet, choose <b>stay connected</b>.</Caption>
          </>
        ) : (
          <Caption>
            Connect the phone to the Wi-Fi network <b>{hotspot?.ssid || 'WastewaterHub'}</b> in its Wi-Fi settings,
            and stay connected even if it says there is no internet.
            <span className="block mt-1.5 text-[11px]">
              A join QR code appears here once WASTEWATER_HOTSPOT_PASSWORD is set on the server.
            </span>
          </Caption>
        )}
      </QrStep>
      <QrStep step={2} title={title}>
        <QRCodeSVG value={downloadUrl} size={180} fgColor="#0F172A" />
        <div className="font-mono text-xs text-dark2 mt-3.5 bg-canvas2 px-3 py-2 rounded-md break-all text-center">
          {downloadUrl}
        </div>
        <Caption>Once on the hotspot, scan with the camera to download {filename}.</Caption>
      </QrStep>
    </div>
  )
}

function QrStep({ step, title, children }) {
  return (
    <div className="flex flex-col items-center p-6 bg-white border border-border rounded-lg">
      <div className="flex items-center gap-2 mb-4 self-start">
        <span className="w-6 h-6 rounded-full bg-accentDeep text-white text-xs font-bold flex items-center justify-center">{step}</span>
        <span className="text-sm font-semibold text-dark">{title}</span>
      </div>
      {children}
    </div>
  )
}

function Caption({ children }) {
  return <div className="text-xs text-dark2 mt-3 text-center max-w-[300px]">{children}</div>
}

/**
 * DownloadQr in a pop-up: what every Download button opens, since the kiosk doesn't save files
 * on the Pi -- the file goes to a phone instead.
 */
export function DownloadQrModal({ downloadUrl, filename, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60] p-4" onClick={onClose}
      role="dialog" aria-modal="true" aria-label={`Download ${filename} to a phone`}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-full overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-start gap-3">
          <div>
            <div className="font-semibold text-sm text-dark">Download to a phone</div>
            <div className="text-xs text-dark2 break-all">{filename}</div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
            className="text-dark2 hover:text-dark text-xl leading-none px-1">×</button>
        </div>
        <DownloadQr downloadUrl={downloadUrl} filename={filename} title="Download the file" />
      </div>
    </div>
  )
}
