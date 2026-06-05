import { useState } from 'react'

export default function LoginScreen({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError]       = useState('')
  const [loading, setLoading]   = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (!username || !password) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail || 'Invalid username or password.')
        setLoading(false)
        setTimeout(() => setError(''), 2500)
      } else {
        localStorage.setItem('rag_token', data.token)
        onLogin(data.role)
      }
    } catch {
      setError('Connection error — is the server running?')
      setLoading(false)
    }
  }

  return (
    <div className="login-bg">
      <div className="login-card">

        {/* Logo + title */}
        <div className="login-top">
          <div className="login-logo-wrap">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2L4 6v6c0 5.25 3.5 10.15 8 11.35C16.5 22.15 20 17.25 20 12V6L12 2z" fill="rgba(255,255,255,0.15)"/>
              <polyline points="8.5 13.5 10.5 10.5 12.5 12.5 15.5 8.5"/>
            </svg>
          </div>
          <h1 className="login-title">Retirement Plan</h1>
          <p className="login-subtitle">Intelligence Platform</p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label className="form-label">Username</label>
            <input
              className="form-input"
              type="text"
              placeholder="Enter username"
              value={username}
              onChange={e => { setUsername(e.target.value); setError('') }}
              autoFocus
              autoComplete="username"
            />
          </div>

          <div className="login-field">
            <label className="form-label">Password</label>
            <input
              className={`form-input login-pw-input${error ? ' login-input-error' : ''}`}
              type="password"
              placeholder="Enter password"
              value={password}
              onChange={e => { setPassword(e.target.value); setError('') }}
              autoComplete="current-password"
            />
            {error && <p className="login-error-msg">{error}</p>}
          </div>

          <button
            type="submit"
            className="btn-primary login-submit"
            disabled={loading || !username || !password}
          >
            {loading ? 'Signing in…' : 'Sign In →'}
          </button>
        </form>

        <div className="login-footer">
          <span className="login-powered">
            Powered by Claude AI
            <span className="login-powered-sep"/>
            Anthropic
          </span>
        </div>

      </div>
    </div>
  )
}
