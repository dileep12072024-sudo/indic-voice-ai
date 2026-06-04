import React, { useRef, useState, useEffect, useCallback } from 'react'

export default function AudioPlayer({ url, label }) {
  const audioRef   = useRef(null)
  const [playing,  setPlaying]  = useState(false)
  const [duration, setDuration] = useState(0)
  const [current,  setCurrent]  = useState(0)
  const [volume,   setVolume]   = useState(1)

  useEffect(() => {
    const a = audioRef.current
    if (!a) return
    const onMeta  = () => setDuration(a.duration || 0)
    const onTime  = () => setCurrent(a.currentTime)
    const onEnded = () => setPlaying(false)
    a.addEventListener('loadedmetadata', onMeta)
    a.addEventListener('timeupdate',     onTime)
    a.addEventListener('ended',          onEnded)
    return () => {
      a.removeEventListener('loadedmetadata', onMeta)
      a.removeEventListener('timeupdate',     onTime)
      a.removeEventListener('ended',          onEnded)
    }
  }, [url])

  const togglePlay = useCallback(() => {
    const a = audioRef.current
    if (!a) return
    if (playing) { a.pause(); setPlaying(false) }
    else         { a.play().catch(() => {}); setPlaying(true) }
  }, [playing])

  const onSeek = useCallback(e => {
    const a = audioRef.current
    if (!a || !duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width))
    a.currentTime = frac * duration
  }, [duration])

  const onVolume = useCallback(e => {
    const v = parseFloat(e.target.value)
    setVolume(v)
    if (audioRef.current) audioRef.current.volume = v
  }, [])

  const fmt = s => {
    if (!s || !isFinite(s)) return '0:00'
    const m = Math.floor(s / 60)
    const sec = Math.floor(s % 60).toString().padStart(2, '0')
    return `${m}:${sec}`
  }

  const pct = duration > 0 ? (current / duration) * 100 : 0

  return (
    <div className="card p-4 space-y-3">
      {label && <p className="text-xs font-medium text-slate-500">{label}</p>}
      <audio ref={audioRef} src={url} preload="metadata" />

      {/* Waveform / seek bar */}
      <div
        role="slider"
        aria-label="Seek"
        aria-valuenow={Math.round(pct)}
        tabIndex={0}
        className="relative w-full h-10 bg-slate-100 rounded-xl overflow-hidden cursor-pointer
                   hover:bg-slate-200 transition-colors"
        onClick={onSeek}
        onKeyDown={e => {
          const a = audioRef.current
          if (!a) return
          if (e.key === 'ArrowRight') a.currentTime = Math.min(duration, a.currentTime + 5)
          if (e.key === 'ArrowLeft')  a.currentTime = Math.max(0,        a.currentTime - 5)
        }}
      >
        {/* Progress fill */}
        <div
          className="absolute inset-y-0 left-0 bg-indigo-500 opacity-20 transition-all duration-100"
          style={{ width: `${pct}%` }}
        />
        {/* Playhead */}
        <div
          className="absolute top-0 bottom-0 w-0.5 bg-indigo-600 transition-all duration-100"
          style={{ left: `${pct}%` }}
        />
        {/* Time labels */}
        <div className="absolute inset-0 flex items-center justify-between px-3 pointer-events-none">
          <span className="text-xs font-mono text-slate-500">{fmt(current)}</span>
          <span className="text-xs font-mono text-slate-400">{fmt(duration)}</span>
        </div>
      </div>

      {/* Controls */}
      <div className="flex items-center gap-3">
        {/* Play / Pause */}
        <button
          type="button"
          onClick={togglePlay}
          className="w-10 h-10 rounded-xl bg-indigo-600 text-white flex items-center justify-center
                     hover:bg-indigo-700 active:scale-95 transition-all duration-150 flex-shrink-0"
          aria-label={playing ? 'Pause' : 'Play'}
        >
          {playing ? (
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <rect x="6" y="4" width="4" height="16" rx="1" />
              <rect x="14" y="4" width="4" height="16" rx="1" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5.14v14l11-7-11-7z" />
            </svg>
          )}
        </button>

        {/* Volume */}
        <div className="flex items-center gap-2 flex-1">
          <svg className="w-4 h-4 text-slate-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            {volume === 0
              ? <path strokeLinecap="round" strokeLinejoin="round" d="M5.586 15H4a1 1 0 01-1-1v-4a1 1 0 011-1h1.586l4.707-4.707C10.923 3.663 12 4.109 12 5v14c0 .891-1.077 1.337-1.707.707L5.586 15z" />
              : <path strokeLinecap="round" strokeLinejoin="round" d="M15.536 8.464a5 5 0 010 7.072M12 6a7 7 0 010 12M9 9H5a1 1 0 00-1 1v4a1 1 0 001 1h4l5 5V4L9 9z" />
            }
          </svg>
          <input
            type="range"
            min={0} max={1} step={0.05}
            value={volume}
            onChange={onVolume}
            className="flex-1 h-1.5 accent-indigo-600 cursor-pointer"
            aria-label="Volume"
          />
        </div>
      </div>
    </div>
  )
}
