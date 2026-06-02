import React, { useState, useRef, useCallback } from 'react'
import AudioPlayer from './AudioPlayer.jsx'

const LANGUAGES = [
  { code: 'te', label: 'Telugu', native: 'తెలుగు', flag: '🇮🇳' },
  { code: 'ta', label: 'Tamil',  native: 'தமிழ்',  flag: '🇮🇳' },
  { code: 'hi', label: 'Hindi',  native: 'हिन्दी', flag: '🇮🇳' },
  { code: 'en', label: 'English',native: 'English', flag: '🌐' },
]

const ACCEPTED_TYPES = ['audio/wav','audio/mpeg','audio/mp3','audio/ogg','audio/webm','audio/flac','audio/x-wav','audio/x-flac']
const ACCEPTED_EXT   = /\.(wav|mp3|ogg|webm|flac)$/i
const MAX_SIZE_MB    = 50
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024

function validateFile(file) {
  if (!file) return 'No file selected.'
  if (file.size === 0) return 'File is empty.'
  if (file.size > MAX_SIZE_BYTES) return `File too large. Max size is ${MAX_SIZE_MB} MB (got ${(file.size/1024/1024).toFixed(1)} MB).`
  const typeOk = ACCEPTED_TYPES.includes(file.type) || ACCEPTED_EXT.test(file.name)
  if (!typeOk) return `Unsupported format "${file.name}". Please upload WAV, MP3, OGG, WEBM, or FLAC.`
  return null
}

// Resolve API base: use Vite proxy in dev, or VITE_API_BASE in production
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export default function VoiceCloner() {
  const [audioFile, setAudioFile]   = useState(null)
  const [text, setText]             = useState('')
  const [language, setLanguage]     = useState('te')
  const [status, setStatus]         = useState('idle') // idle | loading | success | error
  const [outputUrl, setOutputUrl]   = useState(null)
  const [errorMsg, setErrorMsg]     = useState('')
  const [dragOver, setDragOver]     = useState(false)
  const fileInputRef                = useRef(null)

  const applyFile = useCallback((file) => {
    if (!file) return
    const err = validateFile(file)
    if (err) { setErrorMsg(err); return }
    setErrorMsg('')
    setAudioFile(file)
    setOutputUrl(null)
    setStatus('idle')
  }, [])

  // ── Drag-and-drop handlers ──────────────────────────────────────────────
  const onDragEnter = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true) }
  const onDragOver  = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true) }
  const onDragLeave = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(false) }
  const onDrop      = (e) => {
    e.preventDefault(); e.stopPropagation(); setDragOver(false)
    const file = e.dataTransfer.files?.[0]
    applyFile(file)
  }

  const onFileChange = (e) => applyFile(e.target.files?.[0])

  // ── Generate ────────────────────────────────────────────────────────────
  const handleGenerate = async () => {
    if (!audioFile) { setErrorMsg('Please upload a voice sample first.'); return }
    if (!text.trim()) { setErrorMsg('Please enter some text to synthesize.'); return }

    setStatus('loading')
    setErrorMsg('')
    setOutputUrl(null)

    const form = new FormData()
    form.append('audio', audioFile)
    form.append('text', text.trim())
    form.append('language', language)

    try {
      const res = await fetch(`${API_BASE}/generate`, { method: 'POST', body: form })

      if (!res.ok) {
        let detail = `Server error ${res.status}`
        try {
          const data = await res.json()
          detail = data.detail ?? detail
        } catch (_) { /* non-JSON body */ }
        throw new Error(detail)
      }

      const blob = await res.blob()
      if (blob.size === 0) throw new Error('Server returned an empty audio file.')

      const url = URL.createObjectURL(blob)
      setOutputUrl(url)
      setStatus('success')
    } catch (err) {
      // Network errors (backend not running) give a specific message
      const msg = err.message === 'Failed to fetch'
        ? 'Cannot reach the backend. Make sure FastAPI is running on port 8000.'
        : err.message
      setErrorMsg(msg)
      setStatus('error')
    }
  }

  const reset = () => {
    if (outputUrl) URL.revokeObjectURL(outputUrl)
    setAudioFile(null)
    setText('')
    setOutputUrl(null)
    setStatus('idle')
    setErrorMsg('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // ── Render ──────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* ── Upload Zone ── */}
      <div>
        <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
          Voice Sample
        </label>
        <div className="gradient-border rounded-xl">
          <div className="gradient-border-inner p-1 rounded-xl">
            <div
              onDragEnter={onDragEnter}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === 'Enter' && fileInputRef.current?.click()}
              aria-label="Upload voice sample audio file"
              className={[
                'relative cursor-pointer rounded-lg p-8 text-center border-2 border-dashed',
                'transition-all duration-200 select-none outline-none',
                'focus-visible:ring-2 focus-visible:ring-neon-cyan/50',
                dragOver
                  ? 'border-neon-cyan bg-neon-cyan/10 scale-[1.01]'
                  : audioFile
                    ? 'border-neon-green/50 bg-neon-green/5 hover:border-neon-green/70'
                    : 'border-slate-600 hover:border-neon-cyan/50 hover:bg-neon-cyan/5',
              ].join(' ')}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept=".wav,.mp3,.ogg,.webm,.flac,audio/*"
                onChange={onFileChange}
              />

              {dragOver ? (
                <div className="space-y-2 pointer-events-none">
                  <div className="text-5xl">⬇️</div>
                  <p className="text-neon-cyan font-semibold">Drop to upload</p>
                </div>
              ) : audioFile ? (
                <div className="space-y-2">
                  <div className="text-4xl">🎙️</div>
                  <p className="text-neon-green font-semibold text-sm truncate max-w-xs mx-auto">
                    {audioFile.name}
                  </p>
                  <p className="text-slate-500 text-xs">
                    {(audioFile.size / 1024 / 1024).toFixed(2)} MB
                    &nbsp;·&nbsp;
                    Click or drop to replace
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="text-5xl opacity-40">🎤</div>
                  <p className="text-slate-300 font-medium">
                    Drag &amp; drop your voice sample here
                  </p>
                  <p className="text-slate-500 text-xs">
                    WAV · MP3 · OGG · WEBM · FLAC &nbsp;—&nbsp; max {MAX_SIZE_MB} MB
                  </p>
                  <span className="inline-block mt-2 px-4 py-2 text-xs border border-neon-cyan/30 text-neon-cyan rounded-full hover:bg-neon-cyan/10 transition-colors">
                    Browse Files
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Language Selector ── */}
      <div>
        <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
          Target Language
        </label>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {LANGUAGES.map((lang) => (
            <button
              key={lang.code}
              onClick={() => setLanguage(lang.code)}
              className={[
                'relative p-3 rounded-lg border text-left transition-all duration-200',
                language === lang.code
                  ? 'border-neon-cyan bg-neon-cyan/10 text-neon-cyan'
                  : 'border-slate-700 hover:border-neon-cyan/40 text-slate-400 hover:text-slate-200',
              ].join(' ')}
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

      {/* ── Text Input ── */}
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
            className={[
              'w-full bg-dark-700 border rounded-lg px-4 py-3 text-slate-100 text-sm',
              'placeholder-slate-500 resize-none font-mono',
              'focus:outline-none focus:ring-1 focus:ring-neon-cyan/20 transition-all',
              text.length > 450 ? 'border-yellow-500/60 focus:border-yellow-500' : 'border-slate-600 focus:border-neon-cyan/60',
            ].join(' ')}
          />
          <span className={[
            'absolute bottom-3 right-3 text-xs font-mono',
            text.length > 450 ? 'text-yellow-500' : 'text-slate-600',
          ].join(' ')}>
            {text.length}/500
          </span>
        </div>
      </div>

      {/* ── Error Banner ── */}
      {errorMsg && (
        <div
          role="alert"
          className="flex items-start gap-3 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm"
        >
          <span className="text-base leading-none mt-0.5 flex-shrink-0">⚠️</span>
          <span>{errorMsg}</span>
        </div>
      )}

      {/* ── Generate Button ── */}
      <button
        onClick={handleGenerate}
        disabled={status === 'loading'}
        className={[
          'w-full py-4 rounded-lg font-bold text-sm tracking-widest transition-all duration-200',
          status === 'loading'
            ? 'bg-slate-700 text-slate-500 cursor-not-allowed'
            : 'bg-gradient-to-r from-neon-cyan to-neon-purple text-dark-900',
          status !== 'loading' && 'hover:scale-[1.02] hover:shadow-lg hover:shadow-neon-cyan/25 active:scale-[0.98]',
        ].join(' ')}
        style={{ fontFamily: 'Orbitron, monospace' }}
      >
        {status === 'loading' ? (
          <span className="flex items-center justify-center gap-3">
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
            </svg>
            GENERATING VOICE…
          </span>
        ) : (
          'GENERATE VOICE'
        )}
      </button>

      {/* ── Success Output ── */}
      {status === 'success' && outputUrl && (
        <div className="space-y-4">
          <div className="h-px bg-gradient-to-r from-transparent via-neon-cyan/30 to-transparent" />
          <p className="text-xs text-neon-green uppercase tracking-widest text-center font-medium">
            ✓ Voice Generated Successfully
          </p>
          <AudioPlayer
            src={outputUrl}
            filename={`indicvoice_${language}_output.wav`}
          />
          <button
            onClick={reset}
            className="w-full py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors border border-slate-700 rounded-lg hover:border-slate-500"
          >
            Reset &amp; Start Over
          </button>
        </div>
      )}
    </div>
  )
}
