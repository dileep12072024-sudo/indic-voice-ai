import React, { useState, useRef, useEffect, useCallback } from 'react';
import AudioPlayer from './AudioPlayer';

/**
 * VoiceCloner.jsx — Standard TTS + Voice Clone UI component.
 *
 * Fixes in this revision
 * ──────────────────────
 * FIX-3: Client-side file size check (50 MB) before multipart upload.
 *        User gets an instant error instead of waiting 30 s+ for a server 413.
 * FIX-4: Blob URL revocation on each new generation and on component unmount
 *        to prevent accumulating memory-leaked object URLs.
 */

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB — must match backend
const POLL_INTERVAL_MS   = 2000;
const POLL_TIMEOUT_MS    = 120_000;

const LANGUAGES = [
  { code: 'te', label: 'Telugu' },
  { code: 'ta', label: 'Tamil' },
  { code: 'hi', label: 'Hindi' },
  { code: 'en', label: 'English' },
];

// Resolve backend base URL from Vite env — Vite proxy handles routing in dev.
const BASE_URL =
  (import.meta.env.VITE_WORKERS_URL || import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');

export default function VoiceCloner() {
  const [file,        setFile]        = useState(null);
  const [text,        setText]        = useState('');
  const [language,    setLanguage]    = useState('en');
  const [mode,        setMode]        = useState('standard');
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState(null);
  const [audioUrl,    setAudioUrl]    = useState(null);
  const [jobStatus,   setJobStatus]   = useState(null);

  // FIX-4: keep a ref to the current blob URL so we can revoke it
  const blobUrlRef = useRef(null);

  // FIX-4: revoke blob URL on unmount
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

  const handleFileChange = (e) => {
    const selected = e.target.files[0];
    if (!selected) return;

    // FIX-3: client-side file size check — instant feedback, no wasted upload
    if (selected.size > MAX_FILE_SIZE_BYTES) {
      setError(
        `File too large: ${(selected.size / 1024 / 1024).toFixed(1)} MB. ` +
        `Maximum allowed size is 50 MB.`
      );
      e.target.value = '';
      return;
    }

    setFile(selected);
    setError(null);
  };

  const handleGenerate = async () => {
    if (!file) { setError('Please upload a voice sample first.'); return; }
    if (!text.trim()) { setError('Please enter text to synthesise.'); return; }
    if (text.trim().length > 500) {
      setError(`Text too long: ${text.trim().length} characters. Maximum is 500.`);
      return;
    }

    setLoading(true);
    setError(null);
    setJobStatus('submitting');

    // FIX-4: revoke previous blob URL before creating a new one
    revokePreviousBlob();
    setAudioUrl(null);

    try {
      // ── Step 1: submit job ────────────────────────────────────────────
      const formData = new FormData();
      formData.append('audio',    file);
      formData.append('text',     text.trim());
      formData.append('language', language);
      formData.append('mode',     mode);

      let submitResp;
      try {
        submitResp = await fetch(`${BASE_URL}/generate`, {
          method: 'POST',
          body: formData,
        });
      } catch (networkErr) {
        throw new Error(
          'Cannot reach the backend. ' +
          'Is it running on port 8000? Start it with: bash scripts/start-backend.sh'
        );
      }

      if (!submitResp.ok) {
        const body = await submitResp.json().catch(() => ({}));
        const detail = body.detail || submitResp.statusText;
        if (submitResp.status === 413) {
          throw new Error('File too large. Maximum allowed size is 50 MB.');
        }
        if (submitResp.status === 422) {
          throw new Error(`Validation error: ${detail}`);
        }
        throw new Error(`Server error ${submitResp.status}: ${detail}`);
      }

      const { job_id } = await submitResp.json();
      if (!job_id) throw new Error('Server returned no job_id.');

      setJobStatus('processing');

      // ── Step 2: poll ──────────────────────────────────────────────────
      const deadline = Date.now() + POLL_TIMEOUT_MS;
      let jobData = null;

      while (Date.now() < deadline) {
        await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));

        let pollResp;
        try {
          pollResp = await fetch(`${BASE_URL}/job/${job_id}`);
        } catch {
          continue; // transient network hiccup — keep polling
        }

        if (!pollResp.ok) {
          if (pollResp.status === 404) {
            throw new Error('Job expired or not found. Please try again.');
          }
          continue;
        }

        jobData = await pollResp.json();
        const status = jobData?.status;

        if (status === 'completed') break;
        if (status === 'failed') {
          throw new Error(
            `Generation failed: ${jobData.error_message || 'unknown error'}`
          );
        }
      }

      if (!jobData || jobData.status !== 'completed') {
        throw new Error(
          'Generation timed out after 2 minutes. ' +
          'The EdgeTTS service may be slow or unreachable.'
        );
      }

      // ── Step 3: download WAV ──────────────────────────────────────────
      const outputUrl = jobData.output_url;
      if (!outputUrl) throw new Error('Job completed but no output URL returned.');

      const fullUrl = outputUrl.startsWith('/')
        ? `${BASE_URL}${outputUrl}`
        : outputUrl;

      let wavResp;
      try {
        wavResp = await fetch(fullUrl);
      } catch {
        throw new Error('Could not download the generated WAV file.');
      }

      if (!wavResp.ok) {
        throw new Error(`WAV download failed: HTTP ${wavResp.status}`);
      }

      const blob = await wavResp.blob();
      if (!blob.size) throw new Error('Downloaded audio file is empty.');

      // FIX-4: store the new blob URL in the ref so it can be revoked later
      const newUrl = URL.createObjectURL(blob);
      blobUrlRef.current = newUrl;
      setAudioUrl(newUrl);
      setJobStatus('completed');

    } catch (err) {
      // Friendly error rewrites
      let msg = err.message || String(err);
      if (msg.includes('Failed to fetch') || msg.includes('NetworkError')) {
        msg = 'Cannot reach the backend. Is it running? Try: bash scripts/start-backend.sh';
      }
      setError(msg);
      setJobStatus('failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="voice-cloner">
      {/* Mode selector */}
      <div className="mode-selector">
        <label>
          <input
            type="radio" value="standard" name="mode"
            checked={mode === 'standard'}
            onChange={() => setMode('standard')}
          />
          {' '}Standard TTS
        </label>
        <label style={{ marginLeft: '1.5rem' }}>
          <input
            type="radio" value="clone" name="mode"
            checked={mode === 'clone'}
            onChange={() => setMode('clone')}
          />
          {' '}Voice Clone
          {mode === 'clone' && (
            <span style={{ marginLeft: 8, fontSize: '0.8em', color: '#888' }}>
              (fallback to Standard TTS unless OpenVoice is enabled)
            </span>
          )}
        </label>
      </div>

      {/* Language selector */}
      <div className="language-selector" style={{ margin: '1rem 0' }}>
        <label htmlFor="lang-select">Language: </label>
        <select
          id="lang-select"
          value={language}
          onChange={e => setLanguage(e.target.value)}
        >
          {LANGUAGES.map(l => (
            <option key={l.code} value={l.code}>{l.label}</option>
          ))}
        </select>
      </div>

      {/* File upload */}
      <div className="file-upload" style={{ margin: '1rem 0' }}>
        <label htmlFor="audio-upload">Voice sample (WAV/MP3/OGG/WEBM/FLAC, max 50 MB): </label>
        <input
          id="audio-upload"
          type="file"
          accept=".wav,.mp3,.ogg,.webm,.flac,audio/*"
          onChange={handleFileChange}
          disabled={loading}
        />
        {file && (
          <span style={{ marginLeft: 8, fontSize: '0.85em', color: '#555' }}>
            {file.name} ({(file.size / 1024).toFixed(0)} KB)
          </span>
        )}
      </div>

      {/* Text input */}
      <div className="text-input" style={{ margin: '1rem 0' }}>
        <label htmlFor="tts-text">Text to synthesise (max 500 characters): </label>
        <br />
        <textarea
          id="tts-text"
          rows={4}
          style={{ width: '100%', maxWidth: 600, marginTop: 4 }}
          value={text}
          onChange={e => setText(e.target.value)}
          disabled={loading}
          placeholder="Enter text here…"
        />
        <div style={{ fontSize: '0.8em', color: text.length > 500 ? 'red' : '#888' }}>
          {text.length} / 500
        </div>
      </div>

      {/* Generate button */}
      <button
        onClick={handleGenerate}
        disabled={loading || !file || !text.trim() || text.length > 500}
        style={{ marginTop: '0.5rem' }}
      >
        {loading ? `Generating… (${jobStatus || 'waiting'})` : 'Generate'}
      </button>

      {/* Error display */}
      {error && (
        <div className="error" style={{ marginTop: '1rem', color: 'red' }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* Audio player */}
      {audioUrl && (
        <div style={{ marginTop: '1.5rem' }}>
          <AudioPlayer src={audioUrl} />
        </div>
      )}
    </div>
  );
}
