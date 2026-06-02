import React, { useState, useRef } from 'react'
import AudioPlayer from './AudioPlayer.jsx'

const LANGUAGES = [
  { code: 'te', label: 'Telugu', native: 'తెలుగు', flag: '🇮🇳' },
  { code: 'ta', label: 'Tamil',  native: 'தமிழ்',  flag: '🇮🇳' },
  { code: 'hi', label: 'Hindi',  native: 'हिन्दी', flag: '🇮🇳' },
  { code: 'en', label: 'English',native: 'English', flag: '🌐' },
]

export default function VoiceCloner() {
  const [audioFile, setAudioFile]     = useState(null)
  const [text, setText]               = useState('')
  const [language, setLanguage]       = useState('te')
  const [status, setStatus]           = useState('idle') // idle | loading | success | error
  const [outputUrl, setOutputUrl]     = useState(null)
  const [errorMsg, setErrorMsg]       = useState('')
  const [dragOver, setDragOver]       = useState(false)
  const fileInputRef                  = useRef(null)

  const handleFile = (file) => {
    if (!file) return
    const valid = ['audio/wav','audio/mpeg','audio/mp3','audio/ogg','audio/webm','audio/flac']
    if (!valid.includes(file.type) && !file.name.match(/\.(wav|mp3|ogg|webm|flac)$/i)) {
      setErrorMsg('Please upload a WAV, MP3, OGG, WEBM, or FLAC audio file.')
      return
    }
    setErrorMsg('')
    setAudioFile(file)
    setOutputUrl(null)
  }

  const handleDrop = (e) => {
    e.preventDefault(); setDragOver(false)
    handleFile(e.dataTransfer.files[0])
  }

  const handleGenerate = async () => {
    if (!audioFile) { setErrorMsg('Upload a voice sample first.'); return }
    if (!text.trim()) { setErrorMsg('Enter some text to synthesize.'); return }

    setStatus('loading'); setErrorMsg('')
    const form = new FormData()
    form.append('audio', audioFile)
    form.append('text', text)
    form.append('language', language)

    try {
      const res = await fetch('/api/generate', { method: 'POST', body: form })
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'Generation failed') }
      const blob = await res.blob()
      setOutputUrl(URL.createObjectURL(blob))
      setStatus('success')
    } catch (err) {
      setErrorMsg(err.message)
      setStatus('error')
    }
  }

  const reset = () => {
    setAudioFile(null); setText(''); setOutputUrl(null)
    setStatus('idle'); setErrorMsg('')
  }

  return (
    <div className="space-y-6">
      {/* Upload Zone */}
      <div className="gradient-border rounded-xl">
        <div className="gradient-border-inner p-1 rounded-xl">
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`relative cursor-pointer rounded-lg p-8 text-center border-2 border-dashed transition-all duration-200
              ${dragOver ? 'border-neon-cyan bg-neon-cyan/10' : 'border-slate-600 hover:border-neon-cyan/50 hover:bg-neon-cyan/5'}
              ${audioFile ? 'border-neon-green/50 bg-neon-green/5' : ''}`}
          >
            <input
              ref={fileInputRef} type="file" className="hidden"
              accept=".wav,.mp3,.ogg,.webm,.flac,audio/*"
              onChange={(e) => handleFile(e.target.files[0])}
            />
            {audioFile ? (
              <div className="space-y-2">
                <div className="text-4xl">🎙️</div>
                <p className="text-neon-green font-semibold text-sm">{audioFile.name}</p>
                <p className="text-slate-500 text-xs">
                  {(audioFile.size / 1024 / 1024).toFixed(2)} MB · Click to change
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="text-5xl opacity-40">🎤</div>
                <p className="text-slate-300 font-medium">Drop your voice sample here</p>
                <p className="text-slate-500 text-xs">WAV · MP3 · OGG · WEBM · FLAC — min 10s recommended 30s</p>
                <span className="inline-block mt-2 px-4 py-2 text-xs border border-neon-cyan/30 text-neon-cyan rounded-full hover:bg-neon-cyan/10">
                  Browse Files
                </span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Language Selector */}
      <div>
        <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
          Target Language
        </label>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {LANGUAGES.map((lang) => (
            <button
              key={lang.code}
              onClick={() => setLanguage(lang.code)}
              className={`relative p-3 rounded-lg border text-left transition-all duration-200 group
                ${language === lang.code
                  ? 'border-neon-cyan bg-neon-cyan/10 text-neon-cyan'
                  : 'border-slate-700 hover:border-neon-cyan/40 text-slate-400 hover:text-slate-200'}`}
            >
              {language === lang.code && (
                <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-neon-cyan animate-pulse" />
              )}
              <div className="text-xl mb-1">{lang.flag}</div>
              <div className="text-sm font-semibold">{lang.label}</div>
              <div className="text-xs opacity-60">{lang.native}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Text Input */}
      <div>
        <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
          Text to Synthesize
        </label>
        <div className="relative">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={4}
            maxLength={500}
            placeholder="Enter the text you want to synthesize in the selected language…"
            className="w-full bg-dark-700 border border-slate-600 rounded-lg px-4 py-3 text-slate-100 text-sm
              placeholder-slate-500 resize-none focus:outline-none focus:border-neon-cyan/60 focus:ring-1
              focus:ring-neon-cyan/20 transition-all font-mono"
          />
          <span className="absolute bottom-3 right-3 text-xs text-slate-600">{text.length}/500</span>
        </div>
      </div>

      {/* Error */}
      {errorMsg && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm">
          <span>⚠️</span> {errorMsg}
        </div>
      )}

      {/* Generate Button */}
      <button
        onClick={handleGenerate}
        disabled={status === 'loading'}
        className={`w-full py-4 rounded-lg font-bold text-sm tracking-widest transition-all duration-200
          ${status === 'loading'
            ? 'bg-slate-700 text-slate-500 cursor-not-allowed'
            : 'bg-gradient-to-r from-neon-cyan to-neon-purple text-dark-900 hover:scale-[1.02] hover:shadow-lg hover:shadow-neon-cyan/25 active:scale-[0.98]'}`}
        style={{fontFamily:'Orbitron,monospace'}}
      >
        {status === 'loading' ? (
          <span className="flex items-center justify-center gap-3">
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
            </svg>
            GENERATING VOICE…
          </span>
        ) : 'GENERATE VOICE'}
      </button>

      {/* Output */}
      {status === 'success' && outputUrl && (
        <div className="space-y-4">
          <div className="h-px bg-gradient-to-r from-transparent via-neon-cyan/30 to-transparent" />
          <p className="text-xs text-neon-green uppercase tracking-widest text-center font-medium">
            ✓ Voice Generated Successfully
          </p>
          <AudioPlayer src={outputUrl} filename={`indicvoice_${language}_output.wav`} />
          <button
            onClick={reset}
            className="w-full py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors border border-slate-700 rounded-lg hover:border-slate-500"
          >
            Reset & Start Over
          </button>
        </div>
      )}
    </div>
  )
}
