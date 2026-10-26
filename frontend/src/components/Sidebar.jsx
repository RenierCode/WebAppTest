import { Menu, Gauge, Bell, Database, FileText, Settings, Info, Accessibility } from 'lucide-react'
import logo from '../assets/logo.svg'
import ShutdownButton from './ShutdownButton.jsx'
import { formatDuration } from '../time.js'

export const NAV_ITEMS = [
  { key: 'dashboard', icon: Gauge, label: 'Main Dashboard' },
  { key: 'notifications', icon: Bell, label: 'Alerts' },
  { key: 'history', icon: Database, label: 'Historical Logs' },
  { key: 'reports', icon: FileText, label: 'Reports' },
]
export const SYSTEM_ITEMS = [
  //{ key: 'admin', icon: Settings, label: 'Admin & Thresholds' },
  { key: 'about', icon: Info, label: 'About ARGUS' },
  { key: 'preferences', icon: Accessibility, label: 'Preferences & Accessibility' },
]

export default function Sidebar({ collapsed: collapsedSetting, setCollapsed, view, setView: selectView, nodes, nodeContext, setNodeContext, diagnostics, nodeStatus,
  isMobile = false, mobileOpen = false, onMobileClose }) {
  // On a phone the sidebar is a slide-in drawer: always full width, closed after picking a page
  const collapsed = isMobile ? false : collapsedSetting
  const setView = (key) => { selectView(key); if (isMobile) onMobileClose?.() }
  return (
    <>
    {isMobile && mobileOpen && (
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onMobileClose} aria-hidden="true" />
    )}
    <div
      className={isMobile
        ? `fixed inset-y-0 left-0 z-50 h-full flex flex-col bg-dark text-white shadow-xl transition-transform duration-200 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'}`
        : 'h-full flex flex-col bg-dark text-white flex-shrink-0 transition-[width] duration-200'}
      style={{ width: collapsed ? 64 : 260 }}
      aria-hidden={isMobile && !mobileOpen ? 'true' : undefined}
    >
      <div className="flex items-center justify-between p-4 border-b border-slate-800">
        {!collapsed && (
          <div className="flex items-center gap-2 font-extrabold tracking-wider text-sm whitespace-nowrap">
            <img src={logo} alt="ARGUS" className="w-7 h-7 object-contain" />
            ARGUS
          </div>
        )}
        <button className="text-white p-1" onClick={() => (isMobile ? onMobileClose?.() : setCollapsed(!collapsed))}
          aria-label={isMobile ? 'Close menu' : 'Toggle navigation'}>
          <Menu size={20} />
        </button>
      </div>

      {/* Only worth choosing between nodes once there is more than one */}
      {!collapsed && nodes.length > 1 && (
        <div className="p-3 border-b border-slate-800">
          <div className="text-[10px] uppercase tracking-wider text-slate-400 mb-1.5">Showing</div>
          <select
            className="w-full p-2 rounded-md border border-slate-700 bg-slate-800 text-white text-sm"
            value={nodeContext}
            onChange={(e) => setNodeContext(e.target.value)}
          >
            {nodes.map((n) => (
              <option key={n.node_id} value={n.node_id}>{n.label}</option>
            ))}
            <option value="all">All Nodes Overview</option>
          </select>
        </div>
      )}

      <div className="flex-1 min-h-0 p-2">
        {NAV_ITEMS.map((item) => (
          <NavRow key={item.key} item={item} active={view === item.key} collapsed={collapsed} onClick={() => setView(item.key)} />
        ))}
      </div>

      <div className="p-2 border-t border-slate-800">
        {SYSTEM_ITEMS.map((item) => (
          <NavRow key={item.key} item={item} active={view === item.key} collapsed={collapsed} onClick={() => setView(item.key)} />
        ))}
        <ShutdownButton collapsed={collapsed} />
      </div>

      {!collapsed && (
        <div className="p-3 border-t border-slate-800 text-[11px] text-slate-400 space-y-1">
          {nodes.map((n) => {
            const status = nodeStatus?.[String(n.node_id)]
            const online = status?.is_online
            return (
              <div key={n.node_id}>
                <span
                  className="inline-block w-2 h-2 rounded-full mr-1.5"
                  style={{ background: online ? '#22c55e' : '#DC2626' }}
                />
                {n.label}: {online ? 'Online' : status ? `Offline (last data ${formatDuration(status.seconds_since_last_reading)} ago)` : 'Checking…'}
              </div>
            )
          })}
          <div>
            <span className="inline-block w-2 h-2 rounded-full bg-green-500 mr-1.5" />
            Database: {diagnostics ? 'Connected' : 'Checking…'}
          </div>
        </div>
      )}
    </div>
    </>
  )
}

function NavRow({ item, active, collapsed, onClick }) {
  const Icon = item.icon
  return (
    <div
      className={`relative flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer text-sm mb-0.5 min-w-0 ${
        active ? 'bg-accentDeep text-white' : 'text-slate-300 hover:bg-slate-800'
      }`}
      data-tooltip={collapsed ? item.label : undefined}
      aria-label={item.label}
      tabIndex={0}
      onClick={onClick}
    >
      <Icon size={18} className="flex-shrink-0" />
      {!collapsed && <span className="min-w-0 whitespace-normal break-words leading-tight">{item.label}</span>}
    </div>
  )
}
