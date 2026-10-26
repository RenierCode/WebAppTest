import { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'
import { formatDbTime, localDateToDb } from '../time.js'
import DownloadQr, { DownloadQrModal } from '../components/DownloadQr.jsx'
import FilePreview from '../components/FilePreview.jsx'

export default function Reports({ nodes }) {
  const [nodeId, setNodeId] = useState(nodes[0]?.node_id ?? 1)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [reportType, setReportType] = useState('Full Audit')
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [files, setFiles] = useState(null)        // saved reports + CSV exports, newest first
  const [previewing, setPreviewing] = useState(null)   // { filename, download_url } shown in the preview pop-up
  const [sharing, setSharing] = useState(null)         // { filename, download_url } whose download QR pop-up is open

  const loadFiles = useCallback(() => {
    api.reportFiles().then(setFiles).catch(() => setFiles([]))
  }, [])
  useEffect(() => { loadFiles() }, [loadFiles])

  const generate = async () => {
    setError('')
    setResult(null)
    if (!start || !end) { setError('Pick a start and end date.'); return }
    setLoading(true)
    try {
      // The picked days in local time, end day included, as the database's UTC timestamps
      const res = await api.generateReport({
        node_id: Number(nodeId), start: localDateToDb(start), end: localDateToDb(end, true),
        report_type: reportType, tz_offset: new Date().getTimezoneOffset(),
      })
      setResult(res)
      loadFiles()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="bg-white border border-border rounded-lg px-5 py-3.5 mb-5 font-bold text-accentDeep">
        Reports
      </div>

      <div className="bg-white border border-border rounded-lg p-5">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-xl">
          <Field label="Node">
            <select className="w-full p-2.5 border border-border rounded-md text-sm" value={nodeId} onChange={(e) => setNodeId(e.target.value)}>
              {nodes.map((n) => <option key={n.node_id} value={n.node_id}>{n.label}</option>)}
            </select>
          </Field>
          <Field label="Report type">
            <select className="w-full p-2.5 border border-border rounded-md text-sm" value={reportType} onChange={(e) => setReportType(e.target.value)}>
              <option value="Full Audit">Full report (all readings)</option>
              <option value="Anomaly Log Only">Alerts only (readings with an alert)</option>
            </select>
          </Field>
          <Field label="Start Date">
            <input type="date" className="w-full p-2.5 border border-border rounded-md text-sm" value={start} onChange={(e) => setStart(e.target.value)} />
          </Field>
          <Field label="End Date">
            <input type="date" className="w-full p-2.5 border border-border rounded-md text-sm" value={end} onChange={(e) => setEnd(e.target.value)} />
          </Field>
        </div>

        <button
          className="mt-4.5 mt-4 px-4 py-2 bg-accentDeep text-white rounded-md text-sm disabled:opacity-50"
          onClick={generate}
          disabled={loading}
        >
          {loading ? 'Generating…' : 'Generate Report'}
        </button>

        {error && <div className="mt-3 text-sm text-alert">{error}</div>}

        {result && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-5">
              <Stat v={result.summary.mean_ph} l="Mean pH" />
              <Stat v={result.summary.max_turbidity} l="Peak Turbidity (NTU)" />
              <Stat v={`${result.summary.anomaly_pct}%`} l="Anomaly Frequency" />
              <Stat v={`${result.summary.total_volume_l} L`} l="Total Discharge Volume" />
            </div>

            <div className="flex flex-wrap items-center gap-3 mt-5 text-sm">
              <span className="text-dark2 break-all">Saved as <b className="text-dark">{result.filename}</b></span>
              <button className="px-3 py-1.5 bg-accentDeep text-white rounded-md text-xs"
                onClick={() => setPreviewing(result)}>Preview</button>
            </div>
            <DownloadQr downloadUrl={result.download_url} filename={result.filename} title="Download the report" />
          </>
        )}
      </div>

      <SavedReports files={files} onRefresh={loadFiles} onPreview={setPreviewing} onShare={setSharing} />

      {previewing && <FilePreview filename={previewing.filename} downloadUrl={previewing.download_url} onClose={() => setPreviewing(null)} />}
      {sharing && <DownloadQrModal downloadUrl={sharing.download_url} filename={sharing.filename} onClose={() => setSharing(null)} />}
    </div>
  )
}

// Every report and CSV export saved on the Pi (REPORTS_DIR), newest first. Delete switches the
// row buttons to checkboxes; deleting the selection needs a confirmation that unlocks after 5 s.
function SavedReports({ files, onRefresh, onPreview, onShare }) {
  const size = (bytes) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`)
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState(() => new Set())
  const [confirming, setConfirming] = useState(false)
  const [message, setMessage] = useState('')

  // Drop selections of files that are gone (deleted elsewhere, or by the 7-day clean-up)
  useEffect(() => {
    if (!files) return
    setSelected((prev) => new Set([...prev].filter((name) => files.some((f) => f.filename === name))))
  }, [files])

  const toggle = (name) => setSelected((prev) => {
    const next = new Set(prev)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    return next
  })
  const allSelected = files?.length > 0 && selected.size === files.length
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(files.map((f) => f.filename)))
  const stopSelecting = () => { setSelecting(false); setSelected(new Set()) }

  const remove = async () => {
    const names = [...selected]
    const res = await api.deleteReports(names)
    setConfirming(false)
    stopSelecting()
    setMessage(res.missing.length
      ? `Deleted ${res.deleted.length} file(s); ${res.missing.length} were already gone.`
      : `Deleted ${res.deleted.length} file(s).`)
    onRefresh()
  }

  return (
    <div className="bg-white border border-border rounded-lg p-5 mt-5">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div>
          <div className="font-bold text-accentDeep">Saved reports & exports{files ? ` (${files.length})` : ''}</div>
          <div className="text-xs text-dark2">Files are deleted automatically 7 days after they're saved. Download any you need to keep to a phone.</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {selecting ? (
            <>
              <span className="text-xs text-dark2">{selected.size} selected</span>
              <button className="px-3 py-1 bg-alert text-white rounded-md text-xs disabled:opacity-40"
                disabled={selected.size === 0} onClick={() => setConfirming(true)}>
                Delete{selected.size ? ` (${selected.size})` : ''}
              </button>
              <button className="px-3 py-1 border border-border rounded-md text-xs" onClick={stopSelecting}>Cancel</button>
            </>
          ) : (
            <>
              <button className="px-3 py-1 border border-alert text-alert rounded-md text-xs disabled:opacity-40"
                disabled={!files?.length} onClick={() => { setMessage(''); setSelecting(true) }}>Delete</button>
              <button className="px-3 py-1 border border-border rounded-md text-xs" onClick={onRefresh}>Refresh</button>
            </>
          )}
        </div>
      </div>
      {message && <div className="text-xs text-green-700 mb-2">{message}</div>}
      {files == null ? (
        <div className="text-sm text-dark2">Loading…</div>
      ) : files.length === 0 ? (
        <div className="text-sm text-dark2">No saved reports yet. Generated PDFs and Historical Logs CSV exports appear here.</div>
      ) : (
        <div className="max-h-[480px] overflow-auto border border-border rounded-lg">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-canvas2 text-dark2 text-[11px] uppercase sticky top-0">
                {['File', 'Type', 'Period (local)', 'Saved', 'Deleted on', 'Size'].map((h) => (
                  <th key={h} className="text-left px-2.5 py-2 whitespace-nowrap">{h}</th>
                ))}
                <th className="text-left px-2.5 py-2 whitespace-nowrap">
                  {selecting && (
                    <label className="flex items-center gap-1.5 normal-case font-normal cursor-pointer">
                      <input type="checkbox" className="h-4 w-4" checked={allSelected} onChange={toggleAll} />
                      Select all
                    </label>
                  )}
                </th>
              </tr>
            </thead>
            <tbody>
              {files.map((f) => (
                <tr key={f.filename}
                  className={selecting ? `cursor-pointer ${selected.has(f.filename) ? 'bg-red-50' : 'hover:bg-canvas2'}` : ''}
                  onClick={selecting ? () => toggle(f.filename) : undefined}>
                  <td className="px-2.5 py-2 border-b border-border">
                    <span className={`inline-block text-[10px] font-bold px-1.5 py-0.5 rounded mr-2 ${
                      f.format === 'PDF' ? 'bg-red-50 text-alert' : 'bg-green-50 text-green-700'}`}>{f.format}</span>
                    <span className="break-all">{f.filename}</span>
                  </td>
                  <td className="px-2.5 py-2 border-b border-border whitespace-nowrap">
                    {f.report_type ? <>{f.report_type}<div className="text-xs text-dark2">{f.scope}</div></> : <span className="text-dark2">—</span>}
                  </td>
                  <td className="px-2.5 py-2 border-b border-border whitespace-nowrap text-xs">
                    {f.start ? <>{f.start}<br />to {f.end}</> : <span className="text-dark2">—</span>}
                  </td>
                  <td className="px-2.5 py-2 border-b border-border whitespace-nowrap text-xs">{formatDbTime(f.created)}</td>
                  <td className="px-2.5 py-2 border-b border-border whitespace-nowrap text-xs text-dark2">{formatDbTime(f.expires)}</td>
                  <td className="px-2.5 py-2 border-b border-border whitespace-nowrap text-xs">{size(f.size)}</td>
                  <td className="px-2.5 py-2 border-b border-border whitespace-nowrap">
                    {selecting ? (
                      <input type="checkbox" className="h-4 w-4 cursor-pointer" aria-label={`Select ${f.filename}`}
                        checked={selected.has(f.filename)} onChange={() => toggle(f.filename)}
                        onClick={(e) => e.stopPropagation()} />
                    ) : (
                      <div className="flex gap-1.5">
                        <button className="px-2.5 py-1 bg-accentDeep text-white rounded-md text-xs" onClick={() => onPreview(f)}>Preview</button>
                        <button className="px-2.5 py-1 border border-accentDeep text-accentDeep rounded-md text-xs" onClick={() => onShare(f)}>Download</button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {confirming && (
        <ConfirmDelete names={[...selected]} onCancel={() => setConfirming(false)} onConfirm={remove} />
      )}
    </div>
  )
}

const CONFIRM_SECONDS = 5

// "Are you sure" for deleting saved files: the Delete button unlocks after a 5-second countdown
function ConfirmDelete({ names, onCancel, onConfirm }) {
  const [left, setLeft] = useState(CONFIRM_SECONDS)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (left <= 0) return undefined
    const t = setTimeout(() => setLeft((n) => n - 1), 1000)
    return () => clearTimeout(t)
  }, [left])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel, busy])

  const confirm = async () => {
    setBusy(true)
    setError('')
    try {
      await onConfirm()
    } catch (e) {
      setError(e.message)
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60] p-4" onClick={busy ? undefined : onCancel}
      role="alertdialog" aria-modal="true" aria-label="Confirm deleting files">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="font-bold text-alert mb-1">Delete {names.length} file{names.length === 1 ? '' : 's'}?</div>
        <div className="text-sm text-dark2 mb-3">
          They are removed from the Pi and can't be recovered. Phones that already downloaded them keep their copies.
        </div>
        <ul className="max-h-40 overflow-auto text-xs bg-canvas2 border border-border rounded p-2 mb-4 space-y-0.5">
          {names.map((name) => <li key={name} className="break-all">{name}</li>)}
        </ul>
        {error && <div className="text-sm text-alert mb-3">{error}</div>}
        <div className="flex justify-end gap-2">
          <button className="px-4 py-1.5 border border-border rounded-md text-sm" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="px-4 py-1.5 bg-alert text-white rounded-md text-sm disabled:opacity-40 min-w-[110px]"
            disabled={left > 0 || busy} onClick={confirm}>
            {busy ? 'Deleting…' : left > 0 ? `Delete (${left})` : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-dark2 mb-1.5">{label}</label>
      {children}
    </div>
  )
}

function Stat({ v, l }) {
  return (
    <div className="bg-white border border-border rounded-lg p-3.5 text-center">
      <div className="text-xl font-extrabold text-accentDeep">{v ?? '—'}</div>
      <div className="text-[11px] text-dark2 mt-1">{l}</div>
    </div>
  )
}
