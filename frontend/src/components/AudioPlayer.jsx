import React, { useRef, useState, useEffect, useCallback } from 'react'

export default function AudioPlayer({ src, filename = 'output.wav' }) {
  const audioRef        = useRef(null)
  const progressBarRef  = useRef(null)
  const [playing, setPlaying]       = useState(false)
  const [progress, setProgress]     = useState(0)   // 0–100
  const [duration, setDuration]     = useState(0)
  const [currentTime, setCurrent]   = useState(0)
  const [volume, setVolume]         = useState(1)
  const [muted, setMuted]           = useState(false)
  const [seeking, setSeeking]       = useState(false)

  // Reset player when src changes
  useEffect(() => {
    setPlaying(false)
    setProgress(0)
    setCurrent(0)
    setDuration(0)
  }, [src])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    const onTimeUpdate = () => {
      if (!audio.duration || isNaN(audio.duration)) return
      setCurrent(audio.currentTime)
      setProgress((audio.currentTime / audio.duration) * 100)
    }
    const onLoaded = () => {
      if (!isNaN(audio.duration)) setDuration(audio.duration)
    }
    const onEnded = () => {
      setPlaying(false)
      setProgress(0)
      setCurrent(0)
      audio.currentTime = 0
    }

    audio.addEventListener('timeupdate',     onTimeUpdate)
    audio.addEventListener('loadedmetadata', onLoaded)
    audio.addEventListener('durationchange', onLoaded)
    audio.addEventListener('ended',          onEnded)

    return () => {
      audio.removeEventListener('timeupdate',     onTimeUpdate)
      audio.removeEventListener('loadedmetadata', onLoaded)
      audio.removeEventListener('durationchange', onLoaded)
      audio.removeEventListener('ended',          onEnded)
    }
  }, [src])

  const togglePlay = () => {
    const audio = audioRef.current
    if (!audio) return
    if (playing) { audio.pause(); setPlaying(false) }
    else         { audio.play().catch(() => {}); setPlaying(true) }
  }

  // Seek: compute fraction from pointer position within the bar
  const seekToFraction = useCallback((clientX) => {
    const bar = progressBarRef.current
    if (!bar || !duration) return
    const rect = bar.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    const audio = audioRef.current
    if (audio) {
      audio.currentTime = frac * duration
      setProgress(frac * 100)
      setCurrent(frac * duration)
    }
  }, [duration])

  const onBarClick       = (e) => seekToFraction(e.clientX)
  const onBarMouseDown   = (e) => { setSeeking(true); seekToFraction(e.clientX) }
  const onWindowMouseMove= useCallback((e) => { if (seeking) seekToFraction(e.clientX) }, [seeking, seekToFraction])
  const onWindowMouseUp  = useCallback(() => setSeeking(false), [])

  useEffect(() => {
    if (seeking) {
      window.addEventListener('mousemove', onWindowMouseMove)
      window.addEventListener('mouseup',   onWindowMouseUp)
    }
    return () => {
      window.removeEventListener('mousemove', onWindowMouseMove)
      window.removeEventListener('mouseup',   onWindowMouseUp)
    }
  }, [seeking, onWindowMouseMove, onWindowMouseUp])

  const handleVolume = (e) => {
    const v = parseFloat(e.target.value)
    setVolume(v)
    if (audioRef.current) audioRef.current.volume = v
    if (v > 0) setMuted(false)
  }

  const toggleMute = () => {
    const audio = audioRef.current
    if (!audio) return
    const next = !muted
    setMuted(next)
    audio.muted = next
  }

  const fmt = (s) => {
    if (!s || isNaN(s) || !isFinite(s)) return '0:00'
    const m   = Math.floor(s / 60)
    const sec = Math.floor(s % 60)
    return `${m}:${sec.toString().padStart(2, '0')}`
  }

  // Waveform bar heights — static decorative, changes colour with progress
  const BARS = Array.from({ length: 48 }, (_, i) =>
    20 + Math.round(Math.sin(i * 0.8) * 12 + Math.cos(i * 1.3) * 8)
  )

  return (
    <div className="bg-dark-700 border border-neon-cyan/20 rounded-xl p-5 space-y-4 border-glow">
      <audio ref={audioRef} src={src} preload="metadata" />

      {/* Decorative waveform */}
      <div className="flex items-end justify-center gap-0.5 h-10" aria-hidden="true">
        {BARS.map((h, i) => (
          <div
            key={i}
            className="w-1 rounded-sm transition-colors duration-75"
            style={{
              height: `${h}px`,
              backgroundColor: i / BARS.length * 100 <= progress ? '#22d3ee' : '#334155',
            }}
          />
        ))}
      </div>

      {/* Progress bar */}
      <div
        ref={progressBarRef}
        onClick={onBarClick}
        onMouseDown={onBarMouseDown}
        role="slider"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress)}
        aria-label="Audio seek bar"
        className="relative h-2 bg-slate-700 rounded-full cursor-pointer group select-none"
      >
        <div
          className="absolute left-0 top-0 h-full bg-gradient-to-r from-neon-cyan to-neon-purple rounded-full"
          style={{ width: `${progress}%` }}
        />
        {/* Scrubber handle */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3.5 h-3.5 rounded-full bg-neon-cyan shadow-lg shadow-neon-cyan/50 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
          style={{ left: `calc(${progress}% - 7px)` }}
        />
      </div>

      {/* Time display */}
      <div className="flex justify-between text-xs text-slate-500 font-mono select-none">
        <span>{fmt(currentTime)}</span>
        <span>{fmt(duration)}</span>
      </div>

      {/* Controls row */}
      <div className="flex items-center gap-4 flex-wrap">
        {/* Play / Pause */}
        <button
          onClick={togglePlay}
          aria-label={playing ? 'Pause' : 'Play'}
          className="w-12 h-12 rounded-full bg-gradient-to-br from-neon-cyan to-neon-purple flex items-center justify-center
            text-dark-900 hover:scale-110 active:scale-95 transition-all shadow-lg shadow-neon-cyan/30 flex-shrink-0"
        >
          {playing ? (
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <rect x="6" y="4" width="4" height="16" rx="1" />
              <rect x="14" y="4" width="4" height="16" rx="1" />
            </svg>
          ) : (
            <svg className="w-5 h-5 ml-0.5" fill="currentColor" viewBox="0 0 24 24">
              <polygon points="5,3 19,12 5,21" />
            </svg>
          )}
        </button>

        {/* Volume */}
        <button
          onClick={toggleMute}
          aria-label={muted ? 'Unmute' : 'Mute'}
          className="text-slate-400 hover:text-neon-cyan transition-colors flex-shrink-0"
        >
          {muted || volume === 0 ? (
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02z" />
            </svg>
          )}
        </button>

        <input
          type="range" min="0" max="1" step="0.05"
          value={muted ? 0 : volume}
          onChange={handleVolume}
          aria-label="Volume"
          className="w-20 accent-neon-cyan flex-shrink-0"
        />

        <div className="flex-1" />

        {/* Download */}
        <a
          href={src}
          download={filename}
          className="flex items-center gap-2 px-4 py-2 bg-neon-green/10 border border-neon-green/30 text-neon-green
            rounded-lg text-xs font-medium hover:bg-neon-green/20 hover:border-neon-green/50 transition-all flex-shrink-0"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
          </svg>
          Download WAV
        </a>
      </div>
    </div>
  )
}
