import { useState, useEffect, useRef } from 'react'
import { apiFetch } from '../api'

const DOC_TYPES = [
  'SPD',
  'Adoption Agreement',
  'Plan Amendment',
  'IRS Determination Letter',
  'Form 5500',
  'IRS Publication',
  'ERISA Regulation',
  'Other',
]

const AUTO = '__auto__'
const STORAGE_KEY = 'pending_ingest'

export default function IngestTab({ onIngestSuccess, initialPlanId, onIngestTokensUsed, refreshKey }) {
  const [tier, setTier] = useState('generic')
  const [employers, setEmployers] = useState([])
  const [plans, setPlans] = useState([])
  const [selectedPlan, setSelectedPlan] = useState('')
  const [docType, setDocType] = useState(AUTO)
  const [detecting, setDetecting] = useState(false)
  const [detectedType, setDetectedType] = useState(null)
  const [effectiveDate, setEffectiveDate] = useState('')
  const [file, setFile] = useState(null)
  const [isDragging, setIsDragging] = useState(false)
  const [status, setStatus] = useState(null)

  const [showNewPlan, setShowNewPlan] = useState(false)
  const [showNewEmployer, setShowNewEmployer] = useState(false)
  const [newPlanName, setNewPlanName] = useState('')
  const [newEmployerName, setNewEmployerName] = useState('')
  const [selectedEmployer, setSelectedEmployer] = useState('')

  const fileRef = useRef()
  const pollRef = useRef(null)
  const logEndRef = useRef()

  // Scroll audit log to bottom when new entries arrive
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [status?.logs?.length])

  useEffect(() => {
    fetchEmployers()
    fetchPlans()
  }, [refreshKey])

  useEffect(() => {
    // Recover any in-progress job that survived a refresh or tab-switch
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      try {
        const { job_id, filename, started_at } = JSON.parse(saved)
        const elapsedSoFar = Math.round((Date.now() - started_at) / 1000)
        setStatus({
          type: 'loading',
          message: `Resuming ingestion of "${filename}"…`,
          logs: [],
        })
        startPolling(job_id, elapsedSoFar)
      } catch {
        localStorage.removeItem(STORAGE_KEY)
      }
    }
  }, [])

  // Pre-select plan when navigating here from the dashboard
  useEffect(() => {
    if (initialPlanId) {
      setSelectedPlan(initialPlanId)
      setTier('plan_doc')
    }
  }, [initialPlanId])

  // Clear poll interval on unmount
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  async function fetchEmployers() {
    try {
      const res = await apiFetch('/api/employers')
      if (res.ok) setEmployers(await res.json())
    } catch {}
  }

  async function fetchPlans() {
    try {
      const res = await apiFetch('/api/plans')
      if (res.ok) setPlans(await res.json())
    } catch {}
  }

  async function handleCreateEmployer() {
    if (!newEmployerName.trim()) return
    try {
      const res = await apiFetch('/api/employers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newEmployerName }),
      })
      if (res.ok) {
        const emp = await res.json()
        await fetchEmployers()
        setSelectedEmployer(emp.employer_id)
        setNewEmployerName('')
        setShowNewEmployer(false)
        onIngestSuccess?.()
      }
    } catch {}
  }

  async function handleCreatePlan() {
    if (!newPlanName.trim() || !selectedEmployer) return
    try {
      const res = await apiFetch('/api/plans', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newPlanName,
          employer_id: selectedEmployer,
          effective_date: effectiveDate || null,
        }),
      })
      if (res.ok) {
        const plan = await res.json()
        await fetchPlans()
        setSelectedPlan(plan.plan_id)
        setNewPlanName('')
        setShowNewPlan(false)
        setShowNewEmployer(false)
        onIngestSuccess?.()
      }
    } catch {}
  }

  async function runAutoDetect(f) {
    if (docType !== AUTO) return
    setDetectedType(null)

    // ── Fast path: keyword check on filename (instant, no API call) ──
    const name = f.name.toLowerCase().replace(/[_\-\.]/g, ' ')
    const KEYWORD_MAP = [
      { type: 'SPD',                      words: ['spd', 'summary plan description', 'summary plan'] },
      { type: 'Form 5500',                words: ['5500', 'form 5500', 'annual report'] },
      { type: 'Adoption Agreement',       words: ['adoption agreement', 'adoption agmt'] },
      { type: 'Plan Amendment',           words: ['amendment', 'amend', 'restatement'] },
      { type: 'IRS Determination Letter', words: ['determination letter', 'determination ltr', 'irs letter'] },
      { type: 'IRS Publication',          words: ['irs publication', 'irs pub', 'pub 590', 'pub 560', 'pub 15'] },
      { type: 'ERISA Regulation',         words: ['erisa', 'dol regulation', 'department of labor'] },
    ]
    for (const { type, words } of KEYWORD_MAP) {
      if (words.some(w => name.includes(w))) {
        setDocType(type)
        setDetectedType(type)
        return  // done — no API call needed
      }
    }

    // ── Slow path: send file to backend only when filename gives no clue ──
    setDetecting(true)
    try {
      const form = new FormData()
      form.append('file', f)
      const res = await apiFetch('/api/detect-doc-type', { method: 'POST', body: form })
      if (res.ok) {
        const data = await res.json()
        setDocType(data.doc_type)
        setDetectedType(data.doc_type)
        if (data.input_tokens || data.output_tokens) {
          onIngestTokensUsed?.(data.input_tokens || 0, data.output_tokens || 0)
        }
      }
    } catch {}
    finally { setDetecting(false) }
  }

  function handleDrop(e) {
    e.preventDefault()
    setIsDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) { setFile(f); runAutoDetect(f) }
  }

  function startPolling(jobId, startElapsed = 0) {
    let elapsed = startElapsed
    pollRef.current = setInterval(async () => {
      elapsed += 5
      try {
        const res = await apiFetch(`/api/ingest/status/${jobId}`)

        if (res.status === 404) {
          clearInterval(pollRef.current)
          localStorage.removeItem(STORAGE_KEY)
          setStatus({
            type: 'error',
            message: 'Ingestion was interrupted — the server was restarted mid-job. Please try again.',
            logs: [],
          })
          return
        }

        if (!res.ok) return
        const job = await res.json()

        if (job.status === 'done') {
          clearInterval(pollRef.current)
          localStorage.removeItem(STORAGE_KEY)
          setStatus({
            type: 'success',
            message: `Successfully ingested "${job.filename}"`,
            stats: job.stats,
            logs: job.logs || [],
          })
          setFile(null)
          setDocType(AUTO)
          setDetectedType(null)
          setEffectiveDate('')
          if (fileRef.current) fileRef.current.value = ''
          onIngestTokensUsed?.(job.stats?.api_input_tokens || 0, job.stats?.api_output_tokens || 0)
          onIngestSuccess?.()
        } else if (job.status === 'error') {
          clearInterval(pollRef.current)
          localStorage.removeItem(STORAGE_KEY)
          setStatus({
            type: 'error',
            message: job.message || 'Ingestion failed.',
            logs: job.logs || [],
          })
        } else {
          // Still processing — update elapsed and merge latest logs
          setStatus(prev => ({
            ...prev,
            type: 'loading',
            message: `Ingesting document — ${elapsed}s elapsed (large PDFs take 1–3 minutes)…`,
            logs: job.logs || prev?.logs || [],
          }))
        }
      } catch {}
    }, 5000)
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!file) {
      setStatus({ type: 'error', message: 'Please select a file.', logs: [] })
      return
    }
    if (tier === 'plan_doc' && !selectedPlan) {
      setStatus({ type: 'error', message: 'Please select or create a plan.', logs: [] })
      return
    }
    if (docType === AUTO) {
      setStatus({ type: 'error', message: 'Document type could not be detected. Please select it manually.', logs: [] })
      return
    }

    if (pollRef.current) clearInterval(pollRef.current)

    setStatus({ type: 'loading', message: 'Uploading file…', logs: [] })

    const form = new FormData()
    form.append('file', file)
    form.append('tier', tier)
    form.append('doc_type', docType)
    if (tier === 'plan_doc') form.append('plan_id', selectedPlan)
    if (effectiveDate) form.append('effective_date', effectiveDate)

    try {
      const res = await apiFetch('/api/ingest', { method: 'POST', body: form })
      const data = await res.json()

      if (!res.ok) {
        setStatus({ type: 'error', message: data.detail || 'Ingestion failed.', logs: [] })
        return
      }

      if (data.status === 'duplicate') {
        setStatus({ type: 'duplicate', message: data.message, logs: [] })
        return
      }

      if (data.status === 'processing') {
        // Persist job info so a refresh or tab-switch can resume polling
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          job_id: data.job_id,
          filename: data.filename,
          started_at: Date.now(),
        }))
        setStatus({
          type: 'loading',
          message: 'Ingesting document — 0s elapsed (large PDFs take 1–3 minutes)…',
          logs: [],
        })
        startPolling(data.job_id)
      }
    } catch (err) {
      setStatus({ type: 'error', message: err.message, logs: [] })
    }
  }

  const plansByEmployer = employers
    .map(emp => ({
      ...emp,
      plans: plans.filter(p => p.employer_id === emp.employer_id),
    }))
    .filter(emp => emp.plans.length > 0)

  const isSubmitting = status?.type === 'loading'

  return (
    <div className="ingest-tab">
      <div className="ingest-form-card">

        <div className="ingest-card-header">
          <div className="ingest-card-header-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
          </div>
          <div className="ingest-card-header-text">
            <h2>Ingest Document</h2>
            <p>Upload and index a retirement plan document into the knowledge base</p>
          </div>
        </div>

        <div className="ingest-form-body">
        <form onSubmit={handleSubmit}>

          {/* Tier */}
          <div className="form-group">
            <label className="form-label">Document Tier</label>
            <div className="radio-group">
              <label className="radio-label">
                <input
                  type="radio"
                  value="generic"
                  checked={tier === 'generic'}
                  onChange={e => setTier(e.target.value)}
                />
                <span className="radio-content">
                  <span className="radio-title">Generic / Regulatory</span>
                  <span className="radio-desc">IRS Publications, ERISA Regulations — not tied to a specific plan</span>
                </span>
              </label>
              <label className="radio-label">
                <input
                  type="radio"
                  value="plan_doc"
                  checked={tier === 'plan_doc'}
                  onChange={e => setTier(e.target.value)}
                />
                <span className="radio-content">
                  <span className="radio-title">Plan-Specific</span>
                  <span className="radio-desc">SPD, Form 5500, Adoption Agreement, Amendment, IRS Determination Letter</span>
                </span>
              </label>
            </div>
          </div>

          {/* Plan selection */}
          {tier === 'plan_doc' && (
            <div className="form-group">
              <label className="form-label">Plan</label>

              {plansByEmployer.length > 0 && !showNewPlan ? (
                <div className="plan-select-row">
                  <select
                    className="form-select"
                    value={selectedPlan}
                    onChange={e => setSelectedPlan(e.target.value)}
                  >
                    <option value="">Select a plan...</option>
                    {plansByEmployer.map(emp => (
                      <optgroup key={emp.employer_id} label={emp.employer_name}>
                        {emp.plans.map(p => (
                          <option key={p.plan_id} value={p.plan_id}>
                            {p.plan_name}
                          </option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="btn-secondary-sm"
                    onClick={() => setShowNewPlan(true)}
                  >
                    + New Plan
                  </button>
                </div>
              ) : (
                <div className="new-plan-form">
                  <p className="form-hint">Create a new plan</p>

                  {employers.length > 0 && !showNewEmployer ? (
                    <div className="plan-select-row" style={{ marginBottom: 8 }}>
                      <select
                        className="form-select"
                        value={selectedEmployer}
                        onChange={e => setSelectedEmployer(e.target.value)}
                      >
                        <option value="">Select employer...</option>
                        {employers.map(emp => (
                          <option key={emp.employer_id} value={emp.employer_id}>
                            {emp.employer_name}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        className="btn-secondary-sm"
                        onClick={() => setShowNewEmployer(true)}
                      >
                        + New Employer
                      </button>
                    </div>
                  ) : (
                    <div className="inline-create" style={{ marginBottom: 8 }}>
                      <input
                        className="form-input"
                        placeholder="Employer name..."
                        value={newEmployerName}
                        onChange={e => setNewEmployerName(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && e.preventDefault()}
                      />
                      <button
                        type="button"
                        className="btn-create"
                        onClick={handleCreateEmployer}
                        disabled={!newEmployerName.trim()}
                      >
                        Create
                      </button>
                      {employers.length > 0 && (
                        <button type="button" className="btn-link" onClick={() => setShowNewEmployer(false)}>
                          Cancel
                        </button>
                      )}
                    </div>
                  )}

                  <div className="inline-create">
                    <input
                      className="form-input"
                      placeholder="Plan name..."
                      value={newPlanName}
                      onChange={e => setNewPlanName(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && e.preventDefault()}
                    />
                    <button
                      type="button"
                      className="btn-create"
                      onClick={handleCreatePlan}
                      disabled={!selectedEmployer || !newPlanName.trim()}
                    >
                      Create Plan
                    </button>
                    {plansByEmployer.length > 0 && (
                      <button
                        type="button"
                        className="btn-link"
                        onClick={() => { setShowNewPlan(false); setShowNewEmployer(false) }}
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Doc Type */}
          <div className="form-group">
            <label className="form-label">
              Document Type
              {detecting && <span className="detecting-badge">Detecting…</span>}
              {!detecting && detectedType && docType === detectedType && (
                <span className="detected-badge">Auto-detected</span>
              )}
            </label>
            <select
              className="form-select"
              value={docType}
              onChange={e => { setDocType(e.target.value); setDetectedType(null) }}
              disabled={detecting}
            >
              <option value={AUTO}>Auto-detect (recommended)</option>
              <option disabled>──────────</option>
              {DOC_TYPES.map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <div className="tier-doctype-hint">
              {tier === 'generic'
                ? 'Typical for this tier: IRS Publication, ERISA Regulation'
                : 'Typical for this tier: SPD, Form 5500, Adoption Agreement, Plan Amendment, IRS Determination Letter'}
            </div>
          </div>

          {/* Effective Date */}
          <div className="form-group">
            <label className="form-label">
              Effective Date <span className="optional">(optional)</span>
            </label>
            <input
              type="date"
              className="form-input"
              value={effectiveDate}
              onChange={e => setEffectiveDate(e.target.value)}
            />
          </div>

          {/* File Upload */}
          <div className="form-group">
            <label className="form-label">File</label>
            <div
              className={`drop-zone${isDragging ? ' dragging' : ''}${file ? ' has-file' : ''}`}
              onDragOver={e => { e.preventDefault(); setIsDragging(true) }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileRef.current?.click()}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".pdf,.docx,.xlsx,.xls"
                style={{ display: 'none' }}
                onChange={e => {
                  const f = e.target.files[0] || null
                  setFile(f)
                  if (f) runAutoDetect(f)
                }}
              />
              {file ? (
                <div className="file-chosen">
                  <div className="file-icon-wrap">📄</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="file-name">{file.name}</div>
                    <div className="file-size">{(file.size / 1024).toFixed(1)} KB</div>
                  </div>
                  <button
                    type="button"
                    className="remove-file"
                    onClick={e => {
                      e.stopPropagation()
                      setFile(null)
                      setDocType(AUTO)
                      setDetectedType(null)
                    }}
                  >
                    ×
                  </button>
                </div>
              ) : (
                <div className="drop-prompt">
                  <div className="upload-icon-wrap">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="16 16 12 12 8 16"/>
                      <line x1="12" y1="12" x2="12" y2="21"/>
                      <path d="M20.39 18.39A5 5 0 0018 9h-1.26A8 8 0 103 16.3"/>
                    </svg>
                  </div>
                  <div className="drop-title">Drop your file here</div>
                  <div className="drop-subtitle">or click to browse</div>
                  <div className="drop-formats">
                    <span className="drop-format-chip">PDF</span>
                    <span className="drop-format-chip">DOCX</span>
                    <span className="drop-format-chip">XLSX</span>
                  </div>
                </div>
              )}
            </div>
          </div>

          <button type="submit" className="btn-primary ingest-submit-btn" disabled={isSubmitting}>
            {isSubmitting ? (
              <>Processing…</>
            ) : (
              <>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 16, height: 16, marginRight: 8, verticalAlign: 'middle' }}>
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                  <polyline points="17 8 12 3 7 8"/>
                  <line x1="12" y1="3" x2="12" y2="15"/>
                </svg>
                Ingest Document
              </>
            )}
          </button>
        </form>

        {/* Status + Audit Log */}
        {status && (
          <div className={`status-box status-${status.type}`}>
            <div className="status-header">
              <div className="status-message">{status.message}</div>
              {status.type === 'loading' && (
                <div className="progress-dots">
                  <span className="dot" /><span className="dot" /><span className="dot" />
                </div>
              )}
            </div>

            {/* Audit log — visible while processing and after completion */}
            {status.logs?.length > 0 && (
              <div className="audit-log">
                {status.logs.map((entry, i) => (
                  <div key={i} className="log-entry">
                    <span className="log-ts">{entry.ts}</span>
                    <span className="log-msg">{entry.msg}</span>
                  </div>
                ))}
                <div ref={logEndRef} />
              </div>
            )}

            {status.stats && (
              <div className="status-stats">
                <span>Blocks extracted: {status.stats.blocks_extracted}</span>
                <span>Chunks created: {status.stats.chunks_created}</span>
                <span>Vectors stored: {status.stats.vector_stored}</span>
                <span>Avg tokens/chunk: {status.stats.token_avg}</span>
                {status.stats.api_input_tokens > 0 && (
                  <span
                    className="stat-tokens"
                    title={`${status.stats.api_input_tokens.toLocaleString()} input · ${status.stats.api_output_tokens.toLocaleString()} output`}
                  >
                    API tokens: {(status.stats.api_input_tokens + status.stats.api_output_tokens).toLocaleString()}
                  </span>
                )}
              </div>
            )}
          </div>
        )}
        </div>{/* ingest-form-body */}
      </div>
    </div>
  )
}
