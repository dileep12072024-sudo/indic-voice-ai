import React, { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'

const LANG_LABELS = { te: 'Telugu', ta: 'Tamil', hi: 'Hindi', en: 'English' }

// ── Cloning status badge ────────────────────────────────────────────────────
function CloningBadge({ voice, cloningAvailable }) {
  // If OpenVoice is fully disabled at server level
  if (!cloningAvailable) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 text-slate-500 text-xs font-medium px-2.5 py-0.5">
        <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
        Clone Unavailable
      </span>
    )
  }
  // Embedding extraction succeeded
  if (voice.cloning_ready) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 text-emerald-700 text-xs font-medium px-2.5 py-0.5">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
        Ready
      </span>
    )
  }
  // Extraction failed with a known error
  if (voice.embedding_error) {
    return (
      <span
        title={voice.embedding_error}
        className="inline-flex items-center gap-1 rounded-full bg-red-100 text-red-700 text-xs font-medium px-2.5 py-0.5 cursor-help"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
        Clone Unavailable
      </span>
    )
  }
  // Still processing (no error yet, not ready)
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 text-amber-700 text-xs font-medium px-2.5 py-0.5">
      <span className="w-2 h-2 border border-amber-600 border-t-transparent rounded-full animate-spin" />
      Processing
    </span>
  )
}

// ── Voice card ──────────────────────────────────────────────────────────────
function VoiceCard({ voice, cloningAvailable, onDelete }) {
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    if (!window.confirm(`Delete voice "${voice.name}"? This cannot be undone.`)) return
    setDeleting(true)
    try {
      const res = await fetch(`/voices/${voice.id}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(await res.text())
      onDelete(voice.id)
    } catch (err) {
      alert(`Failed to delete: ${err.message}`)
      setDeleting(false)
    }
  }

  const created = new Date(voice.created_at * 1000).toLocaleDateString('en-IN', {
    day: 'numeric', month: 'short', year: 'numeric',
  })

  return (
    <div className="card card-hover p-5 flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-indigo-100 flex items-center justify-center flex-shrink-0">
            <svg className="w-5 h-5 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </div>
          <div>
            <p className="font-semibold text-slate-900 text-sm leading-snug">{voice.name}</p>
            <p className="text-xs text-slate-500 mt-0.5">{created}</p>
          </div>
        </div>
        <span className="badge badge-blue flex-shrink-0">
          {LANG_LABELS[voice.language] || voice.language}
        </span>
      </div>

      {/* Cloning status */}
      <div className="flex items-center gap-2">
        <CloningBadge voice={voice} cloningAvailable={cloningAvailable} />
        {voice.cloning_ready && voice.embedding_key && (
          <span className="text-xs text-slate-400 font-mono truncate" title={voice.embedding_key}>
            emb saved
          </span>
        )}
      </div>

      {/* Embedding error detail */}
      {voice.embedding_error && !voice.cloning_ready && (
        <details className="rounded-lg bg-red-50 border border-red-100 px-3 py-2 text-xs">
          <summary className="text-red-600 font-medium cursor-pointer select-none">Error detail</summary>
          <p className="mt-1 text-red-500 font-mono break-all">{voice.embedding_error}</p>
        </details>
      )}

      {/* Description */}
      {voice.description && (
        <p className="text-xs text-slate-500 leading-relaxed line-clamp-2">{voice.description}</p>
      )}

      {/* Tags */}
      {voice.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {voice.tags.map(tag => (
            <span key={tag} className="badge badge-slate text-xs">{tag}</span>
          ))}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between pt-1 border-t border-slate-100">
        <span className="text-xs text-slate-400">
          {(voice.sample_size / 1024).toFixed(0)} KB
        </span>
        <div className="flex items-center gap-2">
          <Link
            to={`/generate?voice_id=${voice.id}&voice_name=${encodeURIComponent(voice.name)}&lang=${voice.language}`}
            className="btn-secondary py-1.5 px-3 text-xs"
          >
            Use
          </Link>
          <button
            onClick={handleDelete}
            disabled={deleting}
            className="btn-danger py-1.5 px-3 text-xs"
          >
            {deleting ? '…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main page ───────────────────────────────────────────────────────────────
export default function VoiceLibrary() {
  const [voices,          setVoices]          = useState([])
  const [cloningAvailable, setCloningAvailable] = useState(false)
  const [loading,         setLoading]         = useState(true)
  const [error,           setError]           = useState(null)

  const loadVoices = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/voices')
      if (!res.ok) throw new Error(`API error ${res.status}`)
      const data = await res.json()
      setVoices(data.voices || [])
      setCloningAvailable(data.cloning_available || false)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadVoices() }, [loadVoices])

  // Poll for voices that are still processing (cloning_ready=false, no error)
  useEffect(() => {
    const processing = voices.filter(v => !v.cloning_ready && !v.embedding_error)
    if (processing.length === 0) return
    const timer = setTimeout(loadVoices, 4000)
    return () => clearTimeout(timer)
  }, [voices, loadVoices])

  const handleDelete = (id) => setVoices(prev => prev.filter(v => v.id !== id))

  const readyCount      = voices.filter(v => v.cloning_ready).length
  const processingCount = voices.filter(v => !v.cloning_ready && !v.embedding_error).length

  return (
    <div>
      {/* Header */}
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title">Voice Library</h1>
          <p className="page-subtitle">Your saved voice profiles for speech generation.</p>
        </div>
        <Link to="/create" className="btn-primary">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Add Voice
        </Link>
      </div>

      {/* Cloning status summary */}
      {!loading && !error && voices.length > 0 && (
        <div className={`mb-5 flex items-center gap-3 rounded-xl px-4 py-3 text-sm border ${
          cloningAvailable
            ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
            : 'bg-slate-50 border-slate-200 text-slate-600'
        }`}>
          <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          {cloningAvailable ? (
            <span>
              Voice cloning is <strong>active</strong>.{
                readyCount > 0 && ` ${readyCount} voice${readyCount !== 1 ? 's' : ''} ready to clone.`
              }{
                processingCount > 0 && ` ${processingCount} still extracting embedding…`
              }
            </span>
          ) : (
            <span>
              Voice cloning is <strong>disabled</strong>. Set{' '}
              <code className="bg-slate-100 px-1 rounded text-xs">OPENVOICE_ENABLED=true</code>{' '}
              and install{' '}
              <code className="bg-slate-100 px-1 rounded text-xs">requirements-clone.txt</code>{' '}
              to enable real voice cloning.
            </span>
          )}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex items-center justify-center py-24">
          <div className="w-8 h-8 border-2 border-indigo-600 border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="card p-6 border-red-200 bg-red-50">
          <p className="text-sm font-medium text-red-700 mb-1">Could not load voices</p>
          <p className="text-xs text-red-600 mb-4 font-mono">{error}</p>
          <p className="text-xs text-red-500 mb-3">Make sure the backend is running:</p>
          <code className="block text-xs bg-red-100 text-red-800 rounded-lg px-3 py-2 mb-4 font-mono">
            ./scripts/start-backend.sh
          </code>
          <button onClick={loadVoices} className="btn-secondary text-sm">Retry</button>
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && voices.length === 0 && (
        <div className="card p-16 text-center">
          <div className="w-16 h-16 mx-auto rounded-2xl bg-slate-100 flex items-center justify-center mb-5">
            <svg className="w-8 h-8 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4m-4-8a3 3 0 01-3-3V5a3 3 0 116 0v6a3 3 0 01-3 3z" />
            </svg>
          </div>
          <h3 className="text-base font-semibold text-slate-900 mb-2">No voices yet</h3>
          <p className="text-sm text-slate-500 mb-6 max-w-xs mx-auto">
            Upload an audio sample to create your first voice profile.
          </p>
          <Link to="/create" className="btn-primary mx-auto w-fit">Add Your First Voice</Link>
        </div>
      )}

      {/* Voice grid */}
      {!loading && !error && voices.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {voices.map(v => (
            <VoiceCard
              key={v.id}
              voice={v}
              cloningAvailable={cloningAvailable}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
    </div>
  )
}
