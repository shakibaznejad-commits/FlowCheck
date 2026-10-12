import React, { useState } from "react";

import { AppHeader } from "./components.jsx";


function errorMessage(payload) {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }

  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((issue) => issue.msg).join(" ");
  }

  return "FlowCheck could not authenticate your account. Try again.";
}


export default function AuthScreen({ backendState, theme, onToggleTheme, onAuthenticated }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isSignup = mode === "signup";

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);

    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(errorMessage(payload));
      }

      onAuthenticated(payload);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "FlowCheck could not authenticate your account. Try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  function changeMode(nextMode) {
    setMode(nextMode);
    setError("");
    setPassword("");
  }

  return (
    <div className="app-shell" data-theme={theme}>
      <AppHeader backendState={backendState} theme={theme} onToggleTheme={onToggleTheme} />

      <main className="auth-main" id="main-content">
        <section className="auth-panel" aria-labelledby="auth-heading">
          <p className="eyebrow">Your testing workspace</p>
          <h1 id="auth-heading">
            {isSignup ? "Create your account" : "Welcome back"}
          </h1>
          <p className="auth-description">
            {isSignup
              ? "Sign up to plan and run web tests with FlowCheck."
              : "Sign in to continue to your FlowCheck workspace."}
          </p>

          <form className="auth-form" onSubmit={handleSubmit} aria-busy={busy}>
            <div className="field-group">
              <label htmlFor="auth-email">Email address</label>
              <input
                className="auth-input"
                id="auth-email"
                name="email"
                type="email"
                autoComplete="email"
                maxLength={254}
                required
                disabled={busy}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? "auth-error" : undefined}
              />
            </div>

            <div className="field-group">
              <label htmlFor="auth-password">Password</label>
              <input
                className="auth-input"
                id="auth-password"
                name="password"
                type="password"
                autoComplete={isSignup ? "new-password" : "current-password"}
                minLength={isSignup ? 12 : undefined}
                maxLength={256}
                required
                disabled={busy}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                aria-invalid={Boolean(error)}
                aria-describedby={isSignup ? "password-help" : undefined}
              />
              {isSignup && (
                <p className="field-help" id="password-help">
                  Use at least 12 characters. Password managers and paste are supported.
                </p>
              )}
            </div>

            {error && (
              <p className="form-feedback form-feedback--error" id="auth-error" role="alert">
                {error}
              </p>
            )}

            <button className="run-button auth-submit" type="submit" disabled={busy}>
              <span>{busy ? "Please wait…" : isSignup ? "Create account" : "Sign in"}</span>
              {!busy && <span aria-hidden="true">→</span>}
            </button>
          </form>

          <p className="auth-switch">
            {isSignup ? "Already have an account?" : "New to FlowCheck?"}{" "}
            <button
              type="button"
              className="auth-switch-button"
              disabled={busy}
              onClick={() => changeMode(isSignup ? "login" : "signup")}
            >
              {isSignup ? "Sign in" : "Create an account"}
            </button>
          </p>
        </section>
      </main>
    </div>
  );
}
