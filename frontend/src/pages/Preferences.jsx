import { useEffect, useState } from 'react'
import { Accessibility, Check, Eye, RotateCcw, Type } from 'lucide-react'

const DEFAULTS = {
  textSize: 'normal',
  highContrast: false,
  reducedMotion: false,
}

function loadPreferences() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem('argus-preferences') || '{}') }
  } catch {
    return DEFAULTS
  }
}

export default function Preferences() {
  const [preferences, setPreferences] = useState(loadPreferences)

  useEffect(() => {
    localStorage.setItem('argus-preferences', JSON.stringify(preferences))
    document.documentElement.dataset.textSize = preferences.textSize
    document.documentElement.classList.toggle('high-contrast', preferences.highContrast)
    document.documentElement.classList.toggle('reduced-motion', preferences.reducedMotion)
  }, [preferences])

  const update = (key, value) => setPreferences((current) => ({ ...current, [key]: value }))

  const reset = () => setPreferences(DEFAULTS)

  return (
    <main className="max-w-4xl mx-auto">
      <div className="bg-white border border-border rounded-lg px-5 py-4 mb-5">
        <div className="flex items-center gap-3">
          <Accessibility className="text-accentDeep" size={24} />
          <div>
            <h1 className="font-bold text-accentDeep text-lg">Preferences & Accessibility</h1>
            <p className="text-sm text-dark2 mt-1">Personalize how the ARGUS monitoring interface appears and moves.</p>
          </div>
        </div>
      </div>

      <section className="bg-white border border-border rounded-lg p-5 mb-5">
        <PreferenceHeading icon={Type} title="Text Size" />
        <p className="text-sm text-dark2 mb-4">Choose a comfortable reading size for dashboard labels and controls.</p>
        <div className="flex flex-wrap gap-2">
          {['normal', 'large', 'larger'].map((size) => (
            <button
              key={size}
              type="button"
              aria-pressed={preferences.textSize === size}
              onClick={() => update('textSize', size)}
              className={`px-4 py-2 rounded-md border text-sm capitalize ${preferences.textSize === size ? 'bg-accentDeep text-white border-accentDeep' : 'border-border text-accentDeep'}`}
            >
              {size}
              {preferences.textSize === size && <Check size={15} className="inline ml-2" />}
            </button>
          ))}
        </div>
      </section>

      <section className="bg-white border border-border rounded-lg p-5 mb-5">
        <PreferenceHeading icon={Eye} title="Visual Accessibility" />
        <Toggle
          label="High contrast mode"
          description="Increase contrast between text, surfaces, and borders."
          checked={preferences.highContrast}
          onChange={(value) => update('highContrast', value)}
        />
        <Toggle
          label="Reduced motion"
          description="Minimize interface transitions and animations."
          checked={preferences.reducedMotion}
          onChange={(value) => update('reducedMotion', value)}
        />
      </section>

      <button type="button" onClick={reset} className="inline-flex items-center gap-2 px-4 py-2 border border-border rounded-md text-sm text-accentDeep">
        <RotateCcw size={16} />
        Reset preferences
      </button>
    </main>
  )
}

function PreferenceHeading({ icon: Icon, title }) {
  return <h2 className="flex items-center gap-2 text-base font-bold text-accentDeep mb-2"><Icon size={19} />{title}</h2>
}

function Toggle({ label, description, checked, onChange }) {
  return (
    <label className="flex items-start justify-between gap-4 py-3 border-t border-border cursor-pointer">
      <span>
        <span className="block text-sm font-semibold">{label}</span>
        <span className="block text-xs text-dark2 mt-1">{description}</span>
      </span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-1 h-4 w-4" />
    </label>
  )
}