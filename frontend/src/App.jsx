import React from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Navbar from './components/Navbar.jsx'
import VoiceLibrary from './pages/VoiceLibrary.jsx'
import CreateVoice from './pages/CreateVoice.jsx'
import GenerateSpeech from './pages/GenerateSpeech.jsx'

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      <Navbar />
      <main className="flex-1 pt-16">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 py-10">
          <Routes>
            <Route path="/"         element={<VoiceLibrary />} />
            <Route path="/create"   element={<CreateVoice />} />
            <Route path="/generate" element={<GenerateSpeech />} />
            <Route path="*"         element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </main>

      <footer className="border-t border-slate-200 py-5 px-4 text-center text-xs text-slate-400">
        © 2024 IndicVoice AI · Telugu · Tamil · Hindi · English
      </footer>
    </div>
  )
}
