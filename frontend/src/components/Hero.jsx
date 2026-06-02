import React from 'react'

const STATS = [
  { label: 'Languages', value: '4' },
  { label: 'Sample Length', value: '30s' },
  { label: 'Output Format', value: 'WAV' },
  { label: 'API Latency', value: '<2s' },
]

const LANGS = [
  { label: 'తెలుగు Telugu' },
  { label: 'தமிழ் Tamil' },
  { label: 'हिन्दी Hindi' },
  { label: 'English' },
]

export default function Hero() {
  return (
    <section className="relative min-h-[88vh] flex items-center justify-center overflow-hidden px-4 py-16">
      {/* Background orbs */}
      <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
        <div className="absolute top-1/4 left-1/4 w-48 sm:w-64 h-48 sm:h-64 bg-neon-cyan/5 rounded-full blur-3xl animate-pulse-slow" />
        <div
          className="absolute bottom-1/4 right-1/4 w-64 sm:w-80 h-64 sm:h-80 bg-neon-purple/5 rounded-full blur-3xl animate-pulse-slow"
          style={{ animationDelay: '1.5s' }}
        />
      </div>

      <div className="relative z-10 text-center max-w-4xl mx-auto w-full">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 bg-neon-cyan/10 border border-neon-cyan/30 rounded-full px-3 sm:px-4 py-1.5 sm:py-2 text-xs text-neon-cyan mb-6 sm:mb-8">
          <span className="w-2 h-2 rounded-full bg-neon-cyan animate-ping flex-shrink-0" />
          Phase 1 · Voice Cloning Platform · Now Live
        </div>

        {/* Heading */}
        <h1
          className="text-4xl sm:text-6xl md:text-7xl lg:text-8xl font-black tracking-tight mb-5 sm:mb-6 leading-none"
          style={{ fontFamily: 'Orbitron, monospace' }}
        >
          <span className="block text-white">INDIC</span>
          <span className="block bg-gradient-to-r from-neon-cyan via-cyber-400 to-neon-purple bg-clip-text text-transparent">
            VOICE AI
          </span>
        </h1>

        {/* Sub-title */}
        <p className="text-base sm:text-xl md:text-2xl text-slate-300 mb-4 font-light">
          AI-Powered Voice Cloning for
        </p>

        {/* Language chips */}
        <div className="flex flex-wrap justify-center gap-2 sm:gap-3 mb-8 sm:mb-10">
          {LANGS.map((l, i) => (
            <span
              key={i}
              className="px-3 sm:px-4 py-1 sm:py-1.5 bg-dark-700 border border-neon-cyan/20 rounded-full text-xs sm:text-sm text-neon-cyan font-medium hover:border-neon-cyan/60 transition-all cursor-default"
            >
              {l.label}
            </span>
          ))}
        </div>

        {/* CTA buttons */}
        <div className="flex flex-col xs:flex-row items-center justify-center gap-3 sm:gap-4">
          <a
            href="#generator"
            className="group w-full xs:w-auto px-6 sm:px-8 py-3 sm:py-4 bg-gradient-to-r from-neon-cyan to-cyber-500 text-dark-900 font-bold text-sm rounded-lg hover:scale-105 transition-all duration-200 shadow-lg shadow-neon-cyan/25 text-center"
            style={{ fontFamily: 'Orbitron, monospace' }}
          >
            START CLONING
            <span className="ml-2 group-hover:translate-x-1 inline-block transition-transform">→</span>
          </a>
          <a
            href="http://localhost:8000/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="w-full xs:w-auto px-6 sm:px-8 py-3 sm:py-4 border border-neon-cyan/30 text-neon-cyan font-medium text-sm rounded-lg hover:bg-neon-cyan/10 transition-all duration-200 text-center"
          >
            View API Docs
          </a>
        </div>

        {/* Stats grid — 2 cols on xs, 4 cols on sm+ */}
        <div className="mt-12 sm:mt-16 grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4 max-w-2xl mx-auto">
          {STATS.map((s, i) => (
            <div
              key={i}
              className="bg-dark-800/60 border border-neon-cyan/10 rounded-lg p-3 sm:p-4 text-center hover:border-neon-cyan/30 transition-all"
            >
              <div
                className="text-xl sm:text-2xl font-bold text-neon-cyan"
                style={{ fontFamily: 'Orbitron, monospace' }}
              >
                {s.value}
              </div>
              <div className="text-xs text-slate-500 mt-1">{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
