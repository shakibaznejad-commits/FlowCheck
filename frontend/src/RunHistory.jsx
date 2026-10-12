import React, { useEffect, useMemo, useState } from "react";
import { ReportResultCard, ScenarioResults } from "./components.jsx";
import {
  FailureBarChart,
  FailureTrendChart,
  ScreenshotViewer,
  StatusRing,
  StatusStackedChart,
} from "./visuals.jsx";
import {
  buildComparableTrend,
  comparisonTransitions,
  countRunScenarios,
} from "./ui-model.js";

const COLLECTIONS = [
  { id: "runs", label: "All runs" },
  { id: "pinned", label: "Pinned" },
  { id: "suites", label: "Suites" },
];

const STATUS_LABELS = {
  pass: "Pass", fail: "Fail", blocked: "Blocked", inconclusive: "Inconclusive",
  not_run: "Not run", not_planned: "Not planned",
};

function statusForRun(status) {
  return status === "passed" ? "pass" : status === "failed" ? "fail" : status || "inconclusive";
}

function formatDate(timestamp, compact = false) {
  if (!timestamp) return "Unknown date";
  return new Intl.DateTimeFormat(undefined, compact
    ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
    : { dateStyle: "medium", timeStyle: "short" }).format(new Date(timestamp * 1000));
}

function domainFor(value) {
  try { return new URL(value).hostname; } catch { return value || "Unknown website"; }
}

function Icon({ name }) {
  const shared = { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" };
  if (name === "star") return <svg {...shared}><path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9L12 3Z" /></svg>;
  if (name === "back") return <svg {...shared}><path d="m15 18-6-6 6-6M9 12h12" /></svg>;
  if (name === "compare") return <svg {...shared}><path d="M4 6h16M4 12h10M4 18h16" /><path d="m16 9 3 3-3 3" /></svg>;
  return <svg {...shared}><path d="M12 5v14M5 12h14" /></svg>;
}

function RunCard({ run, selected, onOpen, onPin }) {
  const counts = run.scenario_counts || {};
  const status = statusForRun(run.status);
  return (
    <li className={`explorer-run${selected ? " is-selected" : ""}`}>
      <button className="explorer-run-open" type="button" onClick={() => onOpen(run.id)} aria-label={`Open ${domainFor(run.target_domain || run.target_url)}, ${run.flow}, ${STATUS_LABELS[status] || status}, ${formatDate(run.created_at)}`}>
        <span className="explorer-run-icon"><StatusRing status={status} size="sm" /></span>
        <span className="explorer-run-main">
          <strong className="explorer-domain">{run.target_domain || domainFor(run.target_url)}</strong>
          <span className="explorer-run-meta"><span>{run.flow}</span><i aria-hidden="true" />{run.scope || "focused"}<i aria-hidden="true" /><time dateTime={new Date(run.created_at * 1000).toISOString()}>{formatDate(run.created_at, true)}</time></span>
          <span className="explorer-run-distribution"><StatusStackedChart counts={counts} label={`${run.flow} run outcomes`} /></span>
        </span>
        <span className={`explorer-run-status explorer-run-status--${status}`}>{STATUS_LABELS[status] || status}</span>
      </button>
      <button className={`explorer-pin${run.pinned ? " is-pinned" : ""}`} type="button" aria-label={`${run.pinned ? "Unpin" : "Pin"} run for ${domainFor(run.target_domain || run.target_url)}`} aria-pressed={Boolean(run.pinned)} onClick={() => onPin(run)}><Icon name="star" /></button>
    </li>
  );
}

function SuiteCard({ suite, onReplay }) {
  const count = suite.plan?.tests?.length || 0;
  return (
    <article className="explorer-suite">
      <div className="explorer-suite-mark" aria-hidden="true"><span /><span /><span /></div>
      <div className="explorer-suite-copy"><h3 title={suite.name}>{suite.name}</h3><span>{domainFor(suite.url)} <i aria-hidden="true" />{suite.plan?.flow || "Web flow"} <i aria-hidden="true" />{count} scenarios</span></div>
      <button className="quiet-button explorer-suite-replay" type="button" onClick={() => onReplay(suite.id)}>Open</button>
    </article>
  );
}

function RunCollection({ runs, suites, collection, selectedId, onOpen, onPin, onReplay }) {
  if (collection === "suites") {
    return suites.length ? <div className="explorer-suite-list">{suites.map((suite) => <SuiteCard key={suite.id} suite={suite} onReplay={onReplay} />)}</div>
      : <EmptyState icon="suites" title="No saved suites" detail="Save a planned test to reuse it here." />;
  }
  const visibleRuns = collection === "pinned" ? runs.filter((run) => run.pinned) : runs;
  if (!visibleRuns.length) return <EmptyState icon="runs" title={collection === "pinned" ? "Nothing pinned" : "No matching runs"} detail={collection === "pinned" ? "Star a run to keep it close." : "Completed runs will appear here."} />;
  return <ul className="explorer-run-list">{visibleRuns.map((run) => <RunCard key={run.id} run={run} selected={selectedId === run.id} onOpen={onOpen} onPin={onPin} />)}</ul>;
}

function EmptyState({ title, detail, icon }) {
  return <div className="explorer-empty" data-icon={icon}><span className="empty-symbol" aria-hidden="true"><span /><span /><span /></span><strong>{title}</strong><p>{detail}</p></div>;
}

function RunOverview({ run, onReplay, onCompare, pinBusy, onPin }) {
  const counts = countRunScenarios(run);
  const status = statusForRun(run.status);
  const evidenceCount = run.execution?.evidence?.length || 0;
  return (
    <section className="run-overview" aria-labelledby="run-overview-title">
      <div className={`run-result-emblem run-result-emblem--${status}`}><StatusRing status={status} size="lg" label={`Website result: ${STATUS_LABELS[status] || status}`} /><span>{STATUS_LABELS[status] || status}</span></div>
      <div className="run-overview-content">
        <p className="run-overview-context">{domainFor(run.target_domain || run.target_url)} <i aria-hidden="true" />{run.flow} <i aria-hidden="true" />{formatDate(run.created_at)}</p>
        <h2 id="run-overview-title">{run.plan?.tests?.length || 0} scenarios</h2>
        <StatusStackedChart counts={counts} label={`Run outcome distribution for ${domainFor(run.target_url)}`} />
        <div className="run-overview-stats">
          <span><strong>{counts.pass}</strong> pass</span><span><strong>{counts.fail}</strong> fail</span><span><strong>{counts.blocked}</strong> blocked</span><span><strong>{counts.inconclusive}</strong> inconclusive</span><span><strong>{counts.not_run}</strong> not run</span>
        </div>
      </div>
      <div className="run-overview-actions">
        <button type="button" className="run-button" onClick={() => onReplay(run)}>Run again <span aria-hidden="true">↗</span></button>
        <button type="button" className="compare-primary" onClick={onCompare}><Icon name="compare" />Compare</button>
        <button className={`explorer-pin explorer-pin--large${run.pinned ? " is-pinned" : ""}`} type="button" disabled={pinBusy} aria-label={`${run.pinned ? "Unpin" : "Pin"} selected run`} aria-pressed={Boolean(run.pinned)} onClick={() => onPin(run)}><Icon name="star" /></button>
        <span className="run-evidence-count"><span aria-hidden="true">▧</span>{evidenceCount} evidence</span>
      </div>
    </section>
  );
}

function ScenarioTransition({ scenario, beforeRun, afterRun }) {
  const [expanded, setExpanded] = useState(false);
  const beforeStatus = scenario.before === "not_planned" ? "not_planned" : scenario.before;
  const afterStatus = scenario.after === "not_planned" ? "not_planned" : scenario.after;
  const label = {
    resolved: "Fixed", new_failure: "Regression", unchanged: scenario.before === "fail" ? "Unresolved" : "Stable",
    insufficient_data: "Insufficient data", added: "Added", removed: "Removed",
  }[scenario.change] || scenario.change.replaceAll("_", " ");
  const previousIndex = beforeRun?.plan?.tests?.findIndex((test) => test.scenario_id === scenario.scenario_id) ?? -1;
  const currentIndex = afterRun?.plan?.tests?.findIndex((test) => test.scenario_id === scenario.scenario_id) ?? -1;
  const previousEvidence = previousIndex < 0 ? [] : (beforeRun.execution?.evidence || []).filter((item) => item.test_index === previousIndex);
  const currentEvidence = currentIndex < 0 ? [] : (afterRun.execution?.evidence || []).filter((item) => item.test_index === currentIndex);
  return (
    <article className={`transition-row transition-row--${label.toLowerCase().replaceAll(" ", "-")}`}>
      <button type="button" className="transition-row-toggle" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        <span className="transition-pair"><StatusRing status={beforeStatus} size="sm" label={`Previous: ${STATUS_LABELS[beforeStatus] || beforeStatus}`} /><span aria-hidden="true">→</span><StatusRing status={afterStatus} size="sm" label={`Selected: ${STATUS_LABELS[afterStatus] || afterStatus}`} /></span>
        <strong>{scenario.name}</strong><span className="transition-label">{label}</span><span className="transition-chevron" aria-hidden="true">{expanded ? "−" : "+"}</span>
      </button>
      {expanded && <div className="transition-detail"><p>{scenario.expected_result}</p><div className="transition-evidence">
        {previousEvidence.length > 0 && <div><span>Previous evidence</span><ScreenshotViewer screenshots={previousEvidence} runId={beforeRun.evidence_run_id || beforeRun.execution?.run_id} scenarioName={scenario.name} /></div>}
        {currentEvidence.length > 0 && <div><span>Selected evidence</span><ScreenshotViewer screenshots={currentEvidence} runId={afterRun.evidence_run_id || afterRun.execution?.run_id} scenarioName={scenario.name} /></div>}
      </div></div>}
    </article>
  );
}

function ComparisonView({ comparison, previousRun, selectedRun, trend, trendLoading }) {
  const beforeCounts = previousRun ? countRunScenarios(previousRun) : {};
  const afterCounts = countRunScenarios(selectedRun);
  const beforeFailures = beforeCounts.fail || 0;
  const afterFailures = afterCounts.fail || 0;
  const transitions = comparisonTransitions(comparison);
  const coverageChanged = Boolean(comparison?.coverage?.added?.length || comparison?.coverage?.removed?.length);
  const compatible = Boolean(comparison?.comparable);
  return (
    <section className="visual-comparison" aria-label="Visual run comparison">
      <div className="comparison-stat-grid">
        <article className="comparison-chart-card"><div className="comparison-chart-heading"><span className="comparison-chart-index">01</span><div><h3>Outcome distribution</h3><p>All planned scenarios for each run</p></div></div>
          {previousRun && <div className="stacked-run-pair"><div><span>Previous <time>{formatDate(previousRun.created_at, true)}</time></span><StatusStackedChart counts={beforeCounts} label={`Previous run: ${beforeCounts.pass || 0} pass, ${beforeFailures} fail, ${beforeCounts.blocked || 0} blocked, ${beforeCounts.inconclusive || 0} inconclusive, ${beforeCounts.not_run || 0} not run`} /></div><div><span>Selected <time>{formatDate(selectedRun.created_at, true)}</time></span><StatusStackedChart counts={afterCounts} label={`Selected run: ${afterCounts.pass || 0} pass, ${afterFailures} fail, ${afterCounts.blocked || 0} blocked, ${afterCounts.inconclusive || 0} inconclusive, ${afterCounts.not_run || 0} not run`} /></div></div>}
        </article>
        <article className="comparison-chart-card"><div className="comparison-chart-heading"><span className="comparison-chart-index">02</span><div><h3>Failed scenarios</h3><p>{compatible ? "Total failures in both runs" : "Independent run totals · scenarios do not match"}</p></div></div><FailureBarChart previous={beforeFailures} current={afterFailures} /></article>
      </div>

      {!comparison ? <div className="comparison-empty"><StatusRing status="pending" /><span>Select a previous run to compare the results.</span></div> : !compatible ? (
        <div className="comparison-compatibility" role="status"><StatusRing status="inconclusive" /><div><strong>Scenario comparison unavailable</strong><p>{comparison.reason || "These runs have no matching stable scenarios."}</p><small>Each chart above reflects the saved results independently.</small></div></div>
      ) : (
        <>
          {coverageChanged && <div className="coverage-notice" role="status"><strong>Partial coverage</strong><span>{comparison.coverage.before} → {comparison.coverage.after} planned</span><small>Added or removed scenarios are not counted as fixed.</small></div>}
          <section className="transition-section" aria-labelledby="transition-title"><div className="section-heading-compact"><div><span className="comparison-chart-index">03</span><h3 id="transition-title">Scenario transitions</h3></div><span>{comparison.scenarios.length}</span></div>
            {transitions.length ? <div className="transition-list">{transitions.map((scenario) => <ScenarioTransition key={scenario.scenario_id} scenario={scenario} beforeRun={previousRun} afterRun={selectedRun} />)}</div> : <p className="chart-empty">No scenario transitions in this comparison.</p>}
          </section>
        </>
      )}

      <section className="trend-section" aria-labelledby="trend-title"><div className="section-heading-compact"><div><span className="comparison-chart-index">{compatible ? "04" : "03"}</span><h3 id="trend-title">Failure trend</h3></div>{trend.length > 0 && <span>{trend.length} comparable runs</span>}</div>
        {trendLoading ? <p className="trend-placeholder" role="status">Checking comparable history…</p> : trend.length >= 4 ? <><FailureTrendChart points={trend} /><details className="chart-data-disclosure"><summary>View trend data</summary><table><thead><tr><th>Run</th><th>Date</th><th>Failed</th><th>Measured</th></tr></thead><tbody>{trend.map((point) => <tr key={point.id}><td>{point.id.slice(0, 8)}</td><td>{formatDate(point.created_at)}</td><td>{point.fail}</td><td>{point.measured}</td></tr>)}</tbody></table></details></> : <p className="trend-placeholder">Trend appears after 4 runs share the same verified scenarios.</p>}
      </section>
    </section>
  );
}

export default function RunHistory({ onReplay }) {
  const [runs, setRuns] = useState([]);
  const [suites, setSuites] = useState([]);
  const [collection, setCollection] = useState("runs");
  const [screen, setScreen] = useState("collection");
  const [selectedRun, setSelectedRun] = useState(null);
  const [previousRun, setPreviousRun] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [trend, setTrend] = useState([]);
  const [trendLoading, setTrendLoading] = useState(false);
  const [compareState, setCompareState] = useState("idle");
  const [compareError, setCompareError] = useState("");
  const [query, setQuery] = useState("");
  const [feature, setFeature] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [filters, setFilters] = useState({ query: "", feature: "", since: "", until: "" });
  const [listState, setListState] = useState("loading");
  const [listError, setListError] = useState("");
  const [detailState, setDetailState] = useState("idle");
  const [detailError, setDetailError] = useState("");
  const [pinError, setPinError] = useState("");
  const [pinBusy, setPinBusy] = useState(false);
  const pinnedCount = useMemo(() => runs.filter((run) => run.pinned).length, [runs]);

  useEffect(() => {
    let active = true;
    const params = new URLSearchParams();
    if (filters.query) params.set("q", filters.query);
    if (filters.feature) params.set("feature", filters.feature);
    if (filters.since) params.set("since", String(Math.floor(new Date(`${filters.since}T00:00:00`).getTime() / 1000)));
    if (filters.until) params.set("until", String(Math.floor(new Date(`${filters.until}T23:59:59`).getTime() / 1000)));
    fetch(`/api/runs?${params}`, { credentials: "same-origin" })
      .then(async (response) => { const data = await response.json(); if (!response.ok) throw new Error(data.detail || "History is unavailable."); return data; })
      .then((data) => { if (active) { setRuns(data); setListState("ready"); } })
      .catch((error) => { if (active) { setListError(error instanceof Error ? error.message : "History is unavailable."); setListState("error"); } });
    fetch("/api/suites", { credentials: "same-origin" })
      .then((response) => response.ok ? response.json() : [])
      .then((data) => { if (active) setSuites(data); })
      .catch(() => { if (active) setSuites([]); });
    return () => { active = false; };
  }, [filters]);

  const comparisonCandidates = useMemo(() => selectedRun ? runs.filter((run) => run.id !== selectedRun.id
    && run.flow === selectedRun.flow
    && run.target_url === selectedRun.target_url
    && run.created_at < selectedRun.created_at).sort((a, b) => b.created_at - a.created_at) : [], [runs, selectedRun]);

  async function openRun(runId) {
    setScreen("detail"); setDetailState("loading"); setDetailError(""); setSelectedRun(null);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`, { credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "This run is unavailable.");
      setSelectedRun(data); setDetailState("ready");
    } catch (error) { setDetailError(error instanceof Error ? error.message : "This run is unavailable."); setDetailState("error"); }
  }

  async function togglePin(run) {
    setPinBusy(true); setPinError("");
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(run.id)}/pin`, { method: "PATCH", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pinned: !run.pinned }) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not update this pin.");
      setRuns((current) => current.map((item) => item.id === run.id ? { ...item, pinned: data.pinned } : item));
      setSelectedRun((current) => current?.id === run.id ? { ...current, pinned: data.pinned } : current);
    } catch (error) { setPinError(error instanceof Error ? error.message : "Could not update this pin."); }
    finally { setPinBusy(false); }
  }

  async function loadTrend(currentRun) {
    const candidates = runs.filter((run) => run.id !== currentRun.id && run.flow === currentRun.flow && run.target_url === currentRun.target_url && run.created_at < currentRun.created_at).sort((a, b) => b.created_at - a.created_at).slice(0, 8);
    if (candidates.length < 3) { setTrend([]); setTrendLoading(false); return; }
    setTrendLoading(true);
    const pairs = await Promise.all(candidates.map(async (run) => {
      try {
        const [comparisonResponse, runResponse] = await Promise.all([
          fetch(`/api/runs/${encodeURIComponent(run.id)}/compare/${encodeURIComponent(currentRun.id)}`, { credentials: "same-origin" }),
          fetch(`/api/runs/${encodeURIComponent(run.id)}`, { credentials: "same-origin" }),
        ]);
        const [compared, fullRun] = await Promise.all([comparisonResponse.json(), runResponse.json()]);
        return comparisonResponse.ok && runResponse.ok ? { previousRun: fullRun, selectedRun: currentRun, comparison: compared } : null;
      } catch { return null; }
    }));
    setTrend(buildComparableTrend(pairs.filter(Boolean)));
    setTrendLoading(false);
  }

  async function openComparison() {
    if (!selectedRun) return;
    setScreen("compare"); setComparison(null); setPreviousRun(null); setCompareError(""); setCompareState("idle");
    void loadTrend(selectedRun);
  }

  async function choosePrevious(runId) {
    if (!selectedRun || !runId) return;
    setCompareState("loading"); setCompareError(""); setComparison(null); setPreviousRun(null);
    try {
      const [compareResponse, previousResponse] = await Promise.all([
        fetch(`/api/runs/${encodeURIComponent(runId)}/compare/${encodeURIComponent(selectedRun.id)}`, { credentials: "same-origin" }),
        fetch(`/api/runs/${encodeURIComponent(runId)}`, { credentials: "same-origin" }),
      ]);
      const [data, previous] = await Promise.all([compareResponse.json(), previousResponse.json()]);
      if (!compareResponse.ok) throw new Error(data.detail || "Comparison is unavailable.");
      if (!previousResponse.ok) throw new Error(previous.detail || "The previous run is unavailable.");
      setComparison(data); setPreviousRun(previous); setCompareState("ready");
    } catch (error) { setCompareError(error instanceof Error ? error.message : "Comparison is unavailable."); setCompareState("error"); }
  }

  async function replaySuite(suiteId) {
    const response = await fetch(`/api/suites/${encodeURIComponent(suiteId)}`, { credentials: "same-origin" });
    const data = await response.json();
    if (response.ok) onReplay?.({ target_url: data.url, goal: data.goal, plan: data.plan, source_run_id: data.source_run_id });
  }

  const screenTitle = screen === "detail" ? "Run details" : screen === "compare" ? "Compare runs" : "History";
  return (
    <section className={`history-explorer history-explorer--${screen}`} aria-labelledby="history-title" data-testid="history-explorer">
      <div className="history-explorer-heading">
        {screen !== "collection" && <button className="history-back" type="button" onClick={() => setScreen(screen === "compare" ? "detail" : "collection")}><Icon name="back" /><span>{screen === "compare" ? "Run details" : "History"}</span></button>}
        <div><p className="eyebrow">{screen === "collection" ? "Your activity" : screen === "compare" ? "Evidence review" : "Saved browser run"}</p><h1 id="history-title">{screenTitle}</h1></div>
        {screen === "collection" && <div className="history-counts"><span><strong>{runs.length}</strong> runs</span><span><strong>{pinnedCount}</strong> pinned</span></div>}
      </div>

      {screen === "collection" && <>
        <div className="history-collection-tabs" role="group" aria-label="History collection">
          {COLLECTIONS.map((item) => <button type="button" key={item.id} aria-pressed={collection === item.id} onClick={() => setCollection(item.id)}>{item.label}<span>{item.id === "runs" ? runs.length : item.id === "pinned" ? pinnedCount : suites.length}</span></button>)}
        </div>
        {collection !== "suites" && <div className="history-collection-toolbar">
          <label className="history-search"><span className="sr-only">Search websites</span><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.5" /><path d="m16 16 4.2 4.2" /></svg><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") setFilters((current) => ({ ...current, query: query.trim() })); }} placeholder="Search website" /></label>
          <label className="history-flow-select"><span className="sr-only">Filter by flow</span><select aria-label="Filter by flow" value={feature} onChange={(event) => { setFeature(event.target.value); setFilters((current) => ({ ...current, feature: event.target.value })); }}><option value="">All flows</option><option value="login">Login</option><option value="signup">Signup</option></select></label>
          <details className="history-date-filters"><summary>Dates</summary><div><label>From<input type="date" value={since} onChange={(event) => setSince(event.target.value)} /></label><label>To<input type="date" value={until} onChange={(event) => setUntil(event.target.value)} /></label><button type="button" className="quiet-button" onClick={() => setFilters((current) => ({ ...current, query: query.trim(), since, until }))}>Apply</button></div></details>
        </div>}
        {pinError && <p className="form-feedback form-feedback--error" role="alert">{pinError}</p>}
        <div className="history-collection-caption"><h2>{collection === "runs" ? "Recent runs" : collection === "pinned" ? "Pinned runs" : "Saved suites"}</h2><span>{collection === "runs" ? runs.length : collection === "pinned" ? pinnedCount : suites.length}</span></div>
        {listState === "loading" && <p className="explorer-status" role="status">Loading history…</p>}
        {listState === "error" && <div className="explorer-status explorer-status--error" role="alert"><strong>History unavailable</strong><span>{listError}</span></div>}
        {listState === "ready" && <RunCollection runs={runs} suites={suites} collection={collection} selectedId={selectedRun?.id} onOpen={openRun} onPin={togglePin} onReplay={replaySuite} />}
      </>}

      {screen === "detail" && detailState === "loading" && <p className="explorer-status" role="status">Loading run…</p>}
      {screen === "detail" && detailState === "error" && <div className="explorer-status explorer-status--error" role="alert">{detailError}</div>}
      {screen === "detail" && selectedRun && <div className="run-detail-view">
        <RunOverview run={selectedRun} onReplay={() => onReplay?.({ target_url: selectedRun.target_url, goal: selectedRun.goal, plan: selectedRun.plan, source_run_id: selectedRun.id })} onCompare={openComparison} onPin={togglePin} pinBusy={pinBusy} />
        <div className="run-detail-columns">
          <section className="run-detail-scenarios" aria-label="Scenario outcomes"><div className="section-heading-compact"><div><span className="comparison-chart-index">01</span><h3>Scenarios</h3></div><span>{selectedRun.plan?.tests?.length || 0}</span></div>
            <ScenarioResults compact plan={selectedRun.plan} execution={selectedRun.execution} targetUrl={selectedRun.target_url} report={selectedRun.report} reportState={selectedRun.report ? "done" : "error"} executionState="done" />
          </section>
          <aside className="run-detail-aside">
            <details className="run-report-disclosure"><summary><span>Final report</span><StatusRing status={statusForRun(selectedRun.status)} size="sm" /></summary><div><ReportResultCard state={selectedRun.report ? "done" : "error"} report={selectedRun.report} error="The saved browser observations remain available in the scenario details." /></div></details>
            <details className="run-agent-disclosure"><summary>Run metadata</summary><dl><div><dt>Agent</dt><dd>{selectedRun.agent_status}</dd></div><div><dt>Scope</dt><dd>{selectedRun.scope}</dd></div><div><dt>URL</dt><dd>{selectedRun.target_url}</dd></div><div><dt>Evidence</dt><dd>{selectedRun.execution?.evidence?.length || 0} screenshots</dd></div></dl></details>
          </aside>
        </div>
      </div>}

      {screen === "compare" && selectedRun && <div className="compare-workspace">
        <div className="compare-current-run"><StatusRing status={statusForRun(selectedRun.status)} size="sm" /><span><strong>{domainFor(selectedRun.target_domain || selectedRun.target_url)}</strong><small>{selectedRun.flow} · {formatDate(selectedRun.created_at, true)}</small></span><span className="compare-current-label">Selected</span></div>
        <div className="compare-picker-card"><label htmlFor="previous-run-picker">Choose an earlier run</label><select id="previous-run-picker" defaultValue="" onChange={(event) => choosePrevious(event.target.value)}><option value="">Select a run…</option>{comparisonCandidates.map((run) => <option key={run.id} value={run.id}>{formatDate(run.created_at)} · {run.target_domain} · {run.status}</option>)}</select><small>Only the same website, page and flow are listed. Scenario compatibility is checked after selection.</small></div>
        {compareError && <p className="form-feedback form-feedback--error" role="alert">{compareError}</p>}
        {compareState === "loading" && <p className="explorer-status" role="status">Comparing saved evidence…</p>}
        {compareState === "ready" && comparison && <ComparisonView comparison={comparison} previousRun={previousRun} selectedRun={selectedRun} trend={trend} trendLoading={trendLoading} />}
        {compareState === "idle" && !comparisonCandidates.length && <EmptyState icon="compare" title="No earlier runs" detail="Run the same website and flow again to compare results." />}
      </div>}
    </section>
  );
}
