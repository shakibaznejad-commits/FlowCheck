export const SCENARIO_STATUSES = ["pass", "fail", "blocked", "inconclusive", "not_run"];

export function resolveScenarioStatus(test, reportItem, executionItem) {
  const candidate = reportItem?.status || executionItem?.scenario_status;
  return SCENARIO_STATUSES.includes(candidate) ? candidate : "not_run";
}

export function countScenarioStatuses(scenarios) {
  const counts = Object.fromEntries(SCENARIO_STATUSES.map((status) => [status, 0]));
  for (const scenario of scenarios) {
    const status = SCENARIO_STATUSES.includes(scenario.status) ? scenario.status : "not_run";
    counts[status] += 1;
  }
  return counts;
}

export function comparisonCounts(comparison, side) {
  const counts = Object.fromEntries(SCENARIO_STATUSES.map((status) => [status, 0]));
  for (const scenario of comparison?.scenarios || []) {
    const status = scenario[side];
    if (Object.hasOwn(counts, status)) counts[status] += 1;
  }
  return counts;
}

export function countRunScenarios(run) {
  const tests = run?.plan?.tests || [];
  if (!tests.length) {
    const counts = Object.fromEntries(SCENARIO_STATUSES.map((status) => [status, 0]));
    for (const status of SCENARIO_STATUSES) counts[status] = Math.max(0, Number(run?.scenario_counts?.[status]) || 0);
    return counts;
  }
  const executionById = new Map((run?.execution?.tests || []).map((item) => [item.scenario_id, item]));
  const reportById = new Map((run?.report?.scenario_results || []).map((item) => [item.scenario_id, item]));
  return countScenarioStatuses(tests.map((test) => ({
    status: resolveScenarioStatus(test, reportById.get(test.scenario_id), executionById.get(test.scenario_id)),
  })));
}

export function comparisonTransitions(comparison) {
  return (comparison?.scenarios || []).map((scenario) => {
    const { before, after } = scenario;
    let transition = "inconclusive";
    if (before === "not_planned" || after === "not_planned") transition = "coverage_changed";
    else if (before === "fail" && after === "pass") transition = "fixed";
    else if (before === "pass" && after === "fail") transition = "regression";
    else if (before === "fail" && after === "fail") transition = "unresolved";
    else if (before === "pass" && after === "pass") transition = "stable";
    return { ...scenario, transition };
  });
}

export function buildComparableTrend(pairs) {
  if (!Array.isArray(pairs) || pairs.length < 3) return [];
  const selectedId = pairs[0]?.selectedRun?.id;
  const selectedTime = pairs[0]?.selectedRun?.created_at;
  if (!selectedId || !Number.isFinite(selectedTime)) return [];
  let scenarioIds = null;
  let selectedStatuses = null;
  const points = [];

  for (const pair of pairs) {
    const { comparison, previousRun, selectedRun } = pair || {};
    if (!comparison?.comparable
      || selectedRun?.id !== selectedId
      || selectedRun?.created_at !== selectedTime
      || !previousRun?.id
      || !Number.isFinite(previousRun.created_at)
      || comparison.coverage?.added?.length
      || comparison.coverage?.removed?.length
      || comparison.coverage?.before !== comparison.coverage?.after) return [];
    const scenarios = comparison.scenarios || [];
    const ids = scenarios.map((item) => item.scenario_id).sort();
    if (!ids.length || scenarios.length !== comparison.coverage.before) return [];
    if (scenarioIds && ids.join("\u0000") !== scenarioIds.join("\u0000")) return [];
    scenarioIds ||= ids;
    const previousStatuses = Object.fromEntries(scenarios.map((item) => [item.scenario_id, item.before]));
    const currentStatuses = Object.fromEntries(scenarios.map((item) => [item.scenario_id, item.after]));
    if (Object.values(previousStatuses).some((status) => !["pass", "fail"].includes(status))
      || Object.values(currentStatuses).some((status) => !["pass", "fail"].includes(status))) return [];
    if (selectedStatuses && ids.some((id) => selectedStatuses[id] !== currentStatuses[id])) return [];
    selectedStatuses ||= currentStatuses;
    points.push({
      id: previousRun.id,
      created_at: previousRun.created_at,
      fail: Object.values(previousStatuses).filter((status) => status === "fail").length,
      measured: scenarios.length,
    });
  }

  points.push({
    id: selectedId,
    created_at: selectedTime,
    fail: Object.values(selectedStatuses || {}).filter((status) => status === "fail").length,
    measured: scenarioIds?.length || 0,
  });
  const uniquePoints = [...new Map(points.map((point) => [point.id, point])).values()]
    .sort((a, b) => a.created_at - b.created_at);
  return uniquePoints.length >= 4 ? uniquePoints : [];
}

export function scenarioDataCategory(test) {
  const prerequisites = test?.prerequisites || [];
  if (prerequisites.some((item) => item.startsWith("negative_"))) return "Negative test data";
  if (prerequisites.some((item) => item.startsWith("valid_test_") || item === "test_email" || item === "test_password")) return "Valid reference account";
  return "No credentials";
}
