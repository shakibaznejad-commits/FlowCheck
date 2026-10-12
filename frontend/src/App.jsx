import React, { useEffect, useState } from "react";

import AuthScreen from "./AuthScreen.jsx";
import RunHistory from "./RunHistory.jsx";
import {
  AppHeader,
  ExecutionTimeline,
  PlanResult,
  ScenarioResults,
  ScenarioSetup,
  TestWorkspace,
} from "./components.jsx";


export default function App() {
  const [activeView, setActiveView] = useState("workspace");
  const [authState, setAuthState] = useState("checking");
  const [user, setUser] = useState(null);
  const [backendState, setBackendState] =
    useState("checking");
  const [theme, setTheme] = useState(() => localStorage.getItem("flowcheck-theme") || "dark");

  useEffect(() => {
    localStorage.setItem("flowcheck-theme", theme);
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const [plannerState, setPlannerState] =
    useState("idle");

  const [executionState, setExecutionState] =
    useState("idle");

  const [analysisState, setAnalysisState] =
    useState("idle");

  const [reportState, setReportState] =
    useState("idle");

  const [plan, setPlan] =
    useState(null);

  const [executionResult, setExecutionResult] =
    useState(null);

  const [analysis, setAnalysis] =
    useState(null);

  const [report, setReport] =
    useState(null);

  const [preparedRun, setPreparedRun] = useState(null);
  const [activeTargetUrl, setActiveTargetUrl] = useState("");
  const [activeGoal, setActiveGoal] = useState("");

  const [plannerError, setPlannerError] =
    useState("");

  const [executionError, setExecutionError] =
    useState("");

  const [analysisError, setAnalysisError] =
    useState("");

  const [reportError, setReportError] =
    useState("");


  useEffect(() => {
    let active = true;

    fetch("/api/health")
      .then((response) => {
        if (!response.ok) {
          throw new Error(
            "Health check failed"
          );
        }

        return response.json();
      })
      .then((health) => {
        if (active) {
          setBackendState(
            health.status === "ok"
              ? "online"
              : "offline"
          );
        }
      })
      .catch(() => {
        if (active) {
          setBackendState("offline");
        }
      });

    return () => {
      active = false;
    };
  }, []);


  useEffect(() => {
    let active = true;

    fetch("/api/auth/me", { credentials: "same-origin" })
      .then((response) => {
        if (response.status === 401) {
          return null;
        }

        if (!response.ok) {
          throw new Error("Session check failed");
        }

        return response.json();
      })
      .then((currentUser) => {
        if (active) {
          setUser(currentUser);
          setAuthState(currentUser ? "authenticated" : "anonymous");
        }
      })
      .catch(() => {
        if (active) {
          setUser(null);
          setAuthState("anonymous");
        }
      });

    return () => {
      active = false;
    };
  }, []);


  async function handleLogout() {
    const response = await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    });

    if (response.ok) {
      setUser(null);
      setAuthState("anonymous");
      setActiveView("workspace");
      resetPlan();
      setReportState("idle");
      setReport(null);
    }
  }


  async function executePlan(
    url,
    goal,
    generatedPlan,
    options = {},
  ) {
    setExecutionState("running");
    setAnalysisState("idle");
    setReportState("idle");
    setExecutionResult(null);
    setAnalysis(null);
    setReport(null);
    setExecutionError("");
    setAnalysisError("");
    setReportError("");

    try {
      const response = await fetch(
        "/api/execute-plan",
        {
          method: "POST",

          headers: {
            "Content-Type":
              "application/json",
          },

          body: JSON.stringify({
            url,
            plan: generatedPlan,
            goal,
            selected_scenario_ids: options.selectedIds || [],
            test_data: options.testData || {},
            authorized_test_account: Boolean(options.authorizedTestAccount),
            source_run_id: options.sourceRunId || null,
          }),
        }
      );

      const payload =
        await response.json();

      if (!response.ok) {
        throw new Error(
          payload.detail ||
            "Browser execution failed."
        );
      }

      setExecutionResult(payload);

      const effectivePlan = payload.effective_plan || generatedPlan;
      setPlan(effectivePlan);

      setExecutionState(payload.agent_status === "failed" ? "failed" : "done");
      setPreparedRun(null);
      setAnalysisState("running");

      try {
        const analysisResponse = await fetch(
          "/api/analyze-results",
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              goal,
              plan: effectivePlan,
              execution: payload,
            }),
          }
        );
        const analysisPayload = await analysisResponse.json();

        if (!analysisResponse.ok) {
          throw new Error(
            analysisPayload.detail ||
              "FlowCheck could not analyze the browser result."
          );
        }

        setAnalysis(analysisPayload);
        setAnalysisState("done");

        setReportState("running");
        try {
          const reportResponse = await fetch(
            "/api/generate-report",
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                url,
                goal,
            plan: effectivePlan,
                execution: payload,
                analysis: analysisPayload,
              }),
            }
          );
          const reportPayload = await reportResponse.json();

          if (!reportResponse.ok) {
            throw new Error(
              reportPayload.detail ||
                "FlowCheck could not generate the final report."
            );
          }

          setReport(reportPayload);
          setReportState("done");
        } catch (error) {
          setReportError(
            error instanceof Error
              ? error.message
              : "FlowCheck could not generate the final report."
          );
          setReportState("error");
        }
      } catch (error) {
        setAnalysisError(
          error instanceof Error
            ? error.message
            : "FlowCheck could not analyze the browser result."
        );
        setAnalysisState("error");
      }
    } catch (error) {
      setExecutionError(
        error instanceof Error
          ? error.message
          : "Browser execution failed."
      );

      setExecutionState("failed");
    }
  }


  async function handlePlanRequest({
    url,
    goal,
  }) {
    setPlannerState("planning");
    setExecutionState("idle");
    setAnalysisState("idle");
    setReportState("idle");

    setPlan(null);
    setExecutionResult(null);
    setAnalysis(null);
    setReport(null);

    setPlannerError("");
    setExecutionError("");
    setAnalysisError("");
    setReportError("");

    try {
      const response = await fetch(
        "/api/test-plan",
        {
          method: "POST",

          headers: {
            "Content-Type":
              "application/json",
          },

          body: JSON.stringify({
            url,
            goal,
          }),
        }
      );

      const payload =
        await response.json();

      if (!response.ok) {
        throw new Error(
          payload.detail ||
            "FlowCheck could not create a test plan."
        );
      }

      setPlan(payload);

      setPlannerState(
        "generated"
      );

      setPreparedRun({ url, goal, sourceRunId: null });
      setActiveTargetUrl(url);
      setActiveGoal(goal);

      if (payload.scope === "broad" || payload.tests.some((test) => test.prerequisites?.length)) {
        return;
      }

      await executePlan(
        url,
        goal,
        payload
      );
    } catch (error) {
      setPlannerError(
        error instanceof Error
          ? error.message
          : "FlowCheck could not create a test plan."
      );

      setPlannerState("error");
      setExecutionState("idle");
    }
  }


  async function runPreparedScenarios({ selectedIds, testData, authorizedTestAccount }) {
    if (!preparedRun || !plan) return;
    const run = preparedRun;
    await executePlan(run.url, run.goal, plan, {
      selectedIds,
      testData,
      authorizedTestAccount,
      sourceRunId: run.sourceRunId,
    });
  }


  async function saveCurrentSuite() {
    const url = preparedRun?.url || activeTargetUrl;
    const goal = preparedRun?.goal || activeGoal;
    if (!url || !goal || !plan) throw new Error("Create a plan first.");
    const response = await fetch("/api/suites", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: plan.summary.slice(0, 160) || "Saved FlowCheck suite",
        url,
        goal,
        plan,
        source_run_id: preparedRun?.sourceRunId || executionResult?.run_id || null,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Could not save this suite.");
  }


  function prepareReplay(run) {
    setActiveView("workspace");
    setPlan(run.plan);
    setActiveTargetUrl(run.target_url);
    setPreparedRun({
      url: run.target_url,
      goal: run.goal,
      sourceRunId: run.source_run_id || run.id,
    });
    setActiveGoal(run.goal);
    setPlannerState("generated");
    setExecutionState("idle");
    setAnalysisState("idle");
    setReportState("idle");
    setExecutionResult(null);
    setAnalysis(null);
    setReport(null);
  }


  function resetPlan() {
    setPlannerState("idle");
    setExecutionState("idle");
    setAnalysisState("idle");

    setPlan(null);
    setExecutionResult(null);
    setAnalysis(null);
    setPreparedRun(null);

    setPlannerError("");
    setExecutionError("");
    setAnalysisError("");
  }


  function getResultsEyebrow() {
    if (reportState !== "idle") {
      return "Final test report";
    }

    if (analysisState !== "idle") {
      return "Result analysis";
    }

    if (
      executionState === "done" ||
      executionState === "failed"
    ) {
      return "Browser result";
    }

    if (
      plannerState === "generated"
    ) {
      return "Planner output";
    }

    return "What you’ll get";
  }


  function getResultsHeading() {
    if (reportState === "running") {
      return "Generating final report";
    }

    if (reportState === "done") {
      return "Final report ready";
    }

    if (reportState === "error") {
      return "Report generation failed";
    }

    if (analysisState === "running") {
      return "Analyzing browser result";
    }

    if (analysisState === "done") {
      return "Test analysis ready";
    }

    if (analysisState === "error") {
      return "Result analysis failed";
    }

    if (executionState === "running") {
      return "Running browser test";
    }

    if (executionState === "done") {
      return "Browser test completed";
    }

    if (executionState === "failed") {
      return "Browser test found an issue";
    }

    if (
      plannerState === "generated"
    ) {
      return "Structured test plan";
    }

    if (
      plannerState === "planning"
    ) {
      return "Building your test plan";
    }

    if (
      plannerState === "error"
    ) {
      return "Plan generation failed";
    }

    return (
      "A useful result, not just a pass or fail"
    );
  }


  function getPreviewLabel() {
    if (reportState === "running") {
      return "Generating";
    }

    if (reportState === "done") {
      return report?.status || "Reported";
    }

    if (reportState === "error") {
      return "Report failed";
    }

    if (analysisState === "running") {
      return "Analyzing";
    }

    if (analysisState === "done") {
      return analysis?.status || "Analyzed";
    }

    if (analysisState === "error") {
      return "Analysis failed";
    }

    if (executionState === "running") {
      return "Executing";
    }

    if (executionState === "done") {
      return "Completed";
    }

    if (executionState === "failed") {
      return "Failed";
    }

    if (
      plannerState === "generated"
    ) {
      return "Plan ready";
    }

    if (
      plannerState === "idle"
    ) {
      return "Example report";
    }

    return "Planner";
  }


  if (authState === "checking") {
    return (
      <div className="app-shell">
        <AppHeader backendState={backendState} theme={theme} onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")} />
        <main className="auth-main" id="main-content">
          <p className="auth-checking" role="status" aria-live="polite">
            Checking your saved session…
          </p>
        </main>
      </div>
    );
  }

  if (!user) {
    return (
      <AuthScreen
        backendState={backendState}
        theme={theme}
        onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")}
        onAuthenticated={(authenticatedUser) => {
          setUser(authenticatedUser);
          setAuthState("authenticated");
        }}
      />
    );
  }


  return (
    <div className="app-shell" data-theme={theme}>
      <a
        className="skip-link"
        href="#main-content"
      >
        Skip to main content
      </a>


      <AppHeader
        backendState={backendState}
        theme={theme}
        onToggleTheme={() => setTheme((current) => current === "dark" ? "light" : "dark")}
        user={user}
        onLogout={handleLogout}
      />


      <main
        id="main-content"
        className="main-content"
      >
        <nav className="workspace-navigation" aria-label="Main workspace">
          <button
            type="button"
            className={`workspace-navigation-link${activeView === "workspace" ? " is-current" : ""}`}
            aria-current={activeView === "workspace" ? "page" : undefined}
            onClick={() => setActiveView("workspace")}
          >
            Workspace
          </button>
          <button
            type="button"
            className={`workspace-navigation-link${activeView === "history" ? " is-current" : ""}`}
            aria-current={activeView === "history" ? "page" : undefined}
            onClick={() => setActiveView("history")}
          >
            History
          </button>
        </nav>

        {activeView === "history" ? (
          <RunHistory onReplay={prepareReplay} />
        ) : (
          <>
        <section
          className="page-intro"
          aria-labelledby="page-title"
        >
          <p className="eyebrow">
            <span className="eyebrow-mark" />
            Test workspace
          </p>

          <h1 id="page-title">
            Test a web workflow
          </h1>

          <p className="page-description">
            Give FlowCheck a website and a behavior to verify.
          </p>
        </section>


        <div className="workspace-grid">
          <TestWorkspace
            plannerState={plannerState}
            isBusy={
              plannerState === "planning" ||
              executionState === "running" ||
              analysisState === "running" ||
              reportState === "running"
            }
            executionState={executionState}
            analysisState={analysisState}
            plannerError={
              plannerError ||
              executionError
            }
            onSubmit={
              handlePlanRequest
            }
            onInputChange={
              resetPlan
            }
          />

          <ExecutionTimeline
            plannerState={
              plannerState
            }
            executionState={
              executionState
            }
            analysisState={analysisState}
            reportState={reportState}
          />
        </div>

        {plannerState === "generated" && preparedRun && !executionResult && (
          <ScenarioSetup
            plan={plan}
            onRun={runPreparedScenarios}
            onSave={saveCurrentSuite}
            isBusy={executionState === "running" || analysisState === "running" || reportState === "running"}
          />
        )}


        {(plannerState !== "idle" || plan || executionResult || report) && <section
          className="results-section"
          aria-labelledby="results-heading"
        >
          <div className="section-title-row">
            <div>
              <p className="eyebrow">
                {getResultsEyebrow()}
              </p>

              <h2 id="results-heading">
                {getResultsHeading()}
              </h2>
            </div>

            <span className="preview-label">
              {getPreviewLabel()}
            </span>
          </div>


          <PlanResult
            plan={plan}
            plannerState={
              plannerState
            }
            plannerError={
              plannerError
            }
            executionState={
              executionState
            }
            executionResult={
              executionResult
            }
            executionError={
              executionError
            }
            analysisState={analysisState}
            analysis={analysis}
            analysisError={analysisError}
            reportState={reportState}
            report={report}
            reportError={reportError}
          />
        </section>}

        <ScenarioResults plan={plan} execution={executionResult} targetUrl={activeTargetUrl} report={report} reportState={reportState} executionState={executionState} />


        <footer className="app-footer">
          <span>
            FlowCheck{" "}
            <span aria-hidden="true">
              ·
            </span>{" "}
            Evidence-backed browser QA
          </span>

          <span>
            Plan · Run · Review
          </span>
        </footer>
          </>
        )}
      </main>
    </div>
  );
}
