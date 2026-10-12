import assert from "node:assert/strict";
import {
  buildComparableTrend,
  comparisonCounts,
  comparisonTransitions,
  countScenarioStatuses,
  countRunScenarios,
  resolveScenarioStatus,
  scenarioDataCategory,
} from "../src/ui-model.js";

const checks = [
  ["scenario status requires an actual result", () => {
    assert.equal(resolveScenarioStatus({}, null, null), "not_run");
    assert.equal(resolveScenarioStatus({}, null, { scenario_status: "pass" }), "pass");
    assert.equal(resolveScenarioStatus({}, { status: "fail" }, { scenario_status: "pass" }), "fail");
    assert.equal(resolveScenarioStatus({}, null, { scenario_status: "browser step passed" }), "not_run");
  }],
  ["mixed scenario statuses stay independent", () => {
    assert.deepEqual(countScenarioStatuses([
      { status: "pass" }, { status: "fail" }, { status: "blocked" },
      { status: "inconclusive" }, { status: "not_run" },
    ]), { pass: 1, fail: 1, blocked: 1, inconclusive: 1, not_run: 1 });
  }],
  ["comparison counts stored outcomes and excludes missing coverage", () => {
    const comparison = { scenarios: [
      { before: "fail", after: "pass" }, { before: "pass", after: "fail" },
      { before: "not_planned", after: "pass" }, { before: "pass", after: "not_planned" },
    ] };
    assert.deepEqual(comparisonCounts(comparison, "before"), { pass: 2, fail: 1, blocked: 0, inconclusive: 0, not_run: 0 });
    assert.deepEqual(comparisonCounts(comparison, "after"), { pass: 2, fail: 1, blocked: 0, inconclusive: 0, not_run: 0 });
  }],
  ["run summaries count planned scenarios from validated run records", () => {
    const run = {
      plan: { tests: [{ scenario_id: "a" }, { scenario_id: "b" }, { scenario_id: "c" }] },
      execution: { tests: [
        { scenario_id: "a", scenario_status: "pass" },
        { scenario_id: "b", scenario_status: "fail" },
      ] },
    };
    assert.deepEqual(countRunScenarios(run), { pass: 1, fail: 1, blocked: 0, inconclusive: 0, not_run: 1 });
  }],
  ["comparison transitions use explicit status pairs", () => {
    const comparison = { comparable: true, scenarios: [
      { scenario_id: "fixed", before: "fail", after: "pass" },
      { scenario_id: "regression", before: "pass", after: "fail" },
      { scenario_id: "same-fail", before: "fail", after: "fail" },
      { scenario_id: "same-pass", before: "pass", after: "pass" },
      { scenario_id: "unknown", before: "inconclusive", after: "pass" },
      { scenario_id: "added", before: "not_planned", after: "pass" },
    ] };
    assert.deepEqual(comparisonTransitions(comparison).map(({ scenario_id, transition }) => [scenario_id, transition]), [
      ["fixed", "fixed"], ["regression", "regression"], ["same-fail", "unresolved"],
      ["same-pass", "stable"], ["unknown", "inconclusive"], ["added", "coverage_changed"],
    ]);
  }],
  ["historical trend needs four runs with identical full coverage", () => {
    const pairs = [
      { previousRun: { id: "r1", created_at: 1 }, selectedRun: { id: "r4", created_at: 4 }, comparison: { comparable: true, coverage: { before: 2, after: 2, added: [], removed: [] }, scenarios: [{ scenario_id: "a", before: "fail", after: "pass" }, { scenario_id: "b", before: "fail", after: "pass" }] } },
      { previousRun: { id: "r2", created_at: 2 }, selectedRun: { id: "r4", created_at: 4 }, comparison: { comparable: true, coverage: { before: 2, after: 2, added: [], removed: [] }, scenarios: [{ scenario_id: "a", before: "pass", after: "pass" }, { scenario_id: "b", before: "fail", after: "pass" }] } },
      { previousRun: { id: "r3", created_at: 3 }, selectedRun: { id: "r4", created_at: 4 }, comparison: { comparable: true, coverage: { before: 2, after: 2, added: [], removed: [] }, scenarios: [{ scenario_id: "a", before: "pass", after: "pass" }, { scenario_id: "b", before: "pass", after: "pass" }] } },
    ];
    assert.deepEqual(buildComparableTrend(pairs), [
      { id: "r1", created_at: 1, fail: 2, measured: 2 },
      { id: "r2", created_at: 2, fail: 1, measured: 2 },
      { id: "r3", created_at: 3, fail: 0, measured: 2 },
      { id: "r4", created_at: 4, fail: 0, measured: 2 },
    ]);
    assert.deepEqual(buildComparableTrend(pairs.slice(0, 2)), []);
    const partial = structuredClone(pairs);
    partial[0].comparison.coverage.added = ["c"];
    assert.deepEqual(buildComparableTrend(partial), []);
  }],
  ["prerequisite labels disclose category, never credential values", () => {
    assert.equal(scenarioDataCategory({ prerequisites: ["valid_test_email", "valid_test_password"] }), "Valid reference account");
    assert.equal(scenarioDataCategory({ prerequisites: ["negative_test_email"] }), "Negative test data");
    assert.equal(scenarioDataCategory({ prerequisites: [] }), "No credentials");
  }],
];

for (const [name, run] of checks) {
  run();
  process.stdout.write(`PASS ${name}\n`);
}
process.stdout.write(`\n${checks.length}/${checks.length} frontend model checks passed.\n`);
