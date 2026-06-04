import React, { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'

const API = 'http://localhost:8000'
const ACCEPT = '.wav,.mp3,.ogg,.webm,.flac'
const LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'te', label: 'Telugu' },
  { code: 'ta', label: 'Tamil' },
  { code: 'hi', label: 'Hindi' },
]

export default function CreateVoice() {
  const navigate = useNavigate()
  const fileInputRef = useRef(null)

  const [file,        setFile]        = useState(null)
  const [dragging,    setDragging]    = useState(false)
  const [name,        setName]        = useState('')
  const [language,    setLanguage]    = useState('en')
  const [description, setDescription] = useState('')
  const [tags,        setTags]        = useState('')
  const [submitting,  setSubmitting]  = useState(false)
  const [error,       setError]       = useState(null)

  // ── Drag-and-drop ─────────────────────────────────────────────────────
  const onDragOver  = useCallback(e => { e.preventDefault(); setDragging(true)  }, [])
  const onDragLeave = useCallback(()  => setDragging(false), [])
  const onDrop      = useCallback(e => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) setFile(f)
  }, [])

  // ── Submit ────────────────────────────────────────────────────────────
  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!file)       { setError('Please select an audio file.'); return }
    if (!name.trim()){ setError('Please enter a display name.');  return }

    setError(null)
    setSubmitting(true)

    const fd = new FormData()
    fd.append('audio',       file)
    fd.append('name',        name.trim())
    fd.append('language',    language)
    fd.append('description', description.trim())
    fd.append('tags',        tags.trim())

    try {
      const res = await fetch(`${API}/voices`, { method: 'POST', body: fd })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail || res.statusText)
      }
      navigate('/')
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  const fileSizeLabel = file
    ? file.size < 1024 * 1024
      ? `${(file.size / 1024).toFixed(0)} KB`
      : `${(file.size / (1024 * 1024)).toFixed(1)} MB`
    : null

  return (
    <div className="max-w-2xl">
      {/* Header */}
      <div className="page-header">
        <h1 className="page-title">Add Voice Profile</h1>
        <p className="page-subtitle">Upload a clear audio sample (5–30 s) to create a reusable voice.</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">

        {/* Audio upload */}
        <div className="card p-6">
          <label className="label">Voice Sample *</label>
          <div
            className={`dropzone ${dragging ? 'dropzone-active' : ''}`}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            {file ? (
              <>
                <div className="w-12 h-12 rounded-xl bg-indigo-100 flex items-center justify-center">
                  <svg className="w-6 h-6 text-indigo-600" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
                  </svg>
                </div>
                <p className="text-sm font-medium text-slate-800 text-center">{file.name}</p>
                <p className="text-xs text-slate-500">{fileSizeLabel}</p>
                <button
                  type="button"
                  onClick={e => { e.stopPropagation(); setFile(null) }}
                  className="text-xs text-red-500 hover:text-red-700 underline"
                >
                  Remove
                </button>
              </>
            ) : (
              <>
                <div className="w-12 h-12 rounded-xl bg-slate-100 flex items-center justify-center">
                  <svg className="w-6 h-6 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
                  </svg>
                </div>
                <p className="text-sm font-medium text-slate-700">Drop audio here, or click to browse</p>
                <p className="text-xs text-slate-400">WAV · MP3 · OGG · WEBM · FLAC — max 50 MB</p>
              </>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={e => setFile(e.target.files[0] || null)}
          />
        </div>

        {/* Voice details */}
        <div className="card p-6 space-y-4">
          <h2 className="text-sm font-semibold text-slate-800">Voice Details</h2>

          {/* Name */}
          <div>
            <label className="label">Display Name *</label>
            <input
              type="text"
              className="input"
              placeholder="e.g. Ananya — Female Telugu"
              value={name}
              onChange={e => setName(e.target.value)}
              maxLength={100}
              required
            />
          </div>

          {/* Language */}
          <div>
            <label className="label">Language</label>
            <select
              className="input"
              value={language}
              onChange={e => setLanguage(e.target.value)}
            >
              {LANGUAGES.map(l => (
                <option key={l.code} value={l.code}>{l.label}</option>
              ))}
            </select>
          </div>

          {/* Description */}
          <div>
            <label className="label">Description <span className="text-slate-400 font-normal">(optional)</span></label>
            <textarea
              className="input resize-none"
              rows={2}
              placeholder="Gender, accent, tone notes…"
              value={description}
              onChange={e => setDescription(e.target.value)}
              maxLength={300}
            />
          </div>

          {/* Tags */}
          <div>
            <label className="label">Tags <span className="text-slate-400 font-normal">(comma-separated, optional)</span></label>
            <input
              type="text"
              className="input"
              placeholder="female, formal, studio"
              value={tags}
              onChange={e => setTags(e.target.value)}
            />
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="rounded-xl bg-red-50 border border-red-200 px-4 py-3">
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center gap-3">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Saving…
              </>
            ) : (
              'Save Voice Profile'
            )}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate('/')}
            disabled={submitting}
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  )
}
