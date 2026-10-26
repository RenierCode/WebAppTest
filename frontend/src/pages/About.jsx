import { Activity, BrainCircuit, Eye, ShieldCheck, Users, Waves } from 'lucide-react'

const sensors = ['pH', 'Temperature', 'Turbidity', 'Water Level', 'Flow Rate']

const principles = [
  {
    icon: Activity,
    title: 'Real-Time Monitoring',
    text: 'Continuously collect and visualize operational sensor data.',
  },
  {
    icon: BrainCircuit,
    title: 'Intelligent Detection',
    text: 'Use machine-learning techniques to identify unusual patterns.',
  },
  {
    icon: Eye,
    title: 'Centralized Visibility',
    text: 'Bring data from distributed monitoring points into one platform.',
  },
  {
    icon: ShieldCheck,
    title: 'Human-Centered Decisions',
    text: 'Support operators with useful information while keeping human verification involved.',
  },
]

const team = [
  { name: 'Jann Moises Nyll De los Reyes', role: 'Data Science' },
  { name: 'Julianna Mae Dolot', role: 'System Administration' },
  { name: 'Renier Lope', role: 'System Administration' },
  { name: 'Chester Sangcap', role: 'Cyber-Physical System' },
  { name: 'Nathaniel Valerio', role: 'Technopreneurship' },
  { name: 'Engr. Cris Paulo Hate', role: 'Adviser' },
]

export default function About() {
  return (
    <main className="w-full max-w-6xl mx-auto">
      <section className="bg-dark text-white rounded-lg p-5 sm:p-8 md:p-12 mb-6">
        <div className="flex flex-wrap items-center gap-3 text-sky-300 text-xs sm:text-sm font-semibold uppercase tracking-widest mb-4">
          <Waves size={20} />
          PROJECT ARGUS
        </div>

        <h1 className="text-3xl sm:text-4xl md:text-5xl font-extrabold tracking-tight leading-tight max-w-3xl">
          THE ARGUS PROJECT
        </h1>

        <p className="mt-5 max-w-3xl text-slate-300 leading-7 text-sm sm:text-base">
          ARGUS is an IoT and machine-learning-based monitoring system designed
          to help identify irregularities in wastewater treatment operations
          before they become larger operational problems.
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        <InfoBlock title="What Does ARGUS Do?">
          <p>
            ARGUS connects the water-quality sensor nodes to a
            centralized monitoring hub. Sensor readings are transmitted through
            a LoRa-based communication network, then stored, analyzed, and
            visualized for operators.
          </p>

          <div className="flex flex-wrap gap-2 mt-5">
            {sensors.map((sensor) => (
              <span
                key={sensor}
                className="px-3 py-1.5 rounded-full bg-sky-50 text-sky-800 text-sm font-semibold"
              >
                {sensor}
              </span>
            ))}
          </div>
        </InfoBlock>

        <InfoBlock title="From Sensors to Insights">
          <p className="mt-5">
            This creates a bridge between physical water-treatment operations
            and digital intelligence. Machine-learning models examine incoming
            data and identify observations that deviate from expected patterns.
          </p>
        </InfoBlock>
      </section>

      <section className="bg-white border border-border rounded-lg p-6 md:p-8 mb-6">
        <SectionHeading
          icon={Users}
          title="The Team Behind ARGUS"
          subtitle="Engineering students combining software, data, IoT, and systems thinking."
        />

        <p className="text-dark2 leading-7 max-w-4xl mb-6">
          Project ARGUS is built by a team bringing together knowledge from
          software development, data science, IoT, machine learning,
          technopreneurship, and systems engineering.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {team.map((member) => (
            <div
              key={member.name}
              className="border border-border rounded-md p-4 min-w-0"
            >
              <div className="font-bold text-accentDeep break-words">{member.name}</div>
              <div className="text-sm text-dark2 mt-1">{member.role}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="bg-white border border-border rounded-lg p-6 md:p-8 mb-6">
        <SectionHeading
          icon={ShieldCheck}
          title="Why ARGUS?"
          subtitle="Built to support investigation, monitoring, and better operational decisions."
        />

        <p className="max-w-4xl text-dark2 leading-7">
          Water treatment systems continuously generate operational data, but
          meaningful abnormalities can be difficult to identify when information
          is distributed across different sensors and monitoring processes.
          ARGUS combines IoT and machine learning to provide centralized,
          data-driven monitoring. It is designed to support human operators,
          not replace their judgment.
        </p>
      </section>

      <section className="mb-6">
        <SectionHeading
          icon={BrainCircuit}
          title="Our Approach"
          subtitle="Four principles guide the design of Project ARGUS."
        />

        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {principles.map(({ icon: Icon, title, text }) => (
            <article
              key={title}
              className="bg-white border border-border rounded-lg p-5"
            >
              <Icon size={24} className="text-accentDeep mb-4" />
              <h3 className="font-bold text-accentDeep">{title}</h3>
              <p className="text-sm text-dark2 leading-6 mt-2">{text}</p>
            </article>
          ))}
        </div>
      </section>
      
    </main>
  )
}

function InfoBlock({ title, children }) {
  return (
    <section className="bg-white border border-border rounded-lg p-5 sm:p-6 md:p-8">
      <h2 className="text-xl sm:text-2xl font-extrabold text-accentDeep mb-4">{title}</h2>
      <div className="text-dark2 leading-7">{children}</div>
    </section>
  )
}

function HeroStat({ value, label }) {
  return (
    <div className="border border-slate-700 rounded-md px-4 py-3 min-w-0">
      <div className="font-extrabold text-lg text-white break-words">{value}</div>
      <div className="text-xs text-slate-400 mt-1">{label}</div>
    </div>
  )
}

function SectionHeading({ icon: Icon, title, subtitle }) {
  return (
    <div className="flex items-start gap-3 mb-5">
      <Icon size={24} className="text-accentDeep mt-0.5 flex-shrink-0" />
      <div>
        <h2 className="text-xl font-extrabold text-accentDeep">{title}</h2>
        <p className="text-sm text-dark2 mt-1">{subtitle}</p>
      </div>
    </div>
  )
}