import React, { useState, useRef, useCallback } from 'react'
import AudioPlayer from './AudioPlayer.jsx'

const LANGUAGES = [
  { code: 'te', label: 'Telugu', native: 'తెలుగు', flag: '🇮🇳' },
  { code: 'ta', label: 'Tamil',  native: 'தமிழ்',  flag: '🇮🇳' },
  { code: 'hi', label: 'Hindi',  native: 'हिन्दी', flag: '🇮🇳' },
  { code: 'en', label: 'English',native: 'English', flag: '🌐' },
]

// ── Phase 4B: synthesis modes ──────────────────────────────────────────────
const MODES = [
  {
    id:    'standard',
    label: 'Standard TTS',
    icon:  '🔊',
    desc:  'Neural text-to-speech via EdgeTTS. Fast (~3 s). No voice cloning.',
  },
  {
    id:    'clone',
    label: 'Voice Clone',
    icon:  '🧬',
    desc:  "OpenVoice v2 — transfers your voice's tone colour onto the synthesis. Requires GPU backend.",
  },
]

const ACCEPTED_TYPES = ['audio/wav','audio/mpeg','audio/mp3','audio/ogg','audio/webm','audio/flac','audio/x-wav','audio/x-flac']
const ACCEPTED_EXT   = /\.(wav|mp3|ogg|webm|flac)$/i
const MAX_SIZE_MB    = 50
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
const POLL_INTERVAL  = 2000   // ms between status polls
const POLL_TIMEOUT   = 120000 // ms before giving up (2 min)

function validateFile(file) {
  if (!file) return 'No file selected.'
  if (file.size === 0) return 'File is empty.'
  if (file.size > MAX_SIZE_BYTES)
    return `File too large. Max size is ${MAX_SIZE_MB} MB (got ${(file.size / 1024 / 1024).toFixed(1)} MB).`
  const typeOk = ACCEPTED_TYPES.includes(file.type) || ACCEPTED_EXT.test(file.name)
  if (!typeOk)
    return `Unsupported format "${file.name}". Please upload WAV, MP3, OGG, WEBM, or FLAC.`
  return null
}

// ── API base resolution ────────────────────────────────────────────────────
// In production:  VITE_WORKERS_URL  -> CF Workers gateway (preferred)
// Fallback:       VITE_API_BASE     -> direct backend or Vite proxy
// Dev:            empty string      -> Vite proxy (vite.config.js)
const WORKERS_URL = (import.meta.env.VITE_WORKERS_URL ?? '').replace(/\/+$/, '')
const API_BASE    = (import.meta.env.VITE_API_BASE    ?? '').replace(/\/+$/, '')
const BASE_URL    = WORKERS_URL || API_BASE  // prefer Workers URL

// API key for Workers gateway auth (Phase 7 / Phase 8)
// Set VITE_WORKER_API_KEY in your .env or CF Pages environment variables.
const WORKER_API_KEY = import.meta.env.VITE_WORKER_API_KEY ?? ''

/** Build fetch headers — always include X-Api-Key when key is configured */
function apiHeaders(extra = {}) {
  const headers = { ...extra }
  if (WORKER_API_KEY) {
    headers['X-Api-Key'] = WORKER_API_KEY
  }
  return headers
}

// ── Async job polling helper ───────────────────────────────────────────────
async function pollJobUntilDone(jobId, onProgress) {
  const deadline = Date.now() + POLL_TIMEOUT
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, POLL_INTERVAL))
    const res  = await fetch(`${BASE_URL}/job/${jobId}`, {
      headers: apiHeaders(),
    })
    if (!res.ok) throw new Error(`Poll error ${res.status}`)
    const data = await res.json()
    onProgress(data.status)
    if (data.status === 'completed') return data
    if (data.status === 'failed')
      throw new Error(data.error_message || 'Job failed on the server.')
  }
  throw new Error('Timed out waiting for synthesis to complete.')
}

export default function VoiceCloner() {
  const [audioFile, setAudioFile]   = useState(null)
  const [text, setText]             = useState('')
  const [language, setLanguage]     = useState('te')
  const [mode, setMode]             = useState('standard')       // Phase 4B
  const [status, setStatus]         = useState('idle')           // idle | loading | polling | success | error
  const [pollStatus, setPollStatus] = useState('')               // queued | processing | completed | failed
  const [outputUrl, setOutputUrl]   = useState(null)
  const [jobMeta, setJobMeta]       = useState(null)             // { job_id, mode, cloning_enabled }
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
    setJobMeta(null)
    setStatus('idle')
  }, [])

  // ── Drag-and-drop ──────────────────────────────────────────────────────
  const onDragEnter = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true) }
  const onDragOver  = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true) }
  const onDragLeave = (e) => { e.preventDefault(); e.stopPropagation(); setDragOver(false) }
  const onDrop      = (e) => {
    e.preventDefault(); e.stopPropagation(); setDragOver(false)
    applyFile(e.dataTransfer.files?.[0])
  }
  const onFileChange = (e) => applyFile(e.target.files?.[0])

  // ── Generate (Phase 4B: async job + polling) ───────────────────────────
  const handleGenerate = async () => {
    if (!audioFile) { setErrorMsg('Please upload a voice sample first.'); return }
    if (!text.trim()) { setErrorMsg('Please enter some text to synthesize.'); return }

    setStatus('loading')
    setErrorMsg('')
    setOutputUrl(null)
    setJobMeta(null)
    setPollStatus('')

    const form = new FormData()
    form.append('audio',    audioFile)
    form.append('text',     text.trim())
    form.append('language', language)
    form.append('mode',     mode)

    try {
      // Step 1 — submit job (expect 202)
      // Do NOT set Content-Type — browser must set it with correct multipart boundary
      const res = await fetch(`${BASE_URL}/generate`, {
        method:  'POST',
        headers: apiHeaders(), // X-Api-Key only; no Content-Type override
        body:    form,
      })

      if (!res.ok) {
        let detail = `Server error ${res.status}`
        try { const d = await res.json(); detail = d.detail ?? d.error ?? detail } catch (_) {}
        throw new Error(detail)
      }

      const jobData = await res.json()
      setJobMeta({
        job_id:          jobData.job_id,
        mode:            jobData.mode,
        cloning_enabled: jobData.cloning_enabled,
      })
      setStatus('polling')
      setPollStatus('queued')

      // Step 2 — poll until done
      const completed = await pollJobUntilDone(jobData.job_id, (s) => setPollStatus(s))

      if (!completed.output_url) throw new Error('Job completed but no output URL returned.')

      // Step 3 — fetch WAV and create blob URL
      const outputHref = completed.output_url.startsWith('http')
        ? completed.output_url
        : `${BASE_URL}${completed.output_url}`
      const wavRes  = await fetch(outputHref, { headers: apiHeaders() })
      if (!wavRes.ok) throw new Error(`Could not fetch output audio (${wavRes.status}).`)
      const blob    = await wavRes.blob()
      if (blob.size === 0) throw new Error('Server returned an empty audio file.')

      setOutputUrl(URL.createObjectURL(blob))
      setStatus('success')
      setPollStatus('completed')

    } catch (err) {
      const target = WORKERS_URL || 'port 8000'
      const msg = err.message === 'Failed to fetch'
        ? `Cannot reach the backend. Make sure the server is running at ${target}.`
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
    setJobMeta(null)
    setStatus('idle')
    setPollStatus('')
    setErrorMsg('')
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  // ── Status badge helpers ───────────────────────────────────────────────
  const isLoading = status === 'loading' || status === 'polling'

  const pollLabel = {
    queued:     'Queued…',
    processing: 'Synthesising…',
    completed:  'Done ✓',
    failed:     'Failed',
    '':         'Submitting…',
  }[pollStatus] ?? pollStatus

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">

      {/* ── Mode Toggle (Phase 4B) ── */}
      <div>
        <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
          Synthesis Mode
        </label>
        <div className="grid grid-cols-2 gap-3">
          {MODES.map((m) => (
            <button
              key={m.id}
              onClick={() => setMode(m.id)}
              disabled={isLoading}
              className={[
                'relative p-3 rounded-lg border text-left transition-all duration-200',
                mode === m.id
                  ? 'border-neon-cyan bg-neon-cyan/10 shadow-[0_0_12px_rgba(0,255,255,0.15)]'
                  : 'border-slate-700 hover:border-slate-500 bg-transparent',
                isLoading ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
              ].join(' ')}
            >
              {mode === m.id && (
                <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-neon-cyan shadow-[0_0_6px_rgba(0,255,255,0.8)]" />
              )}
              <div className="text-xl mb-1">{m.icon}</div>
              <div className="text-xs font-semibold text-slate-200">{m.label}</div>
              <div className="text-xs text-slate-500 mt-0.5 leading-snug">{m.desc}</div>
            </button>
          ))}
        </div>
        {mode === 'clone' && (
          <p className="mt-2 text-xs text-amber-400/80 leading-relaxed">
            ⚠️ Voice cloning requires{' '}
            <code className="text-amber-300">OPENVOICE_ENABLED=true</code> and GPU
            checkpoints installed on the backend. Falls back to Standard TTS if unavailable.
          </p>
        )}
      </div>

      {/* ── Upload Zone ── */}
      <div>
        <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
          Voice Sample
          {mode === 'clone' && (
            <span className="ml-2 text-neon-cyan/70 normal-case">
              (used as clone reference)
            </span>
          )}
        </label>
        <div className="gradient-border rounded-xl">
          <div className="gradient-border-inner p-1 rounded-xl">
            <div
              onDragEnter={onDragEnter}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onClick={() => !isLoading && fileInputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => !isLoading && e.key === 'Enter' && fileInputRef.current?.click()}
              aria-label="Upload voice sample audio file"
              className={[
                'relative cursor-pointer rounded-lg p-8 text-center border-2 border-dashed',
                'transition-all duration-200 select-none outline-none',
                'focus-visible:ring-2 focus-visible:ring-neon-cyan/50',
                isLoading ? 'opacity-50 cursor-not-allowed' : '',
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
                disabled={isLoading}
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
              onClick={() => !isLoading && setLanguage(lang.code)}
              disabled={isLoading}
              className={[
                'relative p-3 rounded-lg border text-left transition-all duration-200',
                language === lang.code
                  ? 'border-neon-cyan bg-neon-cyan/10 shadow-[0_0_12px_rgba(0,255,255,0.15)]'
                  : 'border-slate-700 hover:border-slate-500 bg-transparent',
                isLoading ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
              ].join(' ')}
            >
              {language === lang.code && (
                <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-neon-cyan shadow-[0_0_6px_rgba(0,255,255,0.8)]" />
              )}
              <div className="text-xl mb-1">{lang.flag}</div>
              <div className="text-xs font-semibold text-slate-200">{lang.label}</div>
              <div className="text-xs text-slate-500">{lang.native}</div>
            </button>
          ))}
        </div>
      </div>

      {/* ── Text Input ── */}
      <div>
        <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
          Text to Synthesise
        </label>
        <div className="gradient-border rounded-xl">
          <div className="gradient-border-inner rounded-xl">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={isLoading}
              placeholder="Enter text here…"
              rows={4}
              maxLength={500}
              className="w-full bg-transparent text-slate-200 placeholder-slate-600 p-4 rounded-xl resize-none focus:outline-none focus:ring-1 focus:ring-neon-cyan/30 text-sm leading-relaxed disabled:opacity-50"
            />
          </div>
        </div>
        <div className="flex justify-between mt-1">
          <span className="text-xs text-slate-600">Max 500 characters</span>
          <span className={`text-xs ${text.length > 450 ? 'text-amber-400' : 'text-slate-500'}`}>
            {text.length} / 500
          </span>
        </div>
      </div>

      {/* ── Error message ── */}
      {errorMsg && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30">
          <span className="text-red-400 text-sm">⚠️</span>
          <p className="text-red-400 text-sm leading-relaxed">{errorMsg}</p>
        </div>
      )}

      {/* ── Job polling status (Phase 4B) ── */}
      {status === 'polling' && (
        <div className="flex items-center gap-3 p-3 rounded-lg bg-neon-cyan/5 border border-neon-cyan/20">
          <div className="w-4 h-4 border-2 border-neon-cyan border-t-transparent rounded-full animate-spin flex-shrink-0" />
          <div className="text-sm text-slate-300">
            <span className="text-neon-cyan font-medium">{pollLabel}</span>
            {jobMeta?.mode === 'clone' && (
              <span className="ml-2 text-slate-500">
                · {jobMeta.cloning_enabled ? 'OpenVoice v2 cloning' : 'EdgeTTS (cloning unavailable)'}
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── Generate button ── */}
      <button
        onClick={handleGenerate}
        disabled={isLoading || !audioFile || !text.trim()}
        className={[
          'w-full py-4 rounded-xl font-semibold text-sm tracking-wide transition-all duration-300',
          'relative overflow-hidden',
          isLoading || !audioFile || !text.trim()
            ? 'opacity-40 cursor-not-allowed bg-slate-800 text-slate-500 border border-slate-700'
            : mode === 'clone'
              ? 'bg-gradient-to-r from-neon-cyan/20 via-violet-500/20 to-neon-cyan/20 border border-neon-cyan/40 text-neon-cyan hover:border-neon-cyan/70 hover:shadow-[0_0_20px_rgba(0,255,255,0.2)]'
              : 'bg-gradient-to-r from-neon-cyan/10 to-neon-cyan/5 border border-neon-cyan/30 text-neon-cyan hover:border-neon-cyan/60 hover:shadow-[0_0_16px_rgba(0,255,255,0.15)]',
        ].join(' ')}
      >
        {isLoading ? (
          <span className="flex items-center justify-center gap-2">
            <span className="w-4 h-4 border-2 border-neon-cyan border-t-transparent rounded-full animate-spin" />
            {status === 'polling' ? pollLabel : 'Submitting…'}
          </span>
        ) : mode === 'clone' ? (
          '🧬 Clone & Generate'
        ) : (
          '▶ Generate Voice'
        )}
      </button>

      {/* ── Output ── */}
      {status === 'success' && outputUrl && (
        <div className="space-y-4">
          {/* Mode badge */}
          {jobMeta && (
            <div className="flex items-center gap-2 text-xs text-slate-400">
              {jobMeta.mode === 'clone' ? (
                jobMeta.cloning_enabled ? (
                  <span className="px-2 py-0.5 rounded-full bg-neon-cyan/10 border border-neon-cyan/30 text-neon-cyan">
                    🧬 Voice cloned
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded-full bg-amber-400/10 border border-amber-400/30 text-amber-400">
                    🔊 Standard TTS (cloning unavailable)
                  </span>
                )
              ) : (
                <span className="px-2 py-0.5 rounded-full bg-slate-700 border border-slate-600 text-slate-400">
                  🔊 Standard TTS
                </span>
              )}
              {jobMeta.job_id && (
                <span className="text-slate-600 font-mono truncate">
                  {jobMeta.job_id.slice(0, 8)}…
                </span>
              )}
            </div>
          )}

          {/* Player */}
          <div>
            <label className="block text-xs text-slate-400 uppercase tracking-widest mb-3 font-medium">
              Generated Audio
            </label>
            <AudioPlayer src={outputUrl} />
          </div>

          {/* Download */}
          <a
            href={outputUrl}
            download="indicvoice_output.wav"
            className="flex items-center justify-center gap-2 w-full py-3 rounded-xl border border-neon-green/30 text-neon-green text-sm font-medium hover:bg-neon-green/5 hover:border-neon-green/60 transition-all duration-200"
          >
            ⬇ Download WAV
          </a>

          {/* Reset */}
          <button
            onClick={reset}
            className="w-full py-3 rounded-xl border border-slate-700 text-slate-400 text-sm hover:border-slate-500 hover:text-slate-300 transition-all duration-200"
          >
            ↺ Start Over
          </button>
        </div>
      )}
    </div>
  )
}
