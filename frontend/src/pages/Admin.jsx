import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function Admin({ nodes, refreshNodes }) {
  const [thresholds, setThresholds] = useState({})     // node_id -> row
  const [mlConfig, setMlConfig] = useState({})          // node_id -> row
  const [diagnostics, setDiagnostics] = useState(null)
  const [newNodeId, setNewNodeId] = useState('')
  const [newNodeLabel, setNewNodeLabel] = useState('')
  const [status, setStatus] = useState('')

  const loadAll = () => {
    api.thresholdsGet().then((rows) => setThresholds(Object.fromEntries(rows.map((r) => [r.node_id, r]))))
    api.mlConfigGet().then((rows) => setMlConfig(Object.fromEntries(rows.map((r) => [r.node_id, r]))))
    api.diagnostics().then(setDiagnostics)
  }

  useEffect(() => { loadAll() }, [])

  const editThreshold = (nodeId, field, value) => {
    setThresholds((prev) => ({ ...prev, [nodeId]: { ...prev[nodeId], [field]: value } }))
  }

  const saveThresholds = async (nodeId) => {
    const t = thresholds[nodeId]
    setStatus('Saving…')
    try {
      await api.thresholdsPut({
        node_id: nodeId,
        min_ph: Number(t.min_ph), max_ph: Number(t.max_ph),
        max_turbidity: Number(t.max_turbidity),
        min_temp: Number(t.min_temp), max_temp: Number(t.max_temp),
        max_level: Number(t.max_level), min_level: Number(t.min_level),
        max_flow: Number(t.max_flow),
      })
      setStatus(`Saved thresholds for node ${nodeId}`)
    } catch (e) {
      setStatus(e.message)
    }
  }

  const addNode = async () => {
    if (!newNodeId || !newNodeLabel) return
    await api.nodesPut({ node_id: Number(newNodeId), label: newNodeLabel })
    setNewNodeId('')
    setNewNodeLabel('')
    refreshNodes()
    loadAll()
  }

  const renameNode = async (nodeId, label) => {
    await api.nodesPut({ node_id: nodeId, label })
    refreshNodes()
  }

  return (
    <div>
      <div className="bg-white border border-border rounded-lg px-5 py-3.5 mb-5 font-bold text-accentDeep">
        Administrative Calibration
      </div>

      <Block title="Mechanical Safety Envelopes (per node)">
        {nodes.map((n) => {
          const t = thresholds[n.node_id] || {}
          return (
            <div key={n.node_id} className="grid grid-cols-3 gap-4 mb-3.5">
              <NumberField label={`${n.label} — Max Level %`} value={t.max_level} onChange={(v) => editThreshold(n.node_id, 'max_level', v)} />
              <NumberField label="Low-Water Trigger %" value={t.min_level} onChange={(v) => editThreshold(n.node_id, 'min_level', v)} />
              <NumberField label="Max Flow Ceiling (L/min)" value={t.max_flow} onChange={(v) => editThreshold(n.node_id, 'max_flow', v)} />
            </div>
          )
        })}
      </Block>

      <Block title="Baseline Parameter Thresholds — Rule-Based If/Else Check (per node)">
        <p className="text-xs text-dark2 mb-3.5">
          Evaluated by edge_engine.py on every packet, independent of the ML model below. Any reading
          outside its min/max fires a WARNING or CRITICAL notification immediately.
        </p>
        {nodes.map((n) => {
          const t = thresholds[n.node_id] || {}
          return (
            <div key={n.node_id} className="mb-4">
              <div className="font-bold text-sm mb-2">{n.label} (node {n.node_id})</div>
              <div className="grid grid-cols-3 gap-4">
                <NumberField label="pH — Min" step="0.1" value={t.min_ph} onChange={(v) => editThreshold(n.node_id, 'min_ph', v)} />
                <NumberField label="pH — Max" step="0.1" value={t.max_ph} onChange={(v) => editThreshold(n.node_id, 'max_ph', v)} />
                <NumberField label="Turbidity — Max (NTU)" value={t.max_turbidity} onChange={(v) => editThreshold(n.node_id, 'max_turbidity', v)} />
                <NumberField label="Temperature — Min (°C)" step="0.1" value={t.min_temp} onChange={(v) => editThreshold(n.node_id, 'min_temp', v)} />
                <NumberField label="Temperature — Max (°C)" step="0.1" value={t.max_temp} onChange={(v) => editThreshold(n.node_id, 'max_temp', v)} />
              </div>
              <button className="mt-3 px-4 py-1.5 bg-accentDeep text-white rounded-md text-sm" onClick={() => saveThresholds(n.node_id)}>
                Save {n.label} Thresholds
              </button>
            </div>
          )
        })}
        {status && <div className="text-xs text-dark2 mt-1">{status}</div>}
      </Block>

      <Block title="ML Model Sensitivity — Read Only">
        <p className="text-xs text-dark2 mb-3.5">
          Not a live control. The trained model's contamination rate is baked into its decision
          boundary at training time and exported into the .onnx file edge_engine.py runs — there's no
          way to adjust it from this page without retraining and redeploying a new model. Shown for
          visibility only.
        </p>
        {nodes.map((n) => {
          const c = mlConfig[n.node_id]?.contamination ?? 0.02
          return (
            <div key={n.node_id} className="flex items-center gap-3.5 mb-2.5">
              <span className="text-sm w-28 flex-shrink-0">{n.label}</span>
              <b>{Number(c).toFixed(2)}</b>
            </div>
          )
        })}
        <p className="text-xs text-dark2 mt-2">
          To change this: <code>python train_anomaly_model.py --contamination 0.0X</code>, then{' '}
          <code>convert_to_onnx.py</code>, then copy the result to{' '}
          <code>models/anomaly_pipeline.onnx</code> on the Pi. See{' '}
          <code>models/training/GUIDE_train_anomaly_model.md</code>.
        </p>
      </Block>

      <Block title="Node Identifier Management">
        {nodes.map((n) => (
          <div key={n.node_id} className="flex gap-2.5 mb-2.5">
            <input value={n.node_id} disabled className="max-w-[100px] p-2 border border-border rounded-md text-sm bg-canvas2" />
            <input
              defaultValue={n.label}
              className="flex-1 p-2 border border-border rounded-md text-sm"
              onBlur={(e) => e.target.value !== n.label && renameNode(n.node_id, e.target.value)}
            />
          </div>
        ))}
        <div className="flex gap-2.5 mt-3">
          <input placeholder="Node ID (e.g. 3)" value={newNodeId} onChange={(e) => setNewNodeId(e.target.value)}
            className="max-w-[140px] p-2 border border-border rounded-md text-sm" />
          <input placeholder="Label (e.g. Intake Basin)" value={newNodeLabel} onChange={(e) => setNewNodeLabel(e.target.value)}
            className="flex-1 p-2 border border-border rounded-md text-sm" />
          <button className="px-4 py-2 border border-accentDeep text-accentDeep rounded-md text-sm bg-white" onClick={addNode}>
            + Register Node
          </button>
        </div>
      </Block>

      <Block title="Engine Diagnostics">
        <div className="grid grid-cols-3 gap-3.5">
          <Diag v={diagnostics?.row_count ?? '—'} l="Total DB Rows" />
          <Diag v={diagnostics ? `${diagnostics.db_size_mb} MB` : '—'} l="database.db size" />
          <Diag v={diagnostics?.connection_status ?? '—'} l="Connection" ok={diagnostics?.connection_status === 'Connected'} />
        </div>
      </Block>
    </div>
  )
}

function Block({ title, children }) {
  return (
    <div className="bg-white border border-border rounded-lg p-5 mb-5">
      <h4 className="text-xs uppercase tracking-wide text-dark2 mb-3.5">{title}</h4>
      {children}
    </div>
  )
}

function NumberField({ label, value, onChange, step }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-dark2 mb-1.5">{label}</label>
      <input
        type="number" step={step} value={value ?? ''}
        className="w-full p-2.5 border border-border rounded-md text-sm"
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

function Diag({ v, l, ok }) {
  return (
    <div className="bg-canvas2 rounded-lg p-3.5 text-center">
      <div className="text-xl font-extrabold" style={ok === false ? { color: '#DC2626' } : ok ? { color: '#16a34a' } : undefined}>{v}</div>
      <div className="text-[11px] text-dark2 mt-1">{l}</div>
    </div>
  )
}
