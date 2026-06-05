import { useState, useEffect } from 'react'
import { apiFetch } from './api'
import DashboardTab  from './components/DashboardTab'
import IngestTab     from './components/IngestTab'
import ChatTab       from './components/ChatTab'
import LoginScreen   from './components/LoginScreen'

export default function App() {
  const [auth, setAuth] = useState(() => {
    const token = localStorage.getItem('rag_token')
    const role  = localStorage.getItem('rag_auth')
    return (token && role) ? role : null
  })

  const [chatTokens, setChatTokens] = useState(() => {
    try { return JSON.parse(localStorage.getItem('rag_chat_tokens') || '{"input":0,"output":0}') }
    catch { return { input: 0, output: 0 } }
  })
  const [ingestTokens, setIngestTokens] = useState(() => {
    try { return JSON.parse(localStorage.getItem('rag_ingest_tokens') || '{"input":0,"output":0}') }
    catch { return { input: 0, output: 0 } }
  })

  const [tab, setTab]             = useState(() => {
    const saved = localStorage.getItem('rag_auth')
    return saved === 'plan_consultant' ? 'chat' : 'dashboard'
  })
  const [summary, setSummary]     = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [chatPlanId, setChatPlanId] = useState('')
  const [ingestPlanId, setIngestPlanId] = useState(null)

  const role = auth

  useEffect(() => { if (auth) fetchSummary() }, [auth])

  async function fetchSummary() {
    try {
      const res = await apiFetch('/api/summary')
      if (res.ok) setSummary(await res.json())
    } catch {}
  }

  function handleLogin(newRole) {
    localStorage.setItem('rag_auth', newRole)
    setAuth(newRole)
    setTab(newRole === 'plan_admin' ? 'dashboard' : 'chat')
  }

  function handleLogout() {
    localStorage.removeItem('rag_auth')
    localStorage.removeItem('rag_token')
    setAuth(null)
  }

  function trackChatTokens(input, output) {
    setChatTokens(prev => {
      const next = { input: prev.input + (input || 0), output: prev.output + (output || 0) }
      localStorage.setItem('rag_chat_tokens', JSON.stringify(next))
      return next
    })
  }

  function trackIngestTokens(input, output) {
    setIngestTokens(prev => {
      const next = { input: prev.input + (input || 0), output: prev.output + (output || 0) }
      localStorage.setItem('rag_ingest_tokens', JSON.stringify(next))
      return next
    })
  }

  function resetTokens() {
    const zero = { input: 0, output: 0 }
    localStorage.setItem('rag_chat_tokens', JSON.stringify(zero))
    localStorage.setItem('rag_ingest_tokens', JSON.stringify(zero))
    setChatTokens(zero)
    setIngestTokens(zero)
  }

  function handleIngestSuccess() {
    fetchSummary()
    setRefreshKey(k => k + 1)
  }

  function goToChat(planId = '') {
    setChatPlanId(planId)
    setTab('chat')
  }

  function goToIngest(planId = null) {
    setIngestPlanId(planId)
    setTab('ingest')
  }

  async function deleteDocument(docId) {
    await apiFetch(`/api/documents/${docId}`, { method: 'DELETE' })
    handleIngestSuccess()
  }

  async function deletePlan(planId) {
    await apiFetch(`/api/plans/${planId}`, { method: 'DELETE' })
    handleIngestSuccess()
  }

  if (!auth) return <LoginScreen onLogin={handleLogin} />

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-brand">
          <div className="header-logo-wrap">
            <svg className="header-logo-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" fill="rgba(255,255,255,0.18)"/>
              <polyline points="8.5 13.5 10.5 10.5 12.5 12.5 15.5 8.5"/>
            </svg>
          </div>
          <div className="header-brand-text">
            <h1>Retirement Plan</h1>
            <p className="header-tagline">Intelligence Platform</p>
          </div>
        </div>

        <div className="header-user">
          <span className={`header-role-badge ${role}`}>
            {role === 'plan_admin' ? 'Plan Admin' : 'Plan Consultant'}
          </span>
          <button className="logout-btn" onClick={handleLogout}>Sign Out</button>
        </div>
      </header>

      <nav className="tab-nav">
        {role === 'plan_admin' && (
          <>
            <button
              className={`tab-btn${tab === 'dashboard' ? ' active' : ''}`}
              onClick={() => setTab('dashboard')}
            >
              <svg className="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3"  y="3"  width="7" height="7" rx="1.5"/>
                <rect x="14" y="3"  width="7" height="7" rx="1.5"/>
                <rect x="3"  y="14" width="7" height="7" rx="1.5"/>
                <rect x="14" y="14" width="7" height="7" rx="1.5"/>
              </svg>
              Dashboard
            </button>
            <button
              className={`tab-btn${tab === 'ingest' ? ' active' : ''}`}
              onClick={() => setTab('ingest')}
            >
              <svg className="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
              Ingest Document
            </button>
          </>
        )}
        <button
          className={`tab-btn${tab === 'chat' ? ' active' : ''}`}
          onClick={() => setTab('chat')}
        >
          <svg className="tab-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
          </svg>
          Query / Chat
        </button>
      </nav>

      <main className="app-main">
        <div className="tabs-wrapper">

          <div className={`tab-panel${tab === 'dashboard' ? ' active' : ''}`}>
            <DashboardTab
              summary={summary}
              refreshKey={refreshKey}
              onAskQuestions={goToChat}
              onAddDocument={goToIngest}
              onAddGenericDoc={() => goToIngest(null)}
              onDeleteDoc={deleteDocument}
              onDeletePlan={deletePlan}
              chatTokens={chatTokens}
              ingestTokens={ingestTokens}
              onResetTokens={resetTokens}
            />
          </div>

          <div className={`tab-panel${tab === 'ingest' ? ' active' : ''}`}>
            <IngestTab onIngestSuccess={handleIngestSuccess} initialPlanId={ingestPlanId} onIngestTokensUsed={trackIngestTokens} refreshKey={refreshKey} />
          </div>

          <div className={`tab-panel${tab === 'chat' ? ' active' : ''}`}>
            <ChatTab
              initialPlanId={chatPlanId}
              role={role}
              onTokensUsed={trackChatTokens}
              refreshKey={refreshKey}
            />
          </div>

        </div>
      </main>
    </div>
  )
}
