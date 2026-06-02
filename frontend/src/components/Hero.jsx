import React from 'react'

export default function Hero() {
  return (
    <section className="relative min-h-[90vh] flex items-center justify-center overflow-hidden px-4">
      {/* Animated background orbs */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/4 left-1/4 w-64 h-64 bg-neon-cyan/5 rounded-full blur-3xl animate-pulse-slow" />
        <div className="absolute bottom-1/4 right-1/4 w-80 h-80 bg-neon-purple/5 rounded-full blur-3xl animate-pulse-slow" style={{animationDelay:'1.5s'}} />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-cyber-500/3 rounded-full blur-3xl" />
      </div>

      {/* Grid lines */}
      <div className="absolute inset-0 bg-cyber-pattern opacity-30 pointer-events-none" />

      <div className="relative z-10 text-center max-w-4xl mx-auto">
        {/* Badge */}
        <div className="inline-flex items-center gap-2 bg-neon-cyan/10 border border-neon-cyan/30 rounded-full px-4 py-2 text-xs text-neon-cyan mb-8 animate-pulse-slow">
          <span className="w-2 h-2 rounded-full bg-neon-cyan animate-ping" />
          Phase 1 · Voice Cloning Platform · Now Live
        </div>

        {/* Main heading */}
        <h1 className="text-5xl sm:text-6xl md:text-7xl lg:text-8xl font-black tracking-tight mb-6 leading-none" style={{fontFamily:'Orbitron,monospace'}}>
          <span className="block text-white">INDIC</span>
          <span className="block bg-gradient-to-r from-neon-cyan via-cyber-400 to-neon-purple bg-clip-text text-transparent">
            VOICE AI
          </span>
        </h1>

        {/* Subtitle */}
        <p className="text-lg sm:text-xl md:text-2xl text-slate-300 mb-4 font-light">
          AI-Powered Voice Cloning for
        </p>
        <div className="flex flex-wrap justify-center gap-3 mb-10">
          {['తెలుగు Telugu', 'தமிழ் Tamil', 'हिन्दी Hindi', 'English'].map((lang, i) => (
            <span key={i} className="px-4 py-1.5 bg-dark-700 border border-neon-cyan/20 rounded-full text-sm text-neon-cyan font-medium hover:border-neon-cyan/60 transition-all cursor-default">
              {lang}
            </span>
          ))}
        </div>

        {/* CTA */}
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <a
            href="#generator"
            className="group relative px-8 py-4 bg-gradient-to-r from-neon-cyan to-cyber-500 text-dark-900 font-bold text-sm rounded-lg hover:scale-105 transition-all duration-200 shadow-lg shadow-neon-cyan/25"
          >
            <span style={{fontFamily:'Orbitron,monospace'}}>START CLONING</span>
            <span className="ml-2 group-hover:translate-x-1 inline-block transition-transform">→</span>
          </a>
          <a
            href="#docs"
            className="px-8 py-4 border border-neon-cyan/30 text-neon-cyan font-medium text-sm rounded-lg hover:bg-neon-cyan/10 transition-all duration-200"
          >
            View API Docs
          </a>
        </div>

        {/* Stats bar */}
        <div className="mt-16 grid grid-cols-2 md:grid-cols-4 gap-4 max-w-2xl mx-auto">
          {[
            { label: 'Languages', value: '4' },
            { label: 'Sample Length', value: '30s' },
            { label: 'Output Format', value: 'WAV' },
            { label: 'API Latency', value: '<2s' },
          ].map((s, i) => (
            <div key={i} className="bg-dark-800/60 border border-neon-cyan/10 rounded-lg p-4 text-center hover:border-neon-cyan/30 transition-all">
              <div className="text-2xl font-bold text-neon-cyan" style={{fontFamily:'Orbitron,monospace'}}>{s.value}</div>
              <div className="text-xs text-slate-500 mt-1">{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
