import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import AudioPlayer from '../components/AudioPlayer.jsx'

const API = 'http://localhost:8000'
const POLL_MS = 2000

const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'te', label: 'Telugu' },
  { code: 'ta', label: 'Tamil' },
  { code: 'hi', label: 'Hindi' },
]
const MAX_CHARS = 2000

const LANG_LABELS = { te: 'Telugu', ta: 'Tamil', hi: 'Hindi', en: 'English' }

function StatusStep({ step, current }) {
  const done    = current > step
  const active  = current === step
  const labels  = ['Submitting', 'Processing', 'Completed']
  return (
    <div className={`flex items-center gap-2 text-sm ${
      done ? 'text-emerald-600' : active ? 'text-indigo-600' : 'text-slate-400'
    }`}>
      <div className={`w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 border-2 ${
        done
          ? 'bg-emerald-100 border-emerald-500'
          : active
          ? 'bg-indigo-100 border-indigo-500 animate-pulse'
          : 'bg-slate-100 border-slate-300'
      }`}>
        {done
          ? <svg className="w-3 h-3 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg>
          : <span className="text-xs font-bold">{step}</span>
        }
      </div>
      <span className={`font-medium ${active ? '' : ''}`}>{labels[step - 1]}</span>
    </div>
  )
}

export default function GenerateSpeech() {
  const [searchParams] = useSearchParams()
  const prefillVoiceId   = searchParams.get('voice_id')   || ''
  const prefillVoiceName = searchParams.get('voice_name') || ''
  const prefillLang      = searchParams.get('lang')       || 'en'

  // Form state
  const [voices,   setVoices]   = useState([])
  const [voiceId,  setVoiceId]  = useState(prefillVoiceId)
  const [text,     setText]     = useState('')
  const [language, setLanguage] = useState(prefillLang)
  const [mode,     setMode]     = useState('standard')

  // Job state
  const [jobId,     setJobId]     = useState(null)
  const [jobStatus, setJobStatus] = useState(null)  // queued|processing|completed|failed
  const [outputUrl, setOutputUrl] = useState(null)
  const [fallback,  setFallback]  = useState(false)
  const [provider,  setProvider]  = useState('')
  const [errorMsg,  setErrorMsg]  = useState(null)
  const [submitting,setSubmitting]= useState(false)

  const pollRef = useRef(null)

  // Load voice list
  useEffect(() => {
    fetch(`${API}/voices`)
      .then(r => r.json())
      .then(d => setVoices(d.voices || []))
      .catch(() => {})
  }, [])

  // Cleanup poll on unmount
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  const startPolling = useCallback((id) => {
    pollRef.current = setInterval(async () => {
      try {
        const res  = await fetch(`${API}/job/${id}`)
        const data = await res.json()
        setJobStatus(data.status)
        if (data.status === 'completed') {
          clearInterval(pollRef.current)
          setOutputUrl(data.output_url)
          setFallback(data.fallback_used  || false)
          setProvider(data.provider_name  || '')
        } else if (data.status === 'failed') {
          clearInterval(pollRef.current)
          setErrorMsg(data.error_message || 'Job failed')
        }
      } catch {
        // network hiccup — keep polling
      }
    }, POLL_MS)
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!voiceId) { setErrorMsg('Please select a voice profile.'); return }
    if (!text.trim()) { setErrorMsg('Please enter text to synthesise.'); return }

    setErrorMsg(null)
    setOutputUrl(null)
    setJobId(null)
    setJobStatus(null)
    setFallback(false)
    setProvider('')
    setSubmitting(true)

    const fd = new FormData()
    fd.append('voice_id', voiceId)
    fd.append('text',     text.trim())
    fd.append('language', language)
    fd.append('mode',     mode)

    try {
      const res = await fetch(`${API}/generate`, { method: 'POST', body: fd })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail || res.statusText)
      }
      const data = await res.json()
      setJobId(data.job_id)
      setJobStatus('queued')
      setSubmitting(false)
      startPolling(data.job_id)
    } catch (err) {
      setErrorMsg(err.message)
      setSubmitting(false)
    }
  }

  const stepIndex = jobStatus === 'completed' || jobStatus === 'failed'
    ? 3
    : jobStatus === 'processing'
    ? 2
    : jobStatus === 'queued'
    ? 1
    : 0

  const selectedVoice = voices.find(v => v.id === voiceId)

  return (
    <div className="max-w-2xl">
      {/* Header */}
      <div className="page-header">
        <h1 className="page-title">Generate Speech</h1>
        <p className="page-subtitle">Pick a saved voice, enter your text, and synthesise audio.</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5">

        {/* Voice selector */}
        <div className="card p-6 space-y-3">
          <h2 className="text-sm font-semibold text-slate-800">Voice</h2>

          {voices.length === 0 ? (
            <div className="rounded-xl bg-amber-50 border border-amber-200 px-4 py-3 flex items-center gap-3">
              <svg className="w-5 h-5 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
              </svg>
              <p className="text-sm text-amber-700">
                No voice profiles yet.{' '}
                <Link to="/create" className="underline font-medium hover:text-amber-900">Add one →</Link>
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {voices.map(v => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => { setVoiceId(v.id); setLanguage(v.language) }}
                  className={`flex items-center gap-3 p-3 rounded-xl border text-left transition-colors duration-150 ${
                    voiceId === v.id
                      ? 'border-indigo-500 bg-indigo-50 ring-2 ring-indigo-300'
                      : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
                  }`}
                >
                  <div className="w-9 h-9 rounded-lg bg-indigo-100 flex items-center justify-center flex-shrink-0">
                    <svg className="w-4 h-4 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
                    </svg>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-900 truncate">{v.name}</p>
                    <p className="text-xs text-slate-500">{LANG_LABELS[v.language] || v.language}</p>
                  </div>
                  {voiceId === v.id && (
                    <svg className="w-4 h-4 text-indigo-600 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Text input */}
        <div className="card p-6 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-800">Text</h2>
            <span className={`text-xs font-mono ${
              text.length > MAX_CHARS * 0.95 ? 'text-red-500'
              : text.length > MAX_CHARS * 0.8  ? 'text-amber-500'
              : 'text-slate-400'
            }`}>
              {text.length}/{MAX_CHARS}
            </span>
          </div>
          <textarea
            className="input resize-none"
            rows={5}
            placeholder="Enter the text you want to synthesise…"
            value={text}
            onChange={e => setText(e.target.value.slice(0, MAX_CHARS))}
            required
          />
          {/* char bar */}
          <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-200 ${
                text.length > MAX_CHARS * 0.95 ? 'bg-red-500'
                : text.length > MAX_CHARS * 0.8  ? 'bg-amber-400'
                : 'bg-indigo-500'
              }`}
              style={{ width: `${Math.min(100, (text.length / MAX_CHARS) * 100)}%` }}
            />
          </div>
        </div>

        {/* Options */}
        <div className="card p-6">
          <h2 className="text-sm font-semibold text-slate-800 mb-4">Options</h2>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Language</label>
              <select className="input" value={language} onChange={e => setLanguage(e.target.value)}>
                {LANGUAGES.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Mode</label>
              <select className="input" value={mode} onChange={e => setMode(e.target.value)}>
                <option value="standard">Standard TTS</option>
                <option value="clone">Voice Clone</option>
              </select>
            </div>
          </div>
        </div>

        {/* Error */}
        {errorMsg && (
          <div className="rounded-xl bg-red-50 border border-red-200 px-4 py-3">
            <p className="text-sm text-red-700">{errorMsg}</p>
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          className="btn-primary w-full py-3"
          disabled={submitting || (!!jobId && jobStatus !== 'completed' && jobStatus !== 'failed')}
        >
          {submitting ? (
            <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> Submitting…</>
          ) : (
            <>
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 5.653c0-.856.917-1.398 1.667-.986l11.54 6.347a1.125 1.125 0 010 1.972l-11.54 6.347a1.125 1.125 0 01-1.667-.986V5.653z" />
              </svg>
              Generate Speech
            </>
          )}
        </button>
      </form>

      {/* Progress + output */}
      {jobStatus && (
        <div className="mt-6 card p-6 space-y-5">
          {/* Steps */}
          <div className="flex items-center gap-4">
            <StatusStep step={1} current={stepIndex} />
            <div className="flex-1 h-px bg-slate-200" />
            <StatusStep step={2} current={stepIndex} />
            <div className="flex-1 h-px bg-slate-200" />
            <StatusStep step={3} current={stepIndex} />
          </div>

          {/* Status badge */}
          <div className="flex items-center gap-3">
            {jobStatus === 'completed' && (
              <span className="badge badge-green">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Completed
              </span>
            )}
            {jobStatus === 'failed' && (
              <span className="badge badge-red">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500" /> Failed
              </span>
            )}
            {(jobStatus === 'queued' || jobStatus === 'processing') && (
              <span className="badge badge-blue">
                <div className="w-3 h-3 border border-blue-600 border-t-transparent rounded-full animate-spin" />
                {jobStatus === 'queued' ? 'Queued' : 'Processing'}
              </span>
            )}
            {provider && (
              <span className="badge badge-slate text-xs">{provider}</span>
            )}
            {fallback && (
              <span className="badge badge-amber text-xs">EdgeTTS fallback</span>
            )}
          </div>

          {/* Audio output */}
          {outputUrl && (
            <div>
              <AudioPlayer url={outputUrl} />
              <a
                href={outputUrl}
                download
                className="btn-secondary mt-3 text-xs w-fit"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
                </svg>
                Download WAV
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
