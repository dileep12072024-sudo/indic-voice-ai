import React, { useState, useRef, useEffect, useCallback } from 'react';
import AudioPlayer from './AudioPlayer';

/**
 * VoiceCloner.jsx — Premium AI Voice Studio UI
 *
 * UI Upgrade (Phase 5)
 * ────────────────────
 * - Card-based section layout (Voice Sample | Text | Language+Mode | Status | Output)
 * - Mobile-first responsive grid (stacked ≤767px, side-by-side ≥768px)
 * - Drag-and-drop + click file upload zone
 * - Character counter 0/2000 with colour-coded fill bar
 * - 3-step progress indicator: Submitting → Processing → Completed
 * - Real-time clone status badge: Real Clone / Fallback / Standard TTS
 * - Actionable error messages with copy-ready commands
 * - Blob URL revocation on each generation and on unmount (FIX-4 retained)
 * - Client-side 50 MB file-size guard (FIX-3 retained)
 */

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
const MAX_TEXT_LENGTH     = 2000;
const POLL_INTERVAL_MS    = 2000;
const POLL_TIMEOUT_MS     = 120_000;

const LANGUAGES = [
  { code: 'te', label: 'Telugu',  flag: '🇮🇳' },
  { code: 'ta', label: 'Tamil',   flag: '🇮🇳' },
  { code: 'hi', label: 'Hindi',   flag: '🇮🇳' },
  { code: 'en', label: 'English', flag: '🇺🇸' },
];

const BASE_URL =
  (import.meta.env.VITE_WORKERS_URL || import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');

// ── Helpers ────────────────────────────────────────────────────────────────────

function CharCounter({ current, max }) {
  const pct   = Math.min(100, (current / max) * 100);
  const color = pct < 60 ? '#22d3ee'   // neon-cyan
              : pct < 80 ? '#84cc16'   // lime
              : pct < 95 ? '#f59e0b'   // amber
              :             '#ef4444'; // red
  const textColor = pct >= 95 ? '#ef4444' : pct >= 80 ? '#f59e0b' : '#94a3b8';
  return (
    <div style={{ marginTop: '0.5rem' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        fontSize: '0.75rem', marginBottom: '0.25rem',
      }}>
        <span style={{ color: textColor, fontVariantNumeric: 'tabular-nums' }}>
          {current.toLocaleString()} / {max.toLocaleString()}
        </span>
        {pct >= 95 && (
          <span style={{ color: '#ef4444', fontSize: '0.7rem' }}>
            {max - current} remaining
          </span>
        )}
      </div>
      <div style={{
        height: '3px', background: '#1e293b', borderRadius: '999px', overflow: 'hidden',
      }}>
        <div style={{
          height: '100%', width: `${pct}%`,
          background: color,
          borderRadius: '999px',
          transition: 'width 0.15s ease, background 0.3s ease',
        }} />
      </div>
    </div>
  );
}

function SectionCard({ title, icon, children, style }) {
  return (
    <div style={{
      background: 'rgba(15, 23, 42, 0.7)',
      border: '1px solid rgba(34, 211, 238, 0.15)',
      borderRadius: '0.75rem',
      padding: '1.25rem',
      backdropFilter: 'blur(8px)',
      ...style,
    }}>
      {title && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.5rem',
          marginBottom: '0.875rem',
        }}>
          {icon && <span style={{ fontSize: '1rem' }}>{icon}</span>}
          <span style={{
            fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.1em',
            color: 'rgba(34, 211, 238, 0.7)', textTransform: 'uppercase',
          }}>
            {title}
          </span>
        </div>
      )}
      {children}
    </div>
  );
}

function StatusBadge({ mode, cloningApplied, fallbackUsed, loading, jobStatus }) {
  if (loading && jobStatus === 'submitting') {
    return (
      <div style={badgeStyle('#60a5fa', 'rgba(96,165,250,0.1)', 'rgba(96,165,250,0.3)')}>
        <PulsingDot color="#60a5fa" />
        Submitting job…
      </div>
    );
  }
  if (loading && jobStatus === 'processing') {
    return (
      <div style={badgeStyle('#a78bfa', 'rgba(167,139,250,0.1)', 'rgba(167,139,250,0.3)')}>
        <PulsingDot color="#a78bfa" />
        Processing audio…
      </div>
    );
  }
  if (jobStatus === 'completed') {
    if (mode === 'clone' && cloningApplied) {
      return (
        <div style={badgeStyle('#22c55e', 'rgba(34,197,94,0.1)', 'rgba(34,197,94,0.3)')}>
          <StaticDot color="#22c55e" />
          Real Clone — OpenVoice v2
        </div>
      );
    }
    if (mode === 'clone' && !cloningApplied) {
      return (
        <div style={badgeStyle('#f59e0b', 'rgba(245,158,11,0.1)', 'rgba(245,158,11,0.3)')}>
          <StaticDot color="#f59e0b" />
          Fallback — EdgeTTS (OpenVoice not enabled)
        </div>
      );
    }
    return (
      <div style={badgeStyle('#22d3ee', 'rgba(34,211,238,0.1)', 'rgba(34,211,238,0.3)')}>
        <StaticDot color="#22d3ee" />
        Standard TTS — EdgeTTS Neural
      </div>
    );
  }
  if (jobStatus === 'failed') {
    return (
      <div style={badgeStyle('#ef4444', 'rgba(239,68,68,0.1)', 'rgba(239,68,68,0.3)')}>
        <StaticDot color="#ef4444" />
        Generation failed
      </div>
    );
  }
  return null;
}

function badgeStyle(color, bg, border) {
  return {
    display: 'inline-flex', alignItems: 'center', gap: '0.4rem',
    fontSize: '0.75rem', fontWeight: 600,
    color, background: bg,
    border: `1px solid ${border}`,
    borderRadius: '999px', padding: '0.3rem 0.75rem',
  };
}

function PulsingDot({ color }) {
  return (
    <span style={{
      width: '6px', height: '6px', borderRadius: '50%',
      background: color, flexShrink: 0,
      animation: 'pulse 1.5s ease-in-out infinite',
    }} />
  );
}
function StaticDot({ color }) {
  return (
    <span style={{
      width: '6px', height: '6px', borderRadius: '50%',
      background: color, flexShrink: 0,
    }} />
  );
}

function ProgressSteps({ jobStatus, loading }) {
  const steps = [
    { key: 'submitting',  label: 'Submitting' },
    { key: 'processing',  label: 'Processing' },
    { key: 'completed',   label: 'Completed'  },
  ];
  const activeIdx = jobStatus === 'submitting'  ? 0
                  : jobStatus === 'processing'   ? 1
                  : jobStatus === 'completed'    ? 2
                  : jobStatus === 'failed'        ? -1
                  : -1;
  if (!loading && jobStatus !== 'completed' && jobStatus !== 'failed') return null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
      {steps.map((step, i) => {
        const done    = i < activeIdx;
        const current = i === activeIdx;
        const failed  = jobStatus === 'failed' && i === activeIdx;
        const color   = failed  ? '#ef4444'
                      : done    ? '#22c55e'
                      : current ? '#22d3ee'
                      :           '#334155';
        const textCol = done || current ? '#e2e8f0' : '#64748b';
        return (
          <React.Fragment key={step.key}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.25rem' }}>
              <div style={{
                width: '28px', height: '28px', borderRadius: '50%',
                border: `2px solid ${color}`,
                background: done ? color : 'transparent',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '0.65rem', fontWeight: 700, color: done ? '#0f172a' : color,
                flexShrink: 0,
              }}>
                {done ? '✓' : i + 1}
              </div>
              <span style={{ fontSize: '0.6rem', color: textCol, whiteSpace: 'nowrap' }}>
                {step.label}
              </span>
            </div>
            {i < steps.length - 1 && (
              <div style={{
                flex: 1, height: '2px', marginBottom: '1rem',
                background: done ? '#22c55e' : '#1e293b',
                minWidth: '2rem',
              }} />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export default function VoiceCloner() {
  const [file,           setFile]           = useState(null);
  const [isDragging,     setIsDragging]     = useState(false);
  const [text,           setText]           = useState('');
  const [language,       setLanguage]       = useState('en');
  const [mode,           setMode]           = useState('standard');
  const [loading,        setLoading]        = useState(false);
  const [error,          setError]          = useState(null);
  const [audioUrl,       setAudioUrl]       = useState(null);
  const [jobStatus,      setJobStatus]      = useState(null);  // submitting|processing|completed|failed
  const [cloningApplied, setCloningApplied] = useState(false);
  const [fallbackUsed,   setFallbackUsed]   = useState(false);
  const [providerName,   setProviderName]   = useState(null);

  const fileInputRef = useRef(null);
  const blobUrlRef   = useRef(null);

  useEffect(() => {
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, []);

  const revokePreviousBlob = useCallback(() => {
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current);
      blobUrlRef.current = null;
    }
  }, []);

  // ── File handling ────────────────────────────────────────────────────────────

  const acceptFile = useCallback((selected) => {
    if (!selected) return;
    if (selected.size > MAX_FILE_SIZE_BYTES) {
      setError(
        `File too large: ${(selected.size / 1024 / 1024).toFixed(1)} MB. ` +
        `Maximum allowed size is 50 MB.`
      );
      return;
    }
    const ext = selected.name.split('.').pop().toLowerCase();
    const ok  = ['wav','mp3','ogg','webm','flac'].includes(ext);
    if (!ok) {
      setError(`Unsupported format: .${ext}. Accepted: WAV, MP3, OGG, WEBM, FLAC.`);
      return;
    }
    setFile(selected);
    setError(null);
  }, []);

  const handleFileChange = (e) => acceptFile(e.target.files[0]);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files[0];
    acceptFile(dropped);
  }, [acceptFile]);

  const handleDragOver = (e) => { e.preventDefault(); setIsDragging(true); };
  const handleDragLeave = () => setIsDragging(false);

  // ── Generate ─────────────────────────────────────────────────────────────────

  const handleGenerate = async () => {
    if (!file)             { setError('Please upload a voice sample first.'); return; }
    if (!text.trim())      { setError('Please enter text to synthesise.');    return; }
    if (text.trim().length > MAX_TEXT_LENGTH) {
      setError(`Text too long: ${text.trim().length.toLocaleString()} characters. Maximum is ${MAX_TEXT_LENGTH.toLocaleString()}.`);
      return;
    }

    setLoading(true);
    setError(null);
    setJobStatus('submitting');
    setCloningApplied(false);
    setFallbackUsed(false);
    setProviderName(null);
    revokePreviousBlob();
    setAudioUrl(null);

    try {
      // ── Step 1: submit job ──────────────────────────────────────────────────
      const formData = new FormData();
      formData.append('audio',    file);
      formData.append('text',     text.trim());
      formData.append('language', language);
      formData.append('mode',     mode);

      let submitResp;
      try {
        submitResp = await fetch(`/generate`, { method: 'POST', body: formData });
      } catch (networkErr) {
        throw new Error(
          'Cannot reach the backend. Is it running on port 8000?\n' +
          'Start it:  bash scripts/start-backend.sh  (macOS/Linux)\n' +
          '           scripts\\start-backend.bat       (Windows)'
        );
      }

      if (!submitResp.ok) {
        const body   = await submitResp.json().catch(() => ({}));
        const detail = body.detail || submitResp.statusText;
        if (submitResp.status === 413)
          throw new Error('File too large. Maximum allowed size is 50 MB.');
        if (submitResp.status === 422)
          throw new Error(`Validation error: ${detail}`);
        throw new Error(`Server error ${submitResp.status}: ${detail}`);
      }

      const { job_id } = await submitResp.json();
      if (!job_id) throw new Error('Server returned no job_id.');

      setJobStatus('processing');

      // ── Step 2: poll ────────────────────────────────────────────────────────
      const deadline = Date.now() + POLL_TIMEOUT_MS;
      let jobData = null;

      while (Date.now() < deadline) {
        await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));

        let pollResp;
        try { pollResp = await fetch(`/job/${job_id}`); }
        catch { continue; }

        if (!pollResp.ok) {
          if (pollResp.status === 404)
            throw new Error('Job expired or not found. Please try again.');
          continue;
        }

        jobData = await pollResp.json();
        const status = jobData?.status;
        if (status === 'completed') break;
        if (status === 'failed')
          throw new Error(`Generation failed: ${jobData?.error_message || 'unknown error'}`);
      }

      if (!jobData || jobData.status !== 'completed') {
        throw new Error(
          'Generation timed out after 2 minutes. ' +
          'The EdgeTTS service may be slow or unreachable.'
        );
      }

      // Read new response fields
      setCloningApplied(jobData.cloning_applied ?? false);
      setFallbackUsed(jobData.fallback_used ?? false);
      setProviderName(jobData.provider_name ?? null);

      // ── Step 3: download WAV ────────────────────────────────────────────────
      const outputUrl = jobData.output_url;
      if (!outputUrl) throw new Error('Job completed but no output URL returned.');

      const fullUrl = outputUrl.startsWith('/') ? `${BASE_URL}${outputUrl}` : outputUrl;

      let wavResp;
      try { wavResp = await fetch(fullUrl); }
      catch { throw new Error('Could not download the generated WAV file.'); }

      if (!wavResp.ok)
        throw new Error(`WAV download failed: HTTP ${wavResp.status}`);

      const blob = await wavResp.blob();
      if (!blob.size) throw new Error('Downloaded audio file is empty.');

      const newUrl = URL.createObjectURL(blob);
      blobUrlRef.current = newUrl;
      setAudioUrl(newUrl);
      setJobStatus('completed');

    } catch (err) {
      let msg = err.message || String(err);
      if (msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
        msg = 'Cannot reach the backend. Is it running?\n' +
              'macOS/Linux: bash scripts/start-backend.sh\n' +
              'Windows:     scripts\\start-backend.bat';
      }
      setError(msg);
      setJobStatus('failed');
    } finally {
      setLoading(false);
    }
  };

  // ── Derived state ─────────────────────────────────────────────────────────────
  const textLen    = text.length;
  const overLimit  = textLen > MAX_TEXT_LENGTH;
  const canGenerate = !loading && !!file && text.trim().length > 0 && !overLimit;
  const selectedLang = LANGUAGES.find(l => l.code === language);

  // ── Render ────────────────────────────────────────────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

      {/* Row 1: Voice Sample + Text — responsive side-by-side */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
        gap: '1rem',
      }}>

        {/* ── Voice Sample ─────────────────────────────────────────────────── */}
        <SectionCard title="Voice Sample" icon="🎙️">
          {/* Drop zone */}
          <div
            onClick={() => fileInputRef.current?.click()}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            role="button"
            tabIndex={0}
            onKeyDown={e => e.key === 'Enter' && fileInputRef.current?.click()}
            aria-label="Upload voice sample"
            style={{
              border: `2px dashed ${isDragging ? '#22d3ee' : file ? 'rgba(34,197,94,0.5)' : 'rgba(34,211,238,0.2)'}`,
              borderRadius: '0.5rem',
              padding: '1.25rem',
              textAlign: 'center',
              cursor: 'pointer',
              background: isDragging
                ? 'rgba(34,211,238,0.05)'
                : file
                  ? 'rgba(34,197,94,0.05)'
                  : 'rgba(15,23,42,0.4)',
              transition: 'all 0.2s ease',
              outline: 'none',
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".wav,.mp3,.ogg,.webm,.flac,audio/*"
              onChange={handleFileChange}
              disabled={loading}
              style={{ display: 'none' }}
            />
            {file ? (
              <div>
                <div style={{ fontSize: '1.5rem', marginBottom: '0.25rem' }}>✅</div>
                <div style={{
                  fontSize: '0.8rem', fontWeight: 600, color: '#22c55e',
                  wordBreak: 'break-all',
                }}>{file.name}</div>
                <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: '0.25rem' }}>
                  {(file.size / 1024).toFixed(0)} KB
                </div>
                {!loading && (
                  <div style={{
                    fontSize: '0.65rem', color: 'rgba(34,211,238,0.5)',
                    marginTop: '0.5rem',
                  }}>Click or drop to replace</div>
                )}
              </div>
            ) : (
              <div>
                <div style={{ fontSize: '2rem', marginBottom: '0.25rem', opacity: 0.4 }}>🎵</div>
                <div style={{ fontSize: '0.8rem', color: '#94a3b8', fontWeight: 500 }}>
                  {isDragging ? 'Drop to upload' : 'Drag & drop or click to upload'}
                </div>
                <div style={{ fontSize: '0.7rem', color: '#475569', marginTop: '0.35rem' }}>
                  WAV · MP3 · OGG · WEBM · FLAC &nbsp;·&nbsp; max 50 MB
                </div>
              </div>
            )}
          </div>
        </SectionCard>

        {/* ── Text Input ───────────────────────────────────────────────────── */}
        <SectionCard title="Text to Synthesise" icon="📝">
          <textarea
            id="tts-text"
            rows={5}
            value={text}
            onChange={e => setText(e.target.value)}
            disabled={loading}
            placeholder="Enter the text you want converted to speech…"
            style={{
              width: '100%',
              background: 'rgba(15,23,42,0.6)',
              border: `1px solid ${overLimit ? 'rgba(239,68,68,0.5)' : 'rgba(34,211,238,0.2)'}`,
              borderRadius: '0.5rem',
              padding: '0.75rem',
              color: '#e2e8f0',
              fontSize: '0.875rem',
              resize: 'vertical',
              outline: 'none',
              lineHeight: 1.6,
              boxSizing: 'border-box',
              fontFamily: 'inherit',
              transition: 'border-color 0.2s ease',
            }}
            onFocus={e => {
              e.target.style.borderColor = overLimit
                ? 'rgba(239,68,68,0.8)'
                : 'rgba(34,211,238,0.5)';
            }}
            onBlur={e => {
              e.target.style.borderColor = overLimit
                ? 'rgba(239,68,68,0.5)'
                : 'rgba(34,211,238,0.2)';
            }}
          />
          <CharCounter current={textLen} max={MAX_TEXT_LENGTH} />
        </SectionCard>
      </div>

      {/* Row 2: Language + Mode — side by side */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
        gap: '1rem',
      }}>

        {/* ── Language ─────────────────────────────────────────────────────── */}
        <SectionCard title="Language" icon="🌐">
          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem',
          }}>
            {LANGUAGES.map(lang => (
              <button
                key={lang.code}
                onClick={() => setLanguage(lang.code)}
                disabled={loading}
                style={{
                  display: 'flex', alignItems: 'center', gap: '0.4rem',
                  padding: '0.6rem 0.75rem',
                  borderRadius: '0.5rem',
                  border: language === lang.code
                    ? '1.5px solid rgba(34,211,238,0.7)'
                    : '1px solid rgba(51,65,85,0.8)',
                  background: language === lang.code
                    ? 'rgba(34,211,238,0.08)'
                    : 'rgba(15,23,42,0.4)',
                  color: language === lang.code ? '#22d3ee' : '#94a3b8',
                  fontSize: '0.82rem', fontWeight: language === lang.code ? 600 : 400,
                  cursor: loading ? 'not-allowed' : 'pointer',
                  transition: 'all 0.15s ease',
                  textAlign: 'left',
                }}
              >
                <span style={{ fontSize: '1rem' }}>{lang.flag}</span>
                {lang.label}
                {language === lang.code && (
                  <span style={{ marginLeft: 'auto', fontSize: '0.7rem' }}>✓</span>
                )}
              </button>
            ))}
          </div>
        </SectionCard>

        {/* ── Mode ─────────────────────────────────────────────────────────── */}
        <SectionCard title="Generation Mode" icon="⚙️">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>

            {/* Standard TTS */}
            <button
              onClick={() => setMode('standard')}
              disabled={loading}
              style={modeButtonStyle(mode === 'standard')}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{
                  width: '8px', height: '8px', borderRadius: '50%',
                  background: mode === 'standard' ? '#22d3ee' : '#334155',
                  flexShrink: 0, transition: 'background 0.2s',
                }} />
                <div style={{ textAlign: 'left' }}>
                  <div style={{
                    fontSize: '0.82rem', fontWeight: 600,
                    color: mode === 'standard' ? '#22d3ee' : '#94a3b8',
                  }}>Standard TTS</div>
                  <div style={{ fontSize: '0.68rem', color: '#475569', marginTop: '0.1rem' }}>
                    EdgeTTS Neural — fast &amp; reliable
                  </div>
                </div>
              </div>
            </button>

            {/* Voice Clone */}
            <button
              onClick={() => setMode('clone')}
              disabled={loading}
              style={modeButtonStyle(mode === 'clone')}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <span style={{
                  width: '8px', height: '8px', borderRadius: '50%',
                  background: mode === 'clone' ? '#a78bfa' : '#334155',
                  flexShrink: 0, transition: 'background 0.2s',
                }} />
                <div style={{ textAlign: 'left' }}>
                  <div style={{
                    fontSize: '0.82rem', fontWeight: 600,
                    color: mode === 'clone' ? '#a78bfa' : '#94a3b8',
                  }}>Voice Clone</div>
                  <div style={{ fontSize: '0.68rem', color: '#475569', marginTop: '0.1rem' }}>
                    OpenVoice v2 — fallback to EdgeTTS if not enabled
                  </div>
                </div>
              </div>
            </button>

          </div>
        </SectionCard>
      </div>

      {/* ── Generate button ──────────────────────────────────────────────────── */}
      <button
        onClick={handleGenerate}
        disabled={!canGenerate}
        style={{
          width: '100%',
          padding: '0.9rem 1.5rem',
          borderRadius: '0.75rem',
          border: 'none',
          background: canGenerate
            ? 'linear-gradient(135deg, #06b6d4 0%, #8b5cf6 100%)'
            : 'rgba(30,41,59,0.8)',
          color: canGenerate ? '#0f172a' : '#475569',
          fontSize: '0.95rem',
          fontWeight: 700,
          letterSpacing: '0.05em',
          cursor: canGenerate ? 'pointer' : 'not-allowed',
          transition: 'all 0.2s ease',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '0.5rem',
          boxShadow: canGenerate
            ? '0 4px 20px rgba(6,182,212,0.25), 0 2px 8px rgba(139,92,246,0.15)'
            : 'none',
        }}
      >
        {loading ? (
          <>
            <SpinnerIcon />
            {jobStatus === 'submitting' ? 'Submitting job…' : 'Generating audio…'}
          </>
        ) : (
          <>
            <span style={{ fontSize: '1rem' }}>▶</span>
            Generate Voice
          </>
        )}
      </button>

      {/* ── Progress steps ───────────────────────────────────────────────────── */}
      {(loading || jobStatus === 'completed' || jobStatus === 'failed') && (
        <SectionCard style={{ padding: '1rem 1.25rem' }}>
          <div style={{
            display: 'flex', alignItems: 'center',
            justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem',
          }}>
            <ProgressSteps jobStatus={jobStatus} loading={loading} />
            <StatusBadge
              mode={mode}
              cloningApplied={cloningApplied}
              fallbackUsed={fallbackUsed}
              loading={loading}
              jobStatus={jobStatus}
            />
          </div>
        </SectionCard>
      )}

      {/* ── Error display ────────────────────────────────────────────────────── */}
      {error && (
        <div style={{
          background: 'rgba(239,68,68,0.06)',
          border: '1px solid rgba(239,68,68,0.3)',
          borderRadius: '0.75rem',
          padding: '1rem 1.25rem',
        }}>
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
          }}>
            <span style={{ fontSize: '0.85rem', flexShrink: 0, marginTop: '0.05rem' }}>⚠️</span>
            <div>
              <div style={{
                fontSize: '0.75rem', fontWeight: 700, letterSpacing: '0.08em',
                color: '#ef4444', textTransform: 'uppercase', marginBottom: '0.25rem',
              }}>Error</div>
              <pre style={{
                fontSize: '0.8rem', color: '#fca5a5',
                margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'inherit',
                lineHeight: 1.5,
              }}>{error}</pre>
            </div>
          </div>
        </div>
      )}

      {/* ── Audio output ─────────────────────────────────────────────────────── */}
      {audioUrl && (
        <SectionCard title="Generated Audio" icon="🔊">
          {providerName && (
            <div style={{
              fontSize: '0.7rem', color: '#64748b',
              marginBottom: '0.75rem',
              display: 'flex', gap: '1rem',
            }}>
              <span>Provider: <span style={{ color: '#94a3b8' }}>{providerName}</span></span>
              {fallbackUsed && (
                <span style={{ color: '#f59e0b' }}>⚠ Fallback used</span>
              )}
            </div>
          )}
          <AudioPlayer
            src={audioUrl}
            filename={`${language}_${mode}_output.wav`}
          />
        </SectionCard>
      )}

      {/* Pulse animation keyframe injected once */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.4; transform: scale(0.75); }
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

function modeButtonStyle(selected) {
  return {
    width: '100%', textAlign: 'left', padding: '0.65rem 0.875rem',
    borderRadius: '0.5rem',
    border: selected
      ? '1.5px solid rgba(167,139,250,0.5)'
      : '1px solid rgba(51,65,85,0.8)',
    background: selected ? 'rgba(167,139,250,0.07)' : 'rgba(15,23,42,0.4)',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  };
}

function SpinnerIcon() {
  return (
    <svg
      width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      style={{ animation: 'spin 0.8s linear infinite', flexShrink: 0 }}
    >
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
    </svg>
  );
}
