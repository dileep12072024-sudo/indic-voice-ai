import React, { useState } from 'react'
import Hero from './components/Hero.jsx'
import VoiceCloner from './components/VoiceCloner.jsx'

export default function App() {
  return (
    <div className="min-h-screen bg-dark-900 bg-cyber-pattern text-slate-100">
      <div className="scanline" />
      {/* Nav */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-dark-900/80 backdrop-blur-md border-b border-neon-cyan/20">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-neon-cyan to-neon-purple flex items-center justify-center text-dark-900 font-bold text-sm">
              IV
            </div>
            <span className="font-bold text-lg tracking-wider text-neon-cyan" style={{fontFamily:'Orbitron,monospace'}}>
              INDIC<span className="text-neon-purple">VOICE</span> AI
            </span>
          </div>
          <div className="hidden md:flex items-center gap-6 text-sm text-slate-400">
            <a href="#features" className="hover:text-neon-cyan transition-colors">Features</a>
            <a href="#generator" className="hover:text-neon-cyan transition-colors">Generator</a>
            <a href="#docs" className="hover:text-neon-cyan transition-colors">Docs</a>
          </div>
          <div className="flex items-center gap-2">
            <span className="inline-flex items-center gap-1.5 text-xs text-neon-green bg-neon-green/10 border border-neon-green/30 rounded-full px-3 py-1">
              <span className="w-1.5 h-1.5 rounded-full bg-neon-green animate-pulse" />
              API Online
            </span>
          </div>
        </div>
      </nav>

      {/* Main */}
      <main className="pt-16">
        <Hero />
        <section id="generator" className="py-20 px-4">
          <div className="max-w-4xl mx-auto">
            <div className="text-center mb-12">
              <h2 className="text-3xl md:text-4xl font-bold mb-4 text-glow-cyan" style={{fontFamily:'Orbitron,monospace'}}>
                VOICE <span className="text-neon-purple">GENERATOR</span>
              </h2>
              <p className="text-slate-400 text-sm md:text-base max-w-xl mx-auto">
                Upload a 30-second voice sample, enter your text, select a language, and generate a cloned voice output.
              </p>
            </div>
            <VoiceCloner />
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-neon-cyan/10 py-8 px-4 text-center text-slate-500 text-xs">
        <p>© 2024 IndicVoice AI — Empowering Indic Languages with AI Voice Cloning</p>
        <p className="mt-1 text-neon-cyan/40">Telugu · Tamil · Hindi · English</p>
      </footer>
    </div>
  )
}
