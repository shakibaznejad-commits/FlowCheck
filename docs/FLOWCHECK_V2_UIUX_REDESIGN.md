# FlowCheck V2 UI/UX redesign

## Direction

**Graphite Lab**: a restrained, dark-first workbench for developers and QA engineers. Graphite surfaces keep browser evidence and status colors legible; electric blue marks actions and focus, while pass, fail, blocked, and inconclusive each have a separate semantic token. IBM Plex Sans remains the UI face; compact labels use the existing monospace role. Light mode is available from the header.

## Component structure

- `App` owns authenticated workspace state and the theme preference.
- `ScenarioSetup` keeps scenario selection, expansion, credentials, and authorization as separate controls.
- `ScenarioResults` presents verified per-scenario outcomes, progressive evidence, and the screenshot viewer.
- `StatusRing`, `StatusDistribution`, and `ScreenshotViewer` are reusable visual primitives in `visuals.jsx`.
- `RunHistory` separates All runs, Pinned, and Suites into focused collections, with dedicated run-detail and comparison views.
- `visuals.jsx` provides direct-label SVG distribution, failure-count, and comparable-history charts.

## State and disclosure rules

Status rings map only stored scenario status. During the synchronous browser request, a single indeterminate indicator shows that Playwright is working; the API has no scenario-progress stream, so individual live statuses and percentages are not invented. Browser execution health is displayed separately from the website result. Scenario descriptions and expected behavior open on demand; assertions, steps, observations, reproduction details, and screenshots require further interaction.

Runs already persist through the existing backend lifecycle; reusable suites remain separate. Pinned runs use an additive SQLite boolean column with a default of false, an additive migration, and authenticated owner-scoped updates. History summaries include status counts computed from stored execution results. Comparisons use stable scenario IDs and report fixed, regression, unresolved, stable, inconclusive, and coverage-changed states. Independent charts remain available when scenarios cannot be compared.

## Accessibility and responsive behavior

Semantic buttons, checkboxes, labels, expanded state, text status, visible focus, a native modal dialog, keyboard screenshot navigation, and reduced-motion rules are used. Layouts collapse to one column below tablet widths; run filters, comparison rows, and evidence galleries reflow for narrow screens. SVG charts provide accessible text summaries. The theme preference is stored locally; run data and credentials are not.

## Backend changes

- `test_runs.pinned` is added with a backward-compatible SQLite migration.
- `PATCH /api/runs/{run_id}/pin` updates only the authenticated owner's run.
- `/api/runs` adds `pinned` and `scenario_counts` fields. Existing routes and run/suite behavior remain intact.

## Verification and limitations

Frontend model checks cover status integrity, mixed distributions, missing comparison coverage, transition labels, strict historical comparability, and credential category labels. Backend migration and ownership tests cover the pin field and endpoint. The API only returns progress after synchronous execution, so the UI uses a truthful indeterminate running state instead of invented per-scenario progress. A historical failure trend is shown only when at least four runs have identical, fully verified scenario coverage and stable selected-run results.

## Visual-first review

The default workspace now prioritizes the target URL, test goal, and concise five-stage pipeline. The prefilled example failure card was removed so sample output cannot be mistaken for a real result. History presents one collection at a time; run details keep scenario results primary and place the report and run metadata behind disclosures. Comparison charts use stored scenario outcomes, and incompatible runs remain explicitly incomparable. No backend behavior was changed for this visual pass.

Visual verification covered the real local UI at 320, 360, 390, 768, 1024, and 1440 CSS pixels, including a saved failed run, evidence dialog, comparison, suite collection, and both themes. No document or key-panel horizontal overflow was detected; browser console error and warning output was empty. Frontend model checks and the production build were rerun after the final edits.
