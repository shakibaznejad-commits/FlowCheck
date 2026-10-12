import React, { useEffect, useState } from "react";
import { StatusDistribution, StatusRing, ScreenshotViewer } from "./visuals.jsx";
import { countScenarioStatuses, resolveScenarioStatus, scenarioDataCategory } from "./ui-model.js";

const SCENARIO_STATUS_LABELS = { pass: "PASS", fail: "FAIL", blocked: "BLOCKED", inconclusive: "INCONCLUSIVE", not_run: "NOT RUN" };


export function AppHeader({ backendState, user, onLogout, theme = "dark", onToggleTheme }) {
  return (
    <header className="app-header">
      <a className="brand" href="/" aria-label="FlowCheck home">
        <span className="brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </span>
        <span className="brand-copy">
          <span className="brand-name">FlowCheck</span>
          <span className="brand-tagline">Agentic web testing</span>
        </span>
      </a>

      <div className={`header-context${user ? " header-context--signed-in" : ""}`}>
        {user ? (
          <>
            <span className="account-email" title={user.email}>{user.email}</span>
            <button className="logout-button" type="button" onClick={onLogout}>
              Sign out
            </button>
          </>
        ) : (
          <span className="workspace-label">Secure workspace</span>
        )}
        <button className="theme-toggle" type="button" onClick={onToggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} aria-pressed={theme === "dark"}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M2 12h2m16 0h2M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42" /></svg>
          <span className="theme-toggle-label">{theme === "dark" ? "Dark" : "Light"}</span>
        </button>
        <BackendHealth state={backendState} />
      </div>
    </header>
  );
}


export function BackendHealth({ state }) {
  const labels = {
    checking: "Checking backend",
    online: "Backend online",
    offline: "Backend unavailable",
  };

  return (
    <div
      className={`backend-health backend-health--${state}`}
      role="status"
      aria-live="polite"
    >
      <span className="health-dot" aria-hidden="true" />
      <span>{labels[state]}</span>
    </div>
  );
}


export function ScenarioSetup({ plan, onRun, onSave, isBusy = false }) {
  const [selected, setSelected] = useState(() => plan.tests.map((test) => test.scenario_id));
  const [expandedId, setExpandedId] = useState(null);
  const [testEmail, setTestEmail] = useState("");
  const [testPassword, setTestPassword] = useState("");
  const [negativeEmail, setNegativeEmail] = useState("");
  const [negativePassword, setNegativePassword] = useState("");
  const [authorized, setAuthorized] = useState(false);
  const [saveState, setSaveState] = useState("");
  const selectedTests = plan.tests.filter((test) => selected.includes(test.scenario_id));
  const required = new Set(selectedTests.flatMap((test) => test.prerequisites || []));
  const validEmailRequired = required.has("valid_test_email") || required.has("test_email");
  const validPasswordRequired = required.has("valid_test_password") || required.has("test_password");
  const negativeEmailUsed = required.has("negative_test_email");
  const negativePasswordUsed = required.has("negative_test_password");
  const credentialsReady = (!validEmailRequired || Boolean(testEmail.trim()))
    && (!validPasswordRequired || Boolean(testPassword))
    && (!validEmailRequired && !validPasswordRequired || authorized);
  const dataLabels = {
    valid_test_email: "Valid reference email", valid_test_password: "Valid reference password",
    negative_test_email: "Negative email", negative_test_password: "Negative password",
    test_email: "Valid reference email", test_password: "Valid reference password",
  };

  useEffect(() => {
    setSelected(plan.tests.map((test) => test.scenario_id));
    setExpandedId(null);
    setTestEmail(""); setTestPassword(""); setNegativeEmail(""); setNegativePassword(""); setAuthorized(false);
  }, [plan]);

  function toggle(id) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  async function save() {
    setSaveState("Saving suite…");
    try { await onSave(); setSaveState("Reusable suite saved"); }
    catch (error) { setSaveState(error instanceof Error ? error.message : "Could not save suite."); }
  }

  return (
    <section className="panel scenario-setup" aria-labelledby="scenario-setup-heading" data-testid="smart-test-setup">
      <div className="scenario-setup-topline">
        <div><p className="eyebrow">{plan.flow} · {plan.scope === "broad" ? "Broad coverage" : "Focused check"}</p><h2 id="scenario-setup-heading">Smart Test Setup</h2></div>
        <span className="setup-scope-count">{plan.tests.length} <span>scenarios</span></span>
      </div>
      <div className="scenario-selection-toolbar">
        <span>{selected.length} selected</span>
        <div>
          <button type="button" className="text-button" disabled={isBusy || selected.length === plan.tests.length} onClick={() => setSelected(plan.tests.map((test) => test.scenario_id))}>Select all</button>
          <span aria-hidden="true">·</span>
          <button type="button" className="text-button" disabled={isBusy || selected.length === 0} onClick={() => setSelected([])}>Deselect all</button>
        </div>
      </div>
      <fieldset className="scenario-selection" disabled={isBusy}>
        <legend className="sr-only">Scenarios to execute</legend>
        {plan.tests.map((test, index) => {
          const isExpanded = expandedId === test.scenario_id;
          const category = scenarioDataCategory(test);
          return (
            <article className={`setup-scenario${selected.includes(test.scenario_id) ? " is-selected" : ""}${isExpanded ? " is-expanded" : ""}`} key={test.scenario_id}>
              <div className="setup-scenario-row">
                <input aria-label={`Select ${test.name}`} type="checkbox" checked={selected.includes(test.scenario_id)} onChange={() => toggle(test.scenario_id)} />
                <StatusRing status="pending" size="sm" label={`Scenario ${index + 1} pending`} />
                <button className="scenario-expand-button" type="button" aria-expanded={isExpanded} aria-controls={`setup-detail-${test.scenario_id}`} onClick={() => setExpandedId(isExpanded ? null : test.scenario_id)}>
                  <span className="scenario-name">{test.name}</span>
                  <span className={`scenario-category scenario-category--${category === "Negative test data" ? "negative" : category === "Valid reference account" ? "reference" : "neutral"}`}>{category}</span>
                </button>
                <span className="scenario-expand-mark" aria-hidden="true">{isExpanded ? "−" : "+"}</span>
              </div>
              <div className={`scenario-expand-content${isExpanded ? " is-open" : ""}`} id={`setup-detail-${test.scenario_id}`} aria-hidden={!isExpanded} inert={!isExpanded}>
                <p>{test.description}</p>
                <div className="setup-detail-grid"><div><span className="detail-label">Expected</span><p>{test.expected_result}</p></div><div><span className="detail-label">Prerequisites</span><p>{(test.prerequisites || []).map((item) => dataLabels[item] || item).join(" · ") || "None"}</p></div></div>
                <details className="technical-disclosure"><summary>Test intent and verification</summary>{test.goal && <p>{test.goal}</p>}<ul>{(test.assertions || []).map((assertion, assertionIndex) => <li key={`assertion-${assertionIndex}`}>{assertion.kind.replaceAll("_", " ")} · {assertion.expectation.replaceAll("_", " ")}{assertion.text ? ` · “${assertion.text}”` : ""}</li>)}{(test.steps || []).map((step, stepIndex) => <li key={`step-${stepIndex}`}>{step.action} · {step.target}</li>)}</ul></details>
              </div>
            </article>
          );
        })}
      </fieldset>

      {(validEmailRequired || validPasswordRequired || negativeEmailUsed || negativePasswordUsed) && (
        <details className="prerequisite-panel" open>
          <summary>Test data <span>{required.size} required type{required.size === 1 ? "" : "s"}</span></summary>
          <div className="test-data-fields">
            {validEmailRequired && <label>Valid test account email<input type="email" autoComplete="username" disabled={isBusy} value={testEmail} onChange={(event) => setTestEmail(event.target.value)} /></label>}
            {validPasswordRequired && <label>Valid test account password<input type="password" autoComplete="current-password" disabled={isBusy} value={testPassword} onChange={(event) => setTestPassword(event.target.value)} /></label>}
            {negativeEmailUsed && <label>Negative test email (optional)<input type="email" autoComplete="off" disabled={isBusy} value={negativeEmail} onChange={(event) => setNegativeEmail(event.target.value)} /><span className="field-help">A unique non-deliverable value is generated if empty.</span></label>}
            {negativePasswordUsed && <label>Negative test password (optional)<input type="password" autoComplete="new-password" disabled={isBusy} value={negativePassword} onChange={(event) => setNegativePassword(event.target.value)} /><span className="field-help">A one-run value is generated if empty.</span></label>}
            {(validEmailRequired || validPasswordRequired) && <label className="authorization-check"><input type="checkbox" checked={authorized} disabled={isBusy} onChange={(event) => setAuthorized(event.target.checked)} /> I am authorized to test this account. Attempts may trigger rate limits or account lockout.</label>}
            <p className="field-help">Passwords go only to browser execution. They are not sent to the LLM or stored in reports and History.</p>
          </div>
        </details>
      )}
      <div className="scenario-setup-actions">
        <button className="run-button" type="button" disabled={!selected.length || isBusy || !credentialsReady} aria-busy={isBusy} onClick={() => onRun({ selectedIds: selected, testData: { ...(testEmail ? { valid_test_email: testEmail } : {}), ...(testPassword ? { valid_test_password: testPassword } : {}), ...(negativeEmail ? { negative_test_email: negativeEmail } : {}), ...(negativePassword ? { negative_test_password: negativePassword } : {}) }, authorizedTestAccount: authorized })}>{isBusy ? "Running approved scenarios…" : "Run selected scenarios"} {!isBusy && <span aria-hidden="true">→</span>}</button>
        <button className="quiet-button" type="button" disabled={isBusy} onClick={save}>Save reusable suite</button>
        {saveState && <span className="field-help" role="status" aria-live="polite">{saveState}</span>}
      </div>
      <p className="setup-persistence-note">Runs are saved automatically. A suite saves the reusable plan.</p>
    </section>
  );
}


export function ScenarioResults({ plan, execution, targetUrl, report, reportState = "idle", executionState = "idle", compact = false }) {
  const [expandedId, setExpandedId] = useState(null);
  if (!plan) return null;
  const domain = (() => { try { return new URL(targetUrl).hostname; } catch { return "Target website"; } })();
  const resultById = new Map((execution?.tests || []).map((item) => [item.scenario_id, item]));
  const reportById = new Map((report?.scenario_results || []).map((item) => [item.scenario_id, item]));
  const counts = countScenarioStatuses(plan.tests.map((test) => ({
    status: resolveScenarioStatus(test, reportById.get(test.scenario_id), resultById.get(test.scenario_id)),
  })));
  const runId = execution?.run_id;
  const inProgress = executionState === "running" && !execution;
  const reportFailed = reportState === "error";
  const overallStatus = report?.status === "passed" ? "pass" : report?.status === "failed" ? "fail" : report?.status === "inconclusive" ? "inconclusive" : report?.status === "blocked" ? "blocked" : "not_run";

  return (
    <section className={`scenario-dashboard${compact ? " scenario-dashboard--compact" : ""}`} aria-label={`${domain} ${plan.flow} scenario results`} data-testid="scenario-results">
      {!compact && <div className="scenario-dashboard-heading">
        <div><p className="eyebrow">{domain} <i aria-hidden="true">·</i> {plan.flow} <i aria-hidden="true">·</i> {plan.tests.length} scenarios</p><h2>{inProgress ? "Browser run in progress" : report ? "Test results" : "Scenario results"}</h2></div>
        {report && <div className="overall-result"><StatusRing status={overallStatus} size="lg" /><span><strong>{overallStatus.replace("_", " ").toUpperCase()}</strong><small>Website result</small></span></div>}
      </div>}
      {inProgress ? (
        <div className="execution-live-panel" role="status" aria-live="polite">
          <span className="indeterminate-track" aria-hidden="true"><span /></span>
          <strong>Browser run in progress</strong>
          <span>Scenario states settle when execution returns.</span>
          <div className="live-scenario-list">{plan.tests.map((test) => <div className="live-scenario-item" key={test.scenario_id}><StatusRing status="pending" size="sm" /><span>{test.name}</span><small>Pending</small></div>)}</div>
        </div>
      ) : reportFailed ? (
        <div className="report-availability" role="status"><StatusRing status="inconclusive" /><span><strong>Report unavailable</strong><small>Browser results are preserved.</small></span></div>
      ) : null}
      {execution && <>
        {!compact && <div className="report-summary-strip"><StatusDistribution counts={counts} /><span className={`agent-health agent-health--${execution.agent_status || "completed"}`}><span className="health-dot" /> Agent {execution.agent_status || "completed"}</span></div>}
        {!compact && report?.narrative_source === "fallback" && <details className="report-source-note"><summary>Deterministic result</summary><p>AI analysis was unavailable. Outcomes use validated browser evidence.</p></details>}
        <div className="scenario-card-list">
          {plan.tests.map((test, index) => {
            const result = resultById.get(test.scenario_id);
            const scenarioReport = reportById.get(test.scenario_id);
            const status = resolveScenarioStatus(test, scenarioReport, result);
            const screenshots = (execution.evidence || []).filter((item) => item.test_index === index);
            const isExpanded = expandedId === test.scenario_id;
            const actual = scenarioReport?.actual_result || result?.actual_result || (status === "not_run" ? "This scenario was not executed." : "No verified observation was recorded.");
            return (
              <article className={`scenario-result-card scenario-result-card--${status}${isExpanded ? " is-expanded" : ""}`} key={test.scenario_id}>
                <button className="scenario-result-trigger" type="button" aria-expanded={isExpanded} aria-controls={`result-detail-${test.scenario_id}`} onClick={() => setExpandedId(isExpanded ? null : test.scenario_id)}>
                  <StatusRing status={status} />
                  <span className="scenario-result-name">{test.name}</span>
                  <span className={`scenario-result-state scenario-result-state--${status}`}>{SCENARIO_STATUS_LABELS[status] || status.replace("_", " ")}</span>
                  <span className="scenario-expand-mark" aria-hidden="true">{isExpanded ? "−" : "+"}</span>
                </button>
                <div className={`scenario-result-detail${isExpanded ? " is-open" : ""}`} id={`result-detail-${test.scenario_id}`} aria-hidden={!isExpanded} inert={!isExpanded}>
                  <div className="scenario-result-grid"><div><span className="detail-label">Expected</span><p>{scenarioReport?.expected_result || test.expected_result}</p></div><div><span className="detail-label">Observed</span><p>{actual}</p></div></div>
                  {(scenarioReport?.uncertainty || result?.uncertainty) && <p className="scenario-uncertainty">{scenarioReport?.uncertainty || result?.uncertainty}</p>}
                  <ScreenshotViewer screenshots={screenshots} runId={runId} scenarioName={test.name} />
                  <details className="technical-disclosure"><summary>Technical evidence and reproduction</summary>
                    <ol>{(scenarioReport?.reproduction_steps || result?.reproduction_steps || test.steps.map((step) => `${step.action}: ${step.target}`)).map((step, stepIndex) => <li key={`${stepIndex}-${step}`}>{step}</li>)}</ol>
                    {(result?.assertions || []).map((assertion, assertionIndex) => <p className="scenario-evidence" key={assertionIndex}><StatusRing status={assertion.status === "passed" ? "pass" : assertion.status === "failed" ? "fail" : "inconclusive"} size="xs" /> {assertion.message}</p>)}
                    {result?.steps?.length > 0 && <ol className="execution-step-list">{result.steps.map((step, stepIndex) => <li key={`${step.action}-${stepIndex}`}><span>{step.action} · {step.target}</span><small>{step.status} — {step.message}</small></li>)}</ol>}
                    {result?.observations?.length > 0 && <details><summary>Observed page details</summary><ul>{result.observations.map((observation, observationIndex) => <li key={observationIndex}>{observation.summary || observation.text || observation.kind}</li>)}</ul></details>}
                    {scenarioReport?.evidence?.length > 0 && <ul>{scenarioReport.evidence.map((item, evidenceIndex) => <li key={evidenceIndex}>{item}</li>)}</ul>}
                  </details>
                </div>
              </article>
            );
          })}
        </div>
      </>}
    </section>
  );
}

export function TestWorkspace({
  plannerState,
  isBusy = false,
  executionState = "idle",
  analysisState = "idle",
  plannerError,
  onSubmit,
  onInputChange,
}) {
  const [url, setUrl] = useState("");
  const [goal, setGoal] = useState("");
  const [touched, setTouched] = useState({
    url: false,
    goal: false,
  });
  const [submitted, setSubmitted] = useState(false);

  const validUrl = (() => {
    try {
      const parsed = new URL(url);

      return (
        parsed.protocol === "http:" ||
        parsed.protocol === "https:"
      );
    } catch {
      return false;
    }
  })();

  const validGoal =
    goal.trim().length > 0;

  const canRun =
    validUrl && validGoal;

  const showUrlError =
    (touched.url || submitted) &&
    !validUrl;

  const showGoalError =
    (touched.goal || submitted) &&
    !validGoal;


  function handleSubmit(event) {
    event.preventDefault();

    setSubmitted(true);

    if (canRun && !isBusy) {
      onSubmit({
        url: url.trim(),
        goal: goal.trim(),
      });
    }
  }


  return (
    <section
      className="panel workspace-panel"
      aria-labelledby="workspace-heading"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">
            01{" "}
            <span className="eyebrow-divider">
              /
            </span>{" "}
            Configure
          </p>

          <h2 id="workspace-heading">
            Set up your test
          </h2>
        </div>

        <span
          className="panel-icon"
          aria-hidden="true"
        >
          ↗
        </span>
      </div>


      <form
        className="test-form"
        onSubmit={handleSubmit}
        noValidate
      >
        <div className="field-group">
          <label htmlFor="target-url">
            Website URL{" "}
            <span className="required-label">
              Required
            </span>
          </label>

          <div
            className={`input-wrap${
              showUrlError
                ? " input-wrap--error"
                : ""
            }`}
          >
            <input
              id="target-url"
              name="url"
              type="url"
              autoComplete="url"
              inputMode="url"
              placeholder="https://example.com"
              disabled={isBusy}
              value={url}
              onChange={(event) => {
                setUrl(event.target.value);
                onInputChange();
              }}
              onBlur={() =>
                setTouched((current) => ({
                  ...current,
                  url: true,
                }))
              }
              aria-invalid={showUrlError}
              aria-describedby="url-help url-error"
              required
            />
          </div>

          <p
            className="field-help"
            id="url-help"
          >
            Start with a page you are authorized to test.
          </p>

          <p
            className="field-error"
            id="url-error"
            aria-live="polite"
          >
            {showUrlError
              ? "Enter a valid http or https website URL."
              : ""}
          </p>
        </div>


        <div className="field-group">
          <label htmlFor="test-goal">
            Test goal{" "}
            <span className="required-label">
              Required
            </span>
          </label>

          <textarea
            id="test-goal"
            name="goal"
            rows="5"
            maxLength="1200"
            placeholder="For example: Try an invalid login and confirm access is rejected."
            disabled={isBusy}
            value={goal}
            onChange={(event) => {
              setGoal(event.target.value);
              onInputChange();
            }}
            onBlur={() =>
              setTouched((current) => ({
                ...current,
                goal: true,
              }))
            }
            aria-invalid={showGoalError}
            aria-describedby="goal-help goal-error goal-count"
            required
          />

          <div className="field-meta">
            <p
              className="field-help"
              id="goal-help"
            >
              Describe the behavior to verify.
            </p>

            <span
              className="character-count"
              id="goal-count"
            >
              {goal.length}/1200
            </span>
          </div>

          <p
            className="field-error"
            id="goal-error"
            aria-live="polite"
          >
            {showGoalError
              ? "Describe the web flow you want to test."
              : ""}
          </p>
        </div>


        <div className="form-actions">
          <p
            className={`ready-state${
              canRun && !isBusy
                ? " ready-state--ready"
                : ""
            }${
              plannerState === "error"
                ? " ready-state--error"
                : ""
            }`}
            role="status"
            aria-live="polite"
          >
            <span
              className="ready-dot"
              aria-hidden="true"
            />

            {isBusy
              ? plannerState === "planning"
                ? "Creating a structured test plan"
                : executionState === "running"
                  ? "Running the planned browser steps"
                  : "Analyzing the browser result"
              : plannerState === "generated"
              ? analysisState === "done"
                ? "Browser result analyzed"
                : analysisState === "error"
                  ? "Analysis failed. Browser observations are retained."
                  : executionState === "failed"
                    ? "Browser steps failed. Analysis is pending."
                    : "Plan generated. Browser run started."
              : plannerState === "error"
              ? "Planner error. Review the message below."
              : canRun
              ? "Ready to run"
              : "Add a URL and goal to continue"}
          </p>

          <button
            className="run-button"
            type="submit"
            disabled={!canRun || isBusy}
            aria-busy={isBusy}
          >
            {isBusy && (
              <span
                className="button-spinner"
                aria-hidden="true"
              />
            )}

            <span>
              {isBusy
                ? plannerState === "planning"
                  ? "Planning..."
                  : "Running..."
                : plannerState === "generated"
                ? "Run Again"
                : plannerState === "error"
                ? "Try Again"
                : "Run Test"}
            </span>

            {!isBusy && (
              <span aria-hidden="true">→</span>
            )}
          </button>
        </div>


        {plannerError && (
          <p
            className="form-feedback form-feedback--error"
            role="alert"
          >
            {plannerError}
          </p>
        )}

        <p className="form-footnote">
          Agentic flow{" "}
          <span aria-hidden="true">·</span>{" "}
          Planner and browser execution connected
        </p>
      </form>
    </section>
  );
}


const executionStages = [
  {
    title: "Understanding goal",
    detail:
      "Identify the flow and expected outcome.",
  },
  {
    title: "Planning tests",
    detail:
      "Break the request into testable steps.",
  },
  {
    title: "Running browser",
    detail:
      "Interact with the target website.",
  },
  {
    title: "Analyzing results",
    detail:
      "Compare observed and expected behavior.",
  },
  {
    title: "Generating report",
    detail:
      "Collect findings and reproduction steps.",
  },
];


function getStageState(
  index,
  plannerState,
  executionState,
  analysisState,
  reportState
) {
  if (plannerState === "idle") {
    return "queued";
  }

  if (plannerState === "planning") {
    if (index === 0) {
      return "done";
    }

    if (index === 1) {
      return "running";
    }

    return "queued";
  }

  if (plannerState === "error") {
    if (index === 0) {
      return "done";
    }

    if (index === 1) {
      return "failed";
    }

    return "queued";
  }

  if (plannerState === "generated") {
    if (index === 0 || index === 1) {
      return "done";
    }

    if (index === 2) {
      if (executionState === "running") {
        return "running";
      }

      if (executionState === "done") {
        return "done";
      }

      if (executionState === "failed") {
        return "failed";
      }

      return "queued";
    }

    if (index === 3) {
      if (analysisState === "running") {
        return "running";
      }

      if (analysisState === "done") {
        return "done";
      }

      if (analysisState === "error") {
        return "failed";
      }

      return "queued";
    }

    if (index === 4) {
      if (analysisState !== "done") {
        return "queued";
      }

      if (reportState === "running") {
        return "running";
      }

      if (reportState === "done") {
        return "done";
      }

      if (reportState === "error") {
        return "failed";
      }

      return "queued";
    }
  }

  return "queued";
}


function getStageLabel(state) {
  const labels = {
    queued: "Next",
    running: "Running",
    done: "Complete",
    failed: "Failed",
  };

  return labels[state] || "Queued";
}


function getMarkerStyle(state) {
  if (state === "done") {
    return {
      background: "#ecfdf3",
      borderColor: "#86efac",
      color: "#15803d",
      fontWeight: 700,
    };
  }

  if (state === "running") {
    return {
      background: "#eff6ff",
      borderColor: "#93c5fd",
      color: "#2563eb",
      fontWeight: 700,
      boxShadow:
        "0 0 0 4px rgba(37, 99, 235, 0.08)",
    };
  }

  if (state === "failed") {
    return {
      background: "#fef2f2",
      borderColor: "#fca5a5",
      color: "#dc2626",
      fontWeight: 700,
    };
  }

  return {};
}


function getStateStyle(state) {
  if (state === "done") {
    return {
      color: "#15803d",
      fontWeight: 700,
    };
  }

  if (state === "running") {
    return {
      color: "#2563eb",
      fontWeight: 700,
    };
  }

  if (state === "failed") {
    return {
      color: "#dc2626",
      fontWeight: 700,
    };
  }

  return {};
}


export function ExecutionTimeline({
  plannerState,
  executionState = "idle",
  analysisState = "idle",
  reportState = "idle",
}) {
  function getHeaderLabel() {
    if (reportState === "running") {
      return "Generating report";
    }

    if (reportState === "done") {
      return "Report ready";
    }

    if (reportState === "error") {
      return "Report failed";
    }

    if (plannerState === "planning") {
      return "Planning";
    }

    if (plannerState === "error") {
      return "Planner error";
    }

    if (analysisState === "running") {
      return "Analyzing results";
    }

    if (analysisState === "done") {
      return "Analysis done";
    }

    if (analysisState === "error") {
      return "Analysis failed";
    }

    if (executionState === "running") {
      return "Browser running";
    }

    if (executionState === "done") {
      return "Browser done";
    }

    if (executionState === "failed") {
      return "Browser failed";
    }

    if (plannerState === "generated") {
      return "Plan ready";
    }

    return "Upcoming";
  }


  return (
    <section
      className="panel timeline-panel"
      aria-labelledby="timeline-heading"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">RUN PIPELINE</p>
          <h2 id="timeline-heading">Test stages</h2>
        </div>

        <span className="upcoming-badge">
          {getHeaderLabel()}
        </span>
      </div>


      <ol className="stage-list">
        {executionStages.map(
          (stage, index) => {
            const stageState =
              getStageState(
                index,
                plannerState,
                executionState,
                analysisState,
                reportState
              );

            return (
              <li
                className={`stage-item stage-item--${stageState}`}
                key={stage.title}
              >
                <span
                  className={`stage-marker stage-marker--${stageState}`}
                  style={getMarkerStyle(
                    stageState
                  )}
                  aria-hidden="true"
                >
                  {stageState === "done"
                    ? "✓"
                    : stageState === "failed"
                    ? "!"
                    : String(
                        index + 1
                      ).padStart(2, "0")}
                </span>

                <div className="stage-copy">
          <h3>{stage.title}</h3>
                </div>

                <span
                  className={`stage-state stage-state--${stageState}`}
                  style={getStateStyle(
                    stageState
                  )}
                >
                  {getStageLabel(
                    stageState
                  )}
                </span>
              </li>
            );
          }
        )}
      </ol>


      {(plannerState === "planning" || executionState === "running" || analysisState === "running" || reportState === "running") && <p className="timeline-note" role="status">This run returns verified progress at completion.</p>}
    </section>
  );
}


function ExampleResult() {
  return (
    <article
      className="result-card"
      aria-label="Example test result preview"
    >
      <div className="result-topline">
        <div className="result-title-block">
          <p className="eyebrow">
            Signup flow{" "}
            <span className="eyebrow-divider">
              /
            </span>{" "}
            Example test
          </p>

          <h3>
            Reject an invalid email address
          </h3>
        </div>

        <span className="result-status result-status--failed">
          <span aria-hidden="true">
            !
          </span>{" "}
          Failed
        </span>
      </div>


      <div className="result-comparison">
        <div className="result-detail">
          <span className="detail-label">
            Expected result
          </span>

          <p>
            The form should reject the invalid
            email and explain the issue.
          </p>
        </div>

        <div className="result-detail result-detail--actual">
          <span className="detail-label">
            Actual result
          </span>

          <p>
            The form accepted the address and
            continued to the next step.
          </p>
        </div>
      </div>


      <div className="result-bottom">
        <div className="severity-block">
          <span className="detail-label">
            Severity
          </span>

          <span className="severity-value">
            <span aria-hidden="true" /> High
          </span>
        </div>

        <div className="reproduction-block">
          <span className="detail-label">
            Reproduction steps
          </span>

          <ol>
            <li>
              Open the signup page
            </li>

            <li>
              Enter an invalid email and submit
            </li>

            <li>
              Check whether an explanation appears
            </li>
          </ol>
        </div>

        <span className="sample-tag">
          Sample data
        </span>
      </div>
    </article>
  );
}


function PageObservationList({ observations = [] }) {
  const visibleObservations = [];
  const seen = new Set();
  for (const observation of observations) {
    const controls = observation.controls || [];
    const useful = Boolean(
      observation.visible_text?.trim() ||
      observation.title?.trim() ||
      observation.url?.trim() ||
      controls.length ||
      observation.alerts?.some((item) => item?.trim()) ||
      observation.console_errors?.some((item) => item?.trim()) ||
      observation.failed_requests?.some((item) => item?.trim()) ||
      observation.http_errors?.some((item) => item?.trim())
      || observation.collection_errors?.some((item) => item?.trim())
    );
    if (!useful) continue;
    const signature = JSON.stringify([
      observation.url,
      observation.title,
      observation.visible_text,
      controls,
      observation.alerts,
      observation.console_errors,
      observation.failed_requests,
      observation.http_errors,
      observation.collection_errors,
    ]);
    if (seen.has(signature)) continue;
    seen.add(signature);
    visibleObservations.push(observation);
  }
  if (!visibleObservations.length) {
    return (
      <p className="scenario-uncertainty" role="status">
        No usable page observations were recorded for this scenario.
      </p>
    );
  }

  return (
    <details className="page-observations">
      <summary>Observed page state ({visibleObservations.length})</summary>
      <ol>
        {visibleObservations.map((observation, index) => (
          <li key={`${observation.step_index}-${observation.action}-${index}`}>
            <span className="detail-label">
              After {observation.action} · step {observation.step_index + 1}
            </span>
            {(observation.title || observation.url) && (
              <p className="observation-location">
                {[observation.title, observation.url].filter(Boolean).join(" · ")}
              </p>
            )}
            {observation.visible_text?.trim() && <blockquote>{observation.visible_text}</blockquote>}
            {observation.controls?.length > 0 && (
              <ul className="observation-signals">
                {observation.controls.map((control, controlIndex) => (
                  <li key={`${control.label}-${controlIndex}`}>
                    <strong>{control.label || "Form control"}</strong>
                    {` · ${control.kind}`}
                    {control.required ? " · required" : ""}
                    {control.valid === true ? " · valid" : control.valid === false ? " · invalid" : ""}
                    {control.aria_invalid ? " · aria-invalid" : ""}
                    {control.validation_message ? ` · ${control.validation_message}` : ""}
                  </li>
                ))}
              </ul>
            )}
            {[
              ...(observation.alerts || []).map((item) => `Browser alert: ${item}`),
              ...(observation.console_errors || []).map((item) => `Console error: ${item}`),
              ...(observation.failed_requests || []).map((item) => `Failed request: ${item}`),
              ...(observation.http_errors || []).map((item) => `HTTP error: ${item}`),
              ...(observation.collection_errors || []).map((item) => `Observation warning: ${item}`),
            ].filter((item) => item.trim()).length > 0 && (
              <details className="observation-signals-details">
                <summary>Additional browser signals</summary>
                <ul className="observation-signals">
                  {[
                    ...(observation.alerts || []).map((item) => `Browser alert: ${item}`),
                    ...(observation.console_errors || []).map((item) => `Console error: ${item}`),
                    ...(observation.failed_requests || []).map((item) => `Failed request: ${item}`),
                    ...(observation.http_errors || []).map((item) => `HTTP error: ${item}`),
                    ...(observation.collection_errors || []).map((item) => `Observation warning: ${item}`),
                  ].filter((item) => item.trim()).map((item, signalIndex) => (
                    <li key={`${item}-${signalIndex}`}>{item}</li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}


function InvestigationDecisionList({ decisions, showEmpty = true }) {
  const entries = Array.isArray(decisions) ? decisions : [];
  const meaningful = entries.filter((item) =>
    Boolean(
      item.rationale?.trim() ||
      item.outcome?.trim() ||
      item.evidence?.some((entry) => entry?.trim())
    )
  );
  if (!meaningful.length) {
    if (!showEmpty || !Array.isArray(decisions)) return null;
    return (
      <p className="scenario-uncertainty" role="status">
        {entries.length
          ? "Investigation decisions contained no readable details."
          : "No additional investigation decisions were recorded."}
      </p>
    );
  }
  return (
    <details className="page-observations">
      <summary>Investigation decisions ({meaningful.length})</summary>
      <ol>
        {meaningful.map((item, index) => (
          <li key={`${item.decision}-${index}`}>
            <span className="detail-label">{item.decision}</span>
            {item.rationale?.trim() && <p>{item.rationale}</p>}
            {item.evidence?.some((entry) => entry?.trim()) && (
              <ul className="observation-signals">
                {item.evidence.filter((entry) => entry?.trim()).map((evidence, evidenceIndex) => (
                  <li key={`${evidence}-${evidenceIndex}`}>{evidence}</li>
                ))}
              </ul>
            )}
            {item.outcome?.trim() && <p className="observation-location">{item.outcome}</p>}
          </li>
        ))}
      </ol>
    </details>
  );
}


function ExecutionResultCard({
  executionState,
  executionResult,
  executionError,
}) {
  if (executionState === "running") {
    return (
      <article className="result-card plan-state-card">
        <span className="eyebrow">
          Browser execution
        </span>

        <h3>
          Running the generated test plan
        </h3>

        <p>
          Playwright is controlling Chromium
          and executing the planned actions.
        </p>
        <InvestigationDecisionList decisions={executionResult?.investigation} showEmpty={false} />
      </article>
    );
  }

  if (executionState === "failed") {
    const hasAssertionFailure = executionResult?.tests?.some((test) =>
      test.assertions?.some((assertion) => assertion.status === "failed")
    );
    const hasStepFailure = executionResult?.tests?.some((test) =>
      test.steps?.some((step) => step.status === "failed")
    );

    return (
      <article className="result-card plan-state-card plan-state-card--error">
        <span className="eyebrow">
          Browser result
        </span>

        <h3>
          {hasAssertionFailure
            ? "A page assertion failed"
            : "Browser steps could not complete"}
        </h3>

        <p>
          {executionError || (hasAssertionFailure
            ? "Playwright completed the actions and observed an unexpected page outcome."
            : hasStepFailure
              ? "Playwright could not complete one or more planned browser steps."
              : "The browser run ended before verification completed.")}
        </p>

        {executionResult?.tests?.map(
          (test, testIndex) => (
            <section
              className="planned-test"
              key={`${test.name}-${testIndex}`}
            >
              <div className="planned-test-heading">
                <div>
                  <span className="detail-label">
                    Test{" "}
                    {String(
                      testIndex + 1
                    ).padStart(2, "0")}
                  </span>

                  <h4>
                    {test.name}
                  </h4>
                </div>

                <span className="planned-test-tag">
                  {test.scenario_status
                    ? test.scenario_status.replace("_", " ").toUpperCase()
                    : test.status}
                </span>
              </div>

              <ol className="planned-steps">
                {test.steps.map(
                  (
                    step,
                    stepIndex
                  ) => (
                    <li
                      key={`${step.action}-${stepIndex}`}
                    >
                      <span className="plan-action">
                        {step.action}
                      </span>

                      <span className="plan-target">
                        {step.target}
                      </span>

                      <span className="plan-value">
                        {step.status}: {step.message}
                      </span>
                    </li>
                  )
                )}
              </ol>
              {test.assertions?.length > 0 && (
                <ul className="analysis-evidence">
                  {test.assertions.map((assertion, assertionIndex) => (
                    <li key={`${assertion.text}-${assertionIndex}`}>
                      {assertion.status}: {assertion.message}
                    </li>
                  ))}
                </ul>
              )}
              <PageObservationList observations={test.observations} />
            </section>
          )
        )}
      </article>
    );
  }

  if (
    executionState === "done" &&
    executionResult
  ) {
    return (
      <article
        className="result-card generated-plan"
        aria-label="Browser execution result"
      >
        <InvestigationDecisionList decisions={executionResult.investigation} />
        <div className="result-topline">
          <div className="result-title-block">
            <p className="eyebrow">
              Browser execution{" "}
              <span className="eyebrow-divider">
                /
              </span>{" "}
              Real run
            </p>

            <h3>
              Browser actions completed
            </h3>
          </div>

          <span className="result-status result-status--planned">
            <span aria-hidden="true">
              ✓
            </span>{" "}
            Steps passed
          </span>
        </div>


        <div className="planned-tests">
          {executionResult.tests.map(
            (test, testIndex) => (
              <section
                className="planned-test"
                key={`${test.name}-${testIndex}`}
              >
                <div className="planned-test-heading">
                  <div>
                    <span className="detail-label">
                      Test{" "}
                      {String(
                        testIndex + 1
                      ).padStart(2, "0")}
                    </span>

                    <h4>
                      {test.name}
                    </h4>
                  </div>

                  <span className="planned-test-tag">
                    {test.scenario_status
                      ? test.scenario_status.replace("_", " ").toUpperCase()
                      : test.status}
                  </span>
                </div>


                <ol className="planned-steps">
                  {test.steps.map(
                    (
                      step,
                      stepIndex
                    ) => (
                      <li
                        key={`${step.action}-${stepIndex}`}
                      >
                        <span className="plan-action">
                          {step.action}
                        </span>

                        <span className="plan-target">
                          {step.target}
                        </span>

                        <span className="plan-value">
                          {step.status}: {step.message}
                        </span>
                      </li>
                    )
                )}
                </ol>
                {test.assertions?.length > 0 && (
                  <ul className="analysis-evidence">
                    {test.assertions.map((assertion, assertionIndex) => (
                      <li key={`${assertion.text}-${assertionIndex}`}>
                        {assertion.status}: {assertion.message}
                      </li>
                    ))}
                  </ul>
                )}
                <PageObservationList observations={test.observations} />
              </section>
            )
          )}
        </div>


        <p className="plan-disclaimer">
          Browser execution completed with
          real Playwright actions inside
          Chromium.
        </p>
      </article>
    );
  }

  return null;
}


function AnalysisResultCard({ state, analysis, error }) {
  if (state === "idle") {
    return null;
  }

  if (state === "running") {
    return (
      <article className="result-card plan-state-card" aria-live="polite">
        <span className="eyebrow">Result analysis</span>
        <h3>Comparing browser observations</h3>
        <p>FlowCheck is checking the observed steps against the goal and expected result.</p>
      </article>
    );
  }

  if (state === "error") {
    return (
      <article className="result-card plan-state-card plan-state-card--error" role="alert">
        <span className="eyebrow">Analysis error</span>
        <h3>FlowCheck could not analyze this run</h3>
        <p>{error || "Check the analyzer configuration and try again."}</p>
      </article>
    );
  }

  const status = analysis?.status || "blocked";

  return (
    <article className="result-card generated-plan analysis-result" aria-label="Analyzed test result">
      <div className="result-topline">
        <div className="result-title-block">
          <p className="eyebrow">Result analysis</p>
          <h3>{analysis.summary}</h3>
        </div>
        <span className={`result-status analysis-status analysis-status--${status}`}>
          {status}
        </span>
      </div>

      {analysis.root_cause && (
        <div className="analysis-detail">
          <span className="detail-label">Possible cause (hypothesis)</span>
          <p>{analysis.root_cause}</p>
        </div>
      )}

      <div className="analysis-detail">
        <span className="detail-label">Observed evidence</span>
        {analysis.evidence.length ? (
          <ul className="analysis-evidence">
            {analysis.evidence.map((item, index) => (
              <li key={`${item}-${index}`}>{item}</li>
            ))}
          </ul>
        ) : (
          <p>No browser step messages were available as evidence.</p>
        )}
      </div>

      <div className="analysis-detail">
        <span className="detail-label">Reproduction steps</span>
        <ol className="analysis-steps">
          {analysis.reproduction_steps.map((step, index) => (
            <li key={`${step}-${index}`}>{step}</li>
          ))}
        </ol>
      </div>

      <div className="analysis-detail">
        <span className="detail-label">Recommendation</span>
        <p>{analysis.recommendation}</p>
      </div>
    </article>
  );
}


export function PlanResult({ plan, plannerState, plannerError, executionState = "idle", executionResult = null, executionError = "", analysisState = "idle", analysis = null, analysisError = "", reportState = "idle", report = null, reportError = "" }) {
  if (plannerState === "planning") return <article className="result-card state-summary" aria-live="polite"><StatusRing status="running" /><span><strong>Building the test plan</strong><small>The planner is shaping your request into structured scenarios.</small></span></article>;
  if (plannerState === "error") return <article className="result-card state-summary state-summary--error" role="alert"><StatusRing status="fail" /><span><strong>Plan could not be created</strong><small>{plannerError || "Check the planner configuration and try again."}</small></span></article>;
  if (executionState === "running") return <article className="result-card state-summary" aria-live="polite"><span className="indeterminate-spinner" aria-hidden="true" /><span><strong>Executing in Chromium</strong><small>Scenario results appear after browser assertions finish.</small></span></article>;
  if (executionState === "failed") return <article className="result-card state-summary state-summary--error" role="alert"><StatusRing status="blocked" /><span><strong>Agent execution stopped</strong><small>{executionError || "Browser execution ended before a complete result was available."}</small></span></article>;
  if (executionState === "done") {
    if (reportState === "running" || analysisState === "running") return <article className="result-card state-summary" aria-live="polite"><span className="indeterminate-spinner" aria-hidden="true" /><span><strong>{analysisState === "running" ? "Analyzing observations" : "Preparing the report"}</strong><small>Browser execution is complete. Validated outcomes are being summarized.</small></span></article>;
    if (reportState === "error") return <article className="result-card state-summary state-summary--warning" role="status"><StatusRing status="inconclusive" /><span><strong>Report text unavailable</strong><small>{reportError || analysisError || "The saved browser results remain available in scenario details and History."}</small></span></article>;
    if (report) return <ReportResultCard state="done" report={report} />;
    return <article className="result-card state-summary" role="status"><StatusRing status="pass" /><span><strong>Browser result saved</strong><small>Scenario outcomes are available below. {analysisError || ""}</small></span></article>;
  }
  if (!plan) return null;
  return <article className="result-card plan-preview" aria-label="Generated test plan"><div><p className="eyebrow">{plan.flow} · {plan.scope === "broad" ? "Broad" : "Focused"}</p><h3>{plan.summary}</h3></div><span className="plan-preview-count"><strong>{plan.tests.length}</strong><small>scenarios planned</small></span></article>;
}


export function ReportResultCard({ state, report, error }) {
  if (state === "idle") return null;
  if (state === "running") return <article className="result-card state-summary" aria-live="polite"><span className="indeterminate-spinner" aria-hidden="true" /><span><strong>Preparing report</strong><small>Organizing verified outcomes and evidence.</small></span></article>;
  if (state === "error") return <article className="result-card state-summary state-summary--error" role="alert"><StatusRing status="fail" /><span><strong>Report unavailable</strong><small>{error || "The browser result is still saved in History."}</small></span></article>;
  if (!report) return null;
  const status = report.status === "passed" ? "pass" : report.status === "failed" ? "fail" : report.status === "blocked" ? "blocked" : "inconclusive";
  return (
    <article className={`result-card compact-report compact-report--${status}`} aria-label="Final test report">
      <StatusRing status={status} size="lg" />
      <div className="compact-report-copy"><p className="eyebrow">Final report · {report.flow}</p><h3>{report.title}</h3><div className="compact-report-meta"><span>{status.toUpperCase()}</span><span>{report.severity === "not_assessed" ? "Severity not assessed" : `${report.severity} severity`}</span>{report.narrative_source === "fallback" && <span className="fallback-badge">Deterministic result</span>}</div></div>
      <details className="report-detail-disclosure"><summary>Report details</summary>
        {report.narrative_source === "fallback" && <p className="report-source-explanation">AI analysis or report text was unavailable. Outcomes below use validated browser evidence.</p>}
        <div className="report-detail-grid"><div><span className="detail-label">Expected</span><ul>{report.expected_results.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div><div><span className="detail-label">Observed</span><p>{report.actual_result}</p></div></div>
        {report.root_cause && <details className="technical-disclosure"><summary>Possible cause · hypothesis</summary><p>{report.root_cause}</p></details>}
        {report.evidence?.length > 0 && <details className="technical-disclosure"><summary>Evidence · {report.evidence.length}</summary><ul>{report.evidence.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
        {report.reproduction_steps?.length > 0 && <details className="technical-disclosure"><summary>Reproduction steps</summary><ol>{report.reproduction_steps.map((item, index) => <li key={index}>{item}</li>)}</ol></details>}
        {report.recommendation && <details className="technical-disclosure"><summary>Recommendation</summary><p>{report.recommendation}</p></details>}
      </details>
    </article>
  );
}
