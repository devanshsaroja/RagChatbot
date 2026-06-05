import { useState, useEffect } from 'react'
import { apiFetch } from '../api'
import CompareModal from './CompareModal'

export default function DashboardTab({
  summary, refreshKey, onAskQuestions, onAddDocument, onAddGenericDoc,
  onDeleteDoc, onDeletePlan, chatTokens, ingestTokens, onResetTokens,
}) {
  const [plans, setPlans]           = useState([])
  const [genericDocs, setGenericDocs] = useState([])
  const [loading, setLoading]       = useState(true)
  const [search, setSearch]         = useState('')
  const [compareOpen, setCompareOpen] = useState(false)

  useEffect(() => {
    setLoading(true)
    apiFetch('/api/registry')
      .then(r => r.ok ? r.json() : {})
      .then(data => {
        setPlans(data.plans || [])
        setGenericDocs(data.generic_documents || [])
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [refreshKey])

  const filteredPlans = plans.filter(p =>
    !search ||
    p.plan_name.toLowerCase().includes(search.toLowerCase()) ||
    (p.employer_name || '').toLowerCase().includes(search.toLowerCase())
  )

  const chatTotal   = chatTokens   ? chatTokens.input   + chatTokens.output   : 0
  const ingestTotal = ingestTokens ? ingestTokens.input + ingestTokens.output : 0

  return (
    <div className="dashboard">

      {/* Stats */}
      <div className="dash-stats">
        {loading ? (
          [0,1,2,3].map(i => <SkeletonStatCard key={i}/>)
        ) : summary ? (
          <>
            <StatCard icon="🏢" value={summary.plans}            label="Plans" />
            <StatCard icon="📄" value={summary.plan_documents}   label="Plan Documents" />
            <StatCard icon="📋" value={summary.generic_documents} label="Regulatory Docs" />
            <StatCard icon="⚡" value={(summary.vector_chunks ?? summary.total_chunks).toLocaleString()} label="Vector Chunks" />
          </>
        ) : null}
      </div>

      {/* API cost bar — admin only */}
      {(chatTokens || ingestTokens) && (
        <div className="dash-cost-bar">
          <div className="dash-cost-head">
            <div className="dash-cost-left">
              <span className="dash-cost-icon">🔢</span>
              <span className="dash-cost-title">Cumulative API Usage</span>
              <span className="dash-cost-grand">{(chatTotal + ingestTotal).toLocaleString()} total tokens</span>
            </div>
            <button className="dash-cost-reset" onClick={onResetTokens}>Reset All</button>
          </div>
          <div className="dash-cost-rows">
            <CostRow icon="💬" label="Chat"    total={chatTotal}   tokens={chatTokens} />
            <CostRow icon="📥" label="Ingest"  total={ingestTotal} tokens={ingestTokens} />
          </div>
        </div>
      )}

      {/* Plans section */}
      <section className="dash-section">
        <div className="dash-section-head">
          <h2>Plans</h2>
          <div className="dash-section-actions">
            {plans.length >= 2 && (
              <button className="btn-secondary-sm" onClick={() => setCompareOpen(true)}>
                ⇄ Compare Plans
              </button>
            )}
          </div>
        </div>

        {/* Search bar */}
        {plans.length >= 4 && (
          <div className="dash-search-wrap">
            <input
              className="form-input dash-search"
              placeholder="Search plans or employers…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            {search && (
              <button className="dash-search-clear" onClick={() => setSearch('')}>✕</button>
            )}
          </div>
        )}

        {loading ? (
          <div className="dash-plan-grid">
            {[0,1].map(i => <SkeletonPlanCard key={i}/>)}
          </div>
        ) : filteredPlans.length === 0 ? (
          search ? (
            <p className="dash-muted">No plans match "{search}".</p>
          ) : (
            <div className="dash-empty">
              <p>No plans yet. Ingest a plan-specific document to get started.</p>
              <button
                className="btn-primary"
                style={{ width: 'auto', padding: '10px 24px' }}
                onClick={() => onAddDocument(null)}
              >
                Ingest first plan document →
              </button>
            </div>
          )
        ) : (
          <div className="dash-plan-grid">
            {filteredPlans.map(plan => (
              <PlanCard
                key={plan.plan_id}
                plan={plan}
                onAsk={() => onAskQuestions(plan.plan_id)}
                onAdd={() => onAddDocument(plan.plan_id)}
                onDeleteDoc={onDeleteDoc}
                onDeletePlan={onDeletePlan}
              />
            ))}
          </div>
        )}
      </section>

      {/* Generic / Regulatory docs */}
      <section className="dash-section">
        <div className="dash-section-head">
          <h2>Generic / Regulatory Documents</h2>
          <button className="btn-secondary-sm" onClick={onAddGenericDoc}>+ Add</button>
        </div>

        {!loading && genericDocs.length === 0 ? (
          <p className="dash-muted">No regulatory documents ingested yet.</p>
        ) : (
          <div className="dash-generic-list">
            {genericDocs.map(doc => (
              <GenericDocRow key={doc.doc_id} doc={doc} onDelete={onDeleteDoc}/>
            ))}
          </div>
        )}
      </section>

      {/* Compare modal */}
      {compareOpen && (
        <CompareModal plans={plans} onClose={() => setCompareOpen(false)}/>
      )}
    </div>
  )
}


// ── Cost Row ──────────────────────────────────────────────────────────────────

function CostRow({ icon, label, total, tokens }) {
  if (!tokens) return null
  return (
    <div className="dash-cost-row">
      <span className="dash-cost-row-label">{icon} {label}</span>
      <span className="dash-cost-row-total">{total.toLocaleString()} tokens</span>
      <span className="dash-cost-row-detail">{tokens.input.toLocaleString()} in · {tokens.output.toLocaleString()} out</span>
    </div>
  )
}


// ── Stat Card ─────────────────────────────────────────────────────────────────

function StatCard({ icon, value, label }) {
  return (
    <div className="dash-stat-card">
      <div className="dash-stat-icon">{icon}</div>
      <div className="dash-stat-value">{value}</div>
      <div className="dash-stat-label">{label}</div>
    </div>
  )
}


// ── Skeleton Components ───────────────────────────────────────────────────────

function SkeletonStatCard() {
  return (
    <div className="dash-stat-card">
      <div className="skeleton" style={{ width: 42, height: 42, borderRadius: 10 }}/>
      <div className="skeleton" style={{ width: '55%', height: 28, marginTop: 4 }}/>
      <div className="skeleton" style={{ width: '75%', height: 13, marginTop: -4 }}/>
    </div>
  )
}

function SkeletonPlanCard() {
  return (
    <div className="dash-plan-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <div className="skeleton" style={{ width: '65%', height: 17, marginBottom: 8 }}/>
          <div className="skeleton" style={{ width: '40%', height: 13 }}/>
        </div>
        <div className="skeleton" style={{ width: 52, height: 22, borderRadius: 99 }}/>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '10px 12px', background: '#f8fafc', borderRadius: 8 }}>
        <div className="skeleton" style={{ height: 14 }}/>
        <div className="skeleton" style={{ height: 14, width: '70%' }}/>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <div className="skeleton" style={{ flex: 1, height: 36, borderRadius: 8 }}/>
        <div className="skeleton" style={{ width: 120, height: 36, borderRadius: 8 }}/>
      </div>
    </div>
  )
}


// ── Plan Card ─────────────────────────────────────────────────────────────────

function PlanCard({ plan, onAsk, onAdd, onDeleteDoc, onDeletePlan }) {
  const docs = plan.documents || []
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting]           = useState(false)

  async function handleDeletePlan() {
    setDeleting(true)
    await onDeletePlan(plan.plan_id)
    setDeleting(false)
  }

  return (
    <div className="dash-plan-card">
      <div className="dash-plan-header">
        <div>
          <div className="dash-plan-name">{plan.plan_name}</div>
          {plan.employer_name && <div className="dash-plan-employer">{plan.employer_name}</div>}
        </div>
        <span className="dash-plan-badge">{docs.length} {docs.length === 1 ? 'doc' : 'docs'}</span>
      </div>

      {docs.length > 0 && (
        <div className="dash-plan-docs">
          {docs.map(doc => (
            <DocRow key={doc.doc_id} doc={doc} onDelete={onDeleteDoc}/>
          ))}
        </div>
      )}

      <div className="dash-plan-actions">
        <button className="dash-ask-btn" onClick={onAsk}>Ask Questions →</button>
        <button className="btn-secondary-sm" onClick={onAdd}>+ Add Document</button>
        <div className="dash-delete-zone">
          {!confirmDelete ? (
            <button className="dash-delete-btn" onClick={() => setConfirmDelete(true)} title="Delete plan">🗑</button>
          ) : (
            <>
              <button className="dash-confirm-btn" onClick={handleDeletePlan} disabled={deleting}>
                {deleting ? '…' : 'Delete plan?'}
              </button>
              <button className="btn-link" onClick={() => setConfirmDelete(false)}>Cancel</button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}


// ── Doc Row ───────────────────────────────────────────────────────────────────

function DocRow({ doc, onDelete }) {
  const [confirm, setConfirm] = useState(false)
  const [deleting, setDeleting] = useState(false)

  async function handleDelete() {
    setDeleting(true)
    await onDelete(doc.doc_id)
    setDeleting(false)
  }

  return (
    <div className="dash-plan-doc">
      <span>📄</span>
      <span className="dash-plan-doc-name">{doc.filename}</span>
      <span className="dash-plan-doc-meta">{doc.doc_type} · {doc.chunk_count} chunks</span>
      {!confirm ? (
        <button className="dash-doc-delete-btn" onClick={() => setConfirm(true)} title="Delete document">×</button>
      ) : (
        <div className="dash-doc-confirm">
          <button className="dash-confirm-btn" onClick={handleDelete} disabled={deleting}>
            {deleting ? '…' : 'Delete?'}
          </button>
          <button className="btn-link" onClick={() => setConfirm(false)}>Cancel</button>
        </div>
      )}
    </div>
  )
}


// ── Generic Doc Row ───────────────────────────────────────────────────────────

function GenericDocRow({ doc, onDelete }) {
  const [confirm, setConfirm] = useState(false)
  const [deleting, setDeleting] = useState(false)

  async function handleDelete() {
    setDeleting(true)
    await onDelete(doc.doc_id)
    setDeleting(false)
  }

  return (
    <div className="dash-generic-item">
      <span className="dash-doc-icon">📋</span>
      <div style={{ flex: 1 }}>
        <div className="dash-doc-name">{doc.filename}</div>
        <div className="dash-doc-meta">{doc.doc_type} · {doc.chunk_count} chunks</div>
      </div>
      {!confirm ? (
        <button className="dash-delete-btn" onClick={() => setConfirm(true)} title="Delete document">🗑</button>
      ) : (
        <div className="dash-doc-confirm">
          <button className="dash-confirm-btn" onClick={handleDelete} disabled={deleting}>
            {deleting ? '…' : 'Delete?'}
          </button>
          <button className="btn-link" onClick={() => setConfirm(false)}>Cancel</button>
        </div>
      )}
    </div>
  )
}
