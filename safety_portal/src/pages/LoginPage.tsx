import { useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { AppHeader } from "../components/AppHeader";

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <AppHeader title="Safety Portal" />
      <main className="login__main">
        <form className="card login__card" onSubmit={onSubmit} noValidate>
          <h1 className="login__heading">Sign in</h1>
          <label className="field">
            <span className="field__label">Username</span>
            <input
              className="field__input"
              type="text"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              maxLength={128}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span className="field__label">Password</span>
            <input
              className="field__input"
              type="password"
              name="password"
              autoComplete="current-password"
              maxLength={256}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          {error ? (
            <p className="login__error" role="alert">
              {error}
            </p>
          ) : null}
          <button className="btn btn--primary btn--block" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </main>
    </div>
  );
}
