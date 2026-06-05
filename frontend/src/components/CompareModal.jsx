import { useState, useEffect } from 'react'
import { apiFetch } from '../api'

const ROWS = [
  { label: 'Plan Type',            get: f => f?.plan_type },
  { label: 'Record Keeper',        get: f => f?.record_keeper },
  { label: 'Plan Year End',        get: f => f?.plan_year_end },
  { label: 'Employer Match',       get: f => f?.employer_match?.formula_readable },
  { label: 'Vesting',              get: f => f?.vesting?.employer_match?.formula_readable },
  { label: 'Eligibility',          get: f => f?.eligibility?.formula_readable },
  { label: 'Company Contribution', get: f => f?.nonelective_contribution?.available ? f.nonelective_contribution.formula_readable : null },
  { label: 'Auto-Enrollment',      get: f => f?.features?.auto_enrollment  == null ? null : f.features.auto_enrollment  ? '✓ Yes' : '✗ No', bool: true },
  { label: 'Roth 401(k)',          get: f => f?.features?.roth_contributions == null ? null : f.features.roth_contributions ? '✓ Yes' : '✗ No', bool: true },
  { label: 'Loans',                get: f => f?.features?.loan_provision    == null ? null : f.features.loan_provision    ? '✓ Yes' : '✗ No', bool: true },
  { label: 'Hardship Withdrawal',  get: f => f?.features?.hardship_withdrawal == null ? null : f.features.hardship_withdrawal ? '✓ Yes' : '✗ No', bool: true },
]

function usePlanFacts(planId) {
  const [facts, setFacts]     = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!planId) { setFacts(null); return }
    setLoading(true)
    apiFetch(`/api/plans/${planId}/rules`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { setFacts(d); setLoading(false) })
      .catch(() => { setFacts(null); setLoading(false) })
  }, [planId])

  return { facts, loading }
}

function CellValue({ value, bool }) {
  if (value == null || value === '') return <span className="cmp-null">—</span>
  if (bool) {
    const isYes = value.startsWith('✓')
    return <span className={`cmp-bool ${isYes ? 'cmp-yes' : 'cmp-no'}`}>{value}</span>
  }
  return <span>{value}</span>
}

export default function CompareModal({ plans, onClose }) {
  const [planAId, setPlanAId] = useState('')
  const [planBId, setPlanBId] = useState('')

  const { facts: factsA, loading: loadingA } = usePlanFacts(planAId)
  const { facts: factsB, loading: loadingB } = usePlanFacts(planBId)

  const planA = plans.find(p => p.plan_id === planAId)
  const planB = plans.find(p => p.plan_id === planBId)

  const showTable = planAId || planBId

  return (
    <div className="cmp-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="cmp-modal">

        <div className="cmp-header">
          <h2>Compare Plans</h2>
          <button className="cmp-close" onClick={onClose}>✕</button>
        </div>

        {/* Plan selectors */}
        <div className="cmp-selectors">
          <div className="cmp-selector-col">
            <label className="form-label">Plan A</label>
            <select
              className="form-select"
              value={planAId}
              onChange={e => setPlanAId(e.target.value)}
            >
              <option value="">Select a plan…</option>
              {plans.filter(p => p.plan_id !== planBId).map(p => (
                <option key={p.plan_id} value={p.plan_id}>{p.plan_name}</option>
              ))}
            </select>
          </div>

          <div className="cmp-vs">vs</div>

          <div className="cmp-selector-col">
            <label className="form-label">Plan B</label>
            <select
              className="form-select"
              value={planBId}
              onChange={e => setPlanBId(e.target.value)}
            >
              <option value="">Select a plan…</option>
              {plans.filter(p => p.plan_id !== planAId).map(p => (
                <option key={p.plan_id} value={p.plan_id}>{p.plan_name}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Comparison table */}
        {showTable ? (
          <div className="cmp-table-wrap">
            <table className="cmp-table">
              <thead>
                <tr>
                  <th className="cmp-th-label"/>
                  <th className="cmp-th-plan">
                    {planA ? (
                      <>
                        <div className="cmp-plan-name">{planA.plan_name}</div>
                        {planA.employer_name && <div className="cmp-plan-employer">{planA.employer_name}</div>}
                        {loadingA && <div className="cmp-loading-badge">Loading…</div>}
                      </>
                    ) : <span className="cmp-placeholder">Plan A</span>}
                  </th>
                  <th className="cmp-th-plan">
                    {planB ? (
                      <>
                        <div className="cmp-plan-name">{planB.plan_name}</div>
                        {planB.employer_name && <div className="cmp-plan-employer">{planB.employer_name}</div>}
                        {loadingB && <div className="cmp-loading-badge">Loading…</div>}
                      </>
                    ) : <span className="cmp-placeholder">Plan B</span>}
                  </th>
                </tr>
              </thead>
              <tbody>
                {ROWS.map(row => (
                  <tr key={row.label} className="cmp-row">
                    <td className="cmp-row-label">{row.label}</td>
                    <td className="cmp-cell">
                      {planAId
                        ? loadingA
                          ? <span className="cmp-shimmer"/>
                          : <CellValue value={row.get(factsA)} bool={row.bool}/>
                        : <span className="cmp-null">—</span>
                      }
                    </td>
                    <td className="cmp-cell">
                      {planBId
                        ? loadingB
                          ? <span className="cmp-shimmer"/>
                          : <CellValue value={row.get(factsB)} bool={row.bool}/>
                        : <span className="cmp-null">—</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="cmp-empty">
            Select two plans above to compare their details side by side.
          </div>
        )}

      </div>
    </div>
  )
}
