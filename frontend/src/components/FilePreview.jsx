import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { DownloadQrModal } from './DownloadQr.jsx'

/**
 * Look at a saved report without downloading it: a PDF as page images rendered on the Pi (so it
 * works in browsers with no PDF viewer, which would only offer to save it), a CSV as a table of
 * its first rows. Closes on Escape or a click outside. Download shows the phone QR codes
 * (downloadUrl: the file's full link a phone can reach).
 */
export default function FilePreview({ filename, downloadUrl, onClose }) {
  const isPdf = filename.toLowerCase().endsWith('.pdf')
  const [table, setTable] = useState(null)       // CSV: { header, rows, total, shown }
  const [pages, setPages] = useState(null)       // PDF: page count
  const [error, setError] = useState('')
  const [showQr, setShowQr] = useState(false)

  useEffect(() => {
    if (isPdf) api.reportPages(filename).then((r) => setPages(r.pages)).catch((e) => setError(e.message))
    else api.reportPreview(filename).then(setTable).catch((e) => setError(e.message))
  }, [filename, isPdf])

  useEffect(() => {
    if (showQr) return undefined                 // Escape closes the QR pop-up first
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, showQr])

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={onClose}
      role="dialog" aria-modal="true" aria-label={`Preview of ${filename}`}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-6xl h-full max-h-[92vh] flex flex-col p-4"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 mb-3">
          <div className="font-semibold text-sm text-dark break-all">{filename}</div>
          <div className="flex items-center gap-2 shrink-0">
            {downloadUrl && (
              <button type="button" onClick={() => setShowQr(true)}
                className="px-3 py-1 border border-accentDeep text-accentDeep rounded-md text-xs">Download</button>
            )}
            <button type="button" onClick={onClose} aria-label="Close"
              className="text-dark2 hover:text-dark text-xl leading-none px-1">×</button>
          </div>
        </div>

        {error ? (
          <div className="text-sm text-alert">{error}</div>
        ) : isPdf ? (
          pages == null ? (
            <div className="flex-1 flex items-center justify-center text-sm text-dark2">Loading…</div>
          ) : (
            <>
              <div className="text-xs text-dark2 mb-2">
                {pages} page{pages === 1 ? '' : 's'}. Pages load as you scroll.
              </div>
              <div className="flex-1 overflow-auto bg-canvas2 border border-border rounded p-3 space-y-3">
                {Array.from({ length: pages }, (_, i) => (
                  <img key={i} src={api.reportPageUrl(filename, i + 1)} alt={`Page ${i + 1} of ${pages}`}
                    loading="lazy" className="block mx-auto max-w-full bg-white shadow border border-border"
                    style={{ minHeight: 200 }} />
                ))}
              </div>
            </>
          )
        ) : !table ? (
          <div className="flex-1 flex items-center justify-center text-sm text-dark2">Loading…</div>
        ) : (
          <>
            {table.summary?.length > 0 && <CsvSummary rows={table.summary} />}
            <div className="text-xs text-dark2 mb-2">
              {table.shown < table.total
                ? `Showing the first ${table.shown} of ${table.total} rows. Download the file for all of them.`
                : `${table.total} rows`}
            </div>
            <div className="flex-1 overflow-auto border border-border rounded">
              <table className="w-full text-xs border-collapse">
                <thead>
                  <tr className="bg-canvas2 text-dark2 sticky top-0">
                    {table.header.map((h) => <th key={h} className="text-left px-2 py-1.5 whitespace-nowrap">{h}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, i) => (
                    <tr key={i} className={i % 2 ? 'bg-canvas2/60' : ''}>
                      {row.map((value, j) => (
                        <td key={j} className="px-2 py-1 border-t border-border whitespace-nowrap">{value}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
      {showQr && <DownloadQrModal downloadUrl={downloadUrl} filename={filename} onClose={() => setShowQr(false)} />}
    </div>
  )
}

// The block above a CSV export's data: a title row, then label/value rows ("Summary" and
// "Measure, Value" are section headings in the file, not values)
function CsvSummary({ rows }) {
  const [title, ...rest] = rows
  const pairs = rest.filter((r) => r.length >= 2 && r[0] !== 'Measure')
  return (
    <div className="mb-3 border border-border rounded p-3 text-xs max-h-[40%] overflow-auto shrink-0">
      <div className="font-semibold text-sm text-accentDeep mb-2">{title[0]}</div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        {pairs.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-dark2 whitespace-nowrap">{label}</dt>
            <dd className="text-dark">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
