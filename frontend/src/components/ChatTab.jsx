import { useState, useEffect, useRef } from 'react'
import { apiFetch } from '../api'
import Markdown from 'react-markdown'

export default function ChatTab({ initialPlanId = '', role = 'plan_consultant', onTokensUsed, refreshKey }) {
  const storageKey = `chat_msgs_${role}`

  const [plans, setPlans]           = useState([])
  const [genericDocs, setGenericDocs] = useState([])
  const [selectedPlan, setSelectedPlan] = useState(initialPlanId)
  const [planFacts, setPlanFacts]   = useState(null)
  const [factsOpen, setFactsOpen]   = useState(false)
  const [input, setInput]           = useState('')
  const [isLoading, setIsLoading]   = useState(false)

  // Role-keyed conversation history
  const [messages, setMessages] = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '[]') }
    catch { return [] }
  })

  const bottomRef = useRef()
  const inputRef  = useRef()

  // Persist messages to localStorage whenever they change
  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify(messages))
  }, [messages, storageKey])

  // Pre-select plan when navigating here from dashboard
  useEffect(() => {
    if (initialPlanId) setSelectedPlan(initialPlanId)
  }, [initialPlanId])

  useEffect(() => {
    apiFetch('/api/registry')
      .then(r => r.ok ? r.json() : {})
      .then(data => {
        setPlans(data.plans || [])
        setGenericDocs(data.generic_documents || [])
      })
      .catch(() => {})
  }, [refreshKey])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isLoading])

  const isGenericOnly  = selectedPlan === '__generic__'
  const selectedPlanObj = isGenericOnly ? null : plans.find(p => p.plan_id === selectedPlan)

  // Fetch plan facts when plan changes
  useEffect(() => {
    if (!selectedPlanObj) {
      setFactsOpen(false)
      const t = setTimeout(() => setPlanFacts(null), 350)
      return () => clearTimeout(t)
    }
    apiFetch(`/api/plans/${selectedPlanObj.plan_id}/rules`)
      .then(r => r.ok ? r.json() : null)
      .then(setPlanFacts)
      .catch(() => setPlanFacts(null))
  }, [selectedPlanObj?.plan_id])

  async function sendMessage(e) {
    e?.preventDefault()
    const question = input.trim()
    if (!question || isLoading) return

    setMessages(prev => [...prev, { role: 'user', text: question }])
    setInput('')
    setIsLoading(true)

    try {
      const res = await apiFetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          plan_id:   selectedPlanObj?.plan_id   || null,
          plan_name: selectedPlanObj?.plan_name  || null,
          scope:     isGenericOnly ? 'generic_only' : null,
        }),
      })
      const data = await res.json()

      if (!res.ok) {
        setMessages(prev => [...prev, { role: 'error', text: data.detail || 'Query failed.' }])
      } else {
        setMessages(prev => [...prev, { role: 'assistant', ...data }])
        onTokensUsed?.(data.input_tokens || 0, data.output_tokens || 0)
      }
    } catch (err) {
      setMessages(prev => [...prev, { role: 'error', text: err.message }])
    } finally {
      setIsLoading(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function setExampleQuestion(q) {
    setInput(q)
    inputRef.current?.focus()
  }

  return (
    <div className="chat-tab">
      <div className={`chat-outer${factsOpen ? ' facts-open' : ''}`}>

        {/* Left sidebar — plan facts */}
        {planFacts && (
          <aside className={`facts-sidebar${factsOpen ? ' open' : ''}`}>
            <PlanFactsPanel
              facts={planFacts}
              planName={selectedPlanObj?.plan_name}
              onClose={() => setFactsOpen(false)}
            />
          </aside>
        )}

        <div className="chat-layout">

          {/* Header */}
          <div className="chat-header">
            <div className="chat-header-inner">
              <div className="chat-header-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" fill="rgba(255,255,255,0.15)"/>
                  <polyline points="8.5 13.5 10.5 10.5 12.5 12.5 15.5 8.5"/>
                </svg>
              </div>

              <div className="plan-picker">
                <label>Plan context</label>
                <select
                  className="plan-select"
                  value={selectedPlan}
                  onChange={e => setSelectedPlan(e.target.value)}
                >
                  <option value="">No specific context — search everything</option>
                  <option value="__generic__">General regulations only</option>
                  {plans.length > 0 && <option disabled>──────────────</option>}
                  {plans.map(p => (
                    <option key={p.plan_id} value={p.plan_id}>
                      {p.plan_name}{p.employer_name ? ` — ${p.employer_name}` : ''}
                    </option>
                  ))}
                </select>
              </div>

              <div className="header-actions">
                {selectedPlanObj && planFacts && (
                  <button
                    className={`show-facts-btn${factsOpen ? ' active' : ''}`}
                    onClick={() => setFactsOpen(o => !o)}
                  >
                    {factsOpen ? 'Hide Facts' : 'Plan Facts'}
                  </button>
                )}
                {messages.length > 0 && (
                  <button className="show-facts-btn" onClick={() => setMessages([])}>
                    Clear
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Scope bar */}
          <SearchScopeBar
            selectedPlanObj={selectedPlanObj}
            genericDocs={genericDocs}
            isGenericOnly={isGenericOnly}
          />

          {/* Messages */}
          <div className="chat-messages">
            {messages.length === 0 && !isLoading ? (
              <div className="chat-empty">
                <div className="empty-icon-wrap">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
                    <line x1="9" y1="10" x2="15" y2="10"/>
                    <line x1="9" y1="14" x2="13" y2="14"/>
                  </svg>
                </div>
                <p className="empty-title">Ask a Question</p>
                <p>
                  {selectedPlanObj
                    ? <>Ask anything about <strong>{selectedPlanObj.plan_name}</strong> or general 401(k) rules.</>
                    : isGenericOnly
                    ? <>Ask anything about IRS rules, ERISA regulations, or general 401(k) concepts.</>
                    : <>Ask anything about retirement plans, 401(k) rules, or ingested documents.</>
                  }
                </p>
                <div className="example-questions">
                  <p>Try asking:</p>
                  <button className="example-q" onClick={() => setExampleQuestion('What are the 401k contribution limits?')}>What are the 401k contribution limits?</button>
                  <button className="example-q" onClick={() => setExampleQuestion('What is the vesting schedule for employer contributions?')}>What is the vesting schedule for employer contributions?</button>
                  <button className="example-q" onClick={() => setExampleQuestion('Explain early withdrawal penalties')}>Explain early withdrawal penalties</button>
                  <button className="example-q" onClick={() => setExampleQuestion('What are the eligibility requirements to participate?')}>What are the eligibility requirements to participate?</button>
                </div>
              </div>
            ) : (
              <>
                {messages.map((msg, i) => (
                  <MessageBubble key={i} msg={msg} />
                ))}
                {isLoading && (
                  <div className="message message-assistant">
                    <div className="ai-avatar">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"/>
                      </svg>
                    </div>
                    <div className="message-bubble loading-bubble">
                      <span className="dot"/><span className="dot"/><span className="dot"/>
                    </div>
                  </div>
                )}
              </>
            )}
            <div ref={bottomRef}/>
          </div>

          {/* Input */}
          <form className="chat-input-area" onSubmit={sendMessage}>
            <textarea
              ref={inputRef}
              className="chat-input"
              placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
              }}
              rows={2}
              disabled={isLoading}
            />
            <button type="submit" className="send-btn" disabled={isLoading || !input.trim()}>
              {isLoading ? (
                <>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10"/>
                    <path d="M12 6v6l4 2"/>
                  </svg>
                  Searching…
                </>
              ) : (
                <>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="22" y1="2" x2="11" y2="13"/>
                    <polygon points="22 2 15 22 11 13 2 9 22 2"/>
                  </svg>
                  Send
                </>
              )}
            </button>
          </form>

        </div>
      </div>
    </div>
  )
}


// ── Search Scope Bar ──────────────────────────────────────────────────────────

function SearchScopeBar({ selectedPlanObj, genericDocs, isGenericOnly }) {
  const planDocs = selectedPlanObj?.documents || []

  if (isGenericOnly) {
    return (
      <div className="scope-bar">
        <span className="scope-label">Searching:</span>
        {genericDocs.length > 0
          ? genericDocs.map(doc => (
              <span key={doc.doc_id} className="scope-chip scope-generic">
                📋 {doc.filename}
                <span className="scope-meta">{doc.doc_type} · {doc.chunk_count} chunks</span>
              </span>
            ))
          : <span className="scope-chip scope-generic">📋 Generic/regulatory docs</span>
        }
        <span className="scope-chip scope-excluded">plan docs excluded</span>
      </div>
    )
  }

  return (
    <div className="scope-bar">
      <span className="scope-label">Searching:</span>
      {selectedPlanObj ? (
        <>
          {planDocs.map(doc => (
            <span key={doc.doc_id} className="scope-chip scope-plan">
              📄 {doc.filename}
              <span className="scope-meta">{doc.doc_type} · {doc.chunk_count} chunks</span>
            </span>
          ))}
          <span className="scope-separator">+</span>
        </>
      ) : (
        <span className="scope-chip scope-all">
          🔍 All plan documents
          <span className="scope-meta">across all plans</span>
        </span>
      )}
      {genericDocs.length > 0
        ? genericDocs.map(doc => (
            <span key={doc.doc_id} className="scope-chip scope-generic">
              📋 {doc.filename}
              <span className="scope-meta">{doc.doc_type} · {doc.chunk_count} chunks · included when relevant</span>
            </span>
          ))
        : (
            <span className="scope-chip scope-generic">
              📋 Generic/regulatory docs
              <span className="scope-meta">included when relevant</span>
            </span>
          )
      }
    </div>
  )
}


// ── Plan Facts Panel ──────────────────────────────────────────────────────────

function PlanFactsPanel({ facts, planName, onClose }) {
  const f          = facts.features || {}
  const match      = facts.employer_match
  const vesting    = facts.vesting?.employer_match
  const eligibility = facts.eligibility
  const nonelective = facts.nonelective_contribution

  const featureList = [
    { label: 'Auto-enrollment',       on: f.auto_enrollment },
    { label: 'Roth 401(k)',           on: f.roth_contributions },
    { label: 'Loans',                 on: f.loan_provision },
    { label: 'Hardship withdrawal',   on: f.hardship_withdrawal },
    { label: 'After-tax contributions', on: f.after_tax_contributions },
  ]

  return (
    <div className="facts-panel-inner">
      <div className="facts-panel-header">
        <div>
          <div className="facts-panel-title">Plan Facts</div>
          {planName && <div className="facts-panel-subtitle">{planName}</div>}
        </div>
        <button className="facts-close-btn" onClick={onClose} title="Close">✕</button>
      </div>

      <div className="facts-panel-body">
        <div className="facts-meta-row">
          {facts.plan_type     && <span className="facts-meta-item"><strong>Type</strong>{facts.plan_type}</span>}
          {facts.record_keeper && <span className="facts-meta-item"><strong>Recordkeeper</strong>{facts.record_keeper}</span>}
          {facts.plan_year_end && <span className="facts-meta-item"><strong>Year end</strong>{facts.plan_year_end}</span>}
        </div>

        <div className="facts-features">
          {featureList.map(feat => (
            <span key={feat.label} className={`feature-chip ${feat.on ? 'feat-on' : 'feat-off'}`}>
              {feat.on ? '✓' : '✗'} {feat.label}
            </span>
          ))}
        </div>

        {match?.formula_readable && (
          <div className="facts-section">
            <div className="facts-section-title">Employer Match</div>
            <div className="facts-section-text">{match.formula_readable}</div>
          </div>
        )}
        {vesting?.formula_readable && (
          <div className="facts-section">
            <div className="facts-section-title">Vesting</div>
            <div className="facts-section-text">{vesting.formula_readable}</div>
          </div>
        )}
        {eligibility?.formula_readable && (
          <div className="facts-section">
            <div className="facts-section-title">Eligibility</div>
            <div className="facts-section-text">{eligibility.formula_readable}</div>
          </div>
        )}
        {nonelective?.available && nonelective?.formula_readable && (
          <div className="facts-section">
            <div className="facts-section-title">Company Contribution</div>
            <div className="facts-section-text">{nonelective.formula_readable}</div>
          </div>
        )}

        <div className="facts-extracted-note">
          Extracted from SPD on {facts.extracted_at}
        </div>
      </div>
    </div>
  )
}


// ── Message Bubble ────────────────────────────────────────────────────────────

function MessageBubble({ msg }) {
  const [showSources, setShowSources] = useState(false)
  const [copied, setCopied]           = useState(false)
  const [feedback, setFeedback]       = useState(null)

  function handleCopy() {
    const text = msg.answer || msg.text || ''
    navigator.clipboard.writeText(text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  if (msg.role === 'user') {
    return (
      <div className="message message-user">
        <div className="message-bubble">{msg.text}</div>
      </div>
    )
  }

  if (msg.role === 'error') {
    return (
      <div className="message message-error">
        <div className="message-bubble error-bubble">Error: {msg.text}</div>
      </div>
    )
  }

  const confidenceClass = {
    High:   'confidence-high',
    Medium: 'confidence-medium',
    Low:    'confidence-low',
  }[msg.confidence] || 'confidence-low'

  return (
    <div className="message message-assistant">
      <div className="ai-avatar">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z"/>
        </svg>
      </div>
      <div className="message-bubble assistant-bubble">

        <div className="answer-text">
          <Markdown>{msg.answer}</Markdown>
        </div>

        {/* Meta row */}
        <div className="message-meta">
          {msg.confidence && (
            <span className={`confidence-badge ${confidenceClass}`}>
              {msg.confidence} confidence{msg.confidence_pct != null ? ` · ${msg.confidence_pct}%` : ''}
            </span>
          )}
          {msg.tool_calls_made > 0 && (
            <span className="tool-calls-badge">
              {msg.tool_calls_made} {msg.tool_calls_made === 1 ? 'search' : 'searches'}
            </span>
          )}
          {msg.input_tokens != null && (
            <span
              className="token-badge"
              title={`${msg.input_tokens.toLocaleString()} input · ${msg.output_tokens.toLocaleString()} output`}
            >
              {(msg.input_tokens + msg.output_tokens).toLocaleString()} tokens
            </span>
          )}
          {msg.sources?.length > 0 && (
            <button className="sources-toggle" onClick={() => setShowSources(s => !s)}>
              {showSources ? 'Hide' : 'Show'} sources ({msg.sources.length})
            </button>
          )}
        </div>

        {showSources && msg.sources?.length > 0 && (
          <div className="sources-list">
            {msg.sources.map((s, i) => (
              <div key={i} className="source-item">{s}</div>
            ))}
          </div>
        )}

        {msg.confidence_reason && (
          <div className="confidence-reason">{msg.confidence_reason}</div>
        )}

        {/* Action bar: copy + thumbs */}
        <div className="msg-action-bar">
          <button className={`msg-action-btn${copied ? ' msg-copied' : ''}`} onClick={handleCopy}>
            {copied ? (
              <>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
                Copied
              </>
            ) : (
              <>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2"/>
                  <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                </svg>
                Copy
              </>
            )}
          </button>

          <div className="msg-thumb-group">
            <button
              className={`msg-thumb-btn${feedback === 'up' ? ' thumb-up-active' : ''}`}
              onClick={() => setFeedback(f => f === 'up' ? null : 'up')}
              title="Helpful"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill={feedback === 'up' ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14z"/>
                <path d="M7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/>
              </svg>
            </button>
            <button
              className={`msg-thumb-btn${feedback === 'down' ? ' thumb-down-active' : ''}`}
              onClick={() => setFeedback(f => f === 'down' ? null : 'down')}
              title="Not helpful"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill={feedback === 'down' ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10 15v4a3 3 0 003 3l4-9V2H5.72a2 2 0 00-2 1.7l-1.38 9a2 2 0 002 2.3H10z"/>
                <path d="M17 2h2.67A2.31 2.31 0 0122 4v7a2.31 2.31 0 01-2.33 2H17"/>
              </svg>
            </button>
          </div>
        </div>

      </div>
    </div>
  )
}
