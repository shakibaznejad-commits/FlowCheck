import React, { useEffect, useRef, useState } from "react";

const STATUS_MARKS = {
  pass: "✓",
  fail: "×",
  blocked: "⊘",
  inconclusive: "?",
  not_run: "–",
  not_planned: "−",
  pending: "·",
  running: "",
};

export function StatusRing({ status = "not_run", label, size = "md" }) {
  const safeStatus = Object.hasOwn(STATUS_MARKS, status) ? status : "not_run";
  const names = {
    pass: "Pass", fail: "Fail", blocked: "Blocked", inconclusive: "Inconclusive",
    not_run: "Not run", not_planned: "Not planned", pending: "Pending", running: "Running",
  };
  return (
    <span className={`status-ring status-ring--${safeStatus} status-ring--${size}`} role="img" aria-label={label || names[safeStatus]}>
      <span aria-hidden="true">{STATUS_MARKS[safeStatus]}</span>
    </span>
  );
}

export function StatusDistribution({ counts, compact = false }) {
  const statuses = ["pass", "fail", "blocked", "inconclusive", "not_run"];
  const total = statuses.reduce((sum, status) => sum + (counts?.[status] || 0), 0);
  return (
    <div className={`status-distribution${compact ? " status-distribution--compact" : ""}`} aria-label={`${total} scenarios: ${statuses.map((status) => `${counts?.[status] || 0} ${status.replace("_", " ")}`).join(", ")}`}>
      <div className="distribution-bar" aria-hidden="true">
        {statuses.map((status) => <span key={status} className={`distribution-segment distribution-segment--${status}`} style={{ width: `${total ? ((counts?.[status] || 0) / total) * 100 : 0}%` }} />)}
      </div>
      <div className="distribution-legend">
        {statuses.filter((status) => (counts?.[status] || 0) > 0).map((status) => (
          <span className={`distribution-count distribution-count--${status}`} key={status}>
            <StatusRing status={status} size="xs" />
            <strong>{counts[status]}</strong> {status.replace("_", " ")}
          </span>
        ))}
      </div>
    </div>
  );
}

const DISTRIBUTION_ORDER = ["pass", "fail", "blocked", "inconclusive", "not_run"];

export function StatusStackedChart({ counts = {}, label }) {
  const total = DISTRIBUTION_ORDER.reduce((sum, status) => sum + (Number(counts[status]) || 0), 0);
  let offset = 0;
  return (
    <div className="status-chart">
      {total ? (
        <svg className="status-chart-svg" viewBox="0 0 100 12" role="img" aria-label={`${label}: ${DISTRIBUTION_ORDER.map((status) => `${counts[status] || 0} ${status.replace("_", " ")}`).join(", ")}`}>
          <title>{label}</title>
          {DISTRIBUTION_ORDER.map((status) => {
            const count = Number(counts[status]) || 0;
            const width = count / total * 100;
            const x = offset;
            offset += width;
            return count > 0 ? <rect key={status} x={x} y="1" width={width} height="10" rx="2" className={`status-chart-segment status-chart-segment--${status}`}><title>{`${count} ${status.replace("_", " ")}`}</title></rect> : null;
          })}
        </svg>
      ) : <p className="chart-empty">No scenario outcomes</p>}
      <div className="status-chart-legend" aria-label="Scenario outcome counts">
        {DISTRIBUTION_ORDER.filter((status) => Number(counts[status]) > 0).map((status) => (
          <span key={status} className={`status-chart-legend-item status-chart-legend-item--${status}`}><i aria-hidden="true" />{status.replace("_", " ")} <strong>{counts[status]}</strong></span>
        ))}
      </div>
    </div>
  );
}

export function FailureBarChart({ previous = 0, current = 0, previousLabel = "Previous", currentLabel = "Selected" }) {
  const max = Math.max(previous, current, 1);
  const baseY = 128;
  const chartHeight = 92;
  const bars = [
    { label: previousLabel, value: previous, x: 96, className: "failure-chart-bar--previous" },
    { label: currentLabel, value: current, x: 202, className: "failure-chart-bar--current" },
  ];
  return (
    <svg className="failure-chart" viewBox="0 0 340 164" role="img" aria-label={`Failed scenarios: ${previousLabel} ${previous}, ${currentLabel} ${current}`}>
      <title>Failed scenarios per run</title>
      {[0, 0.5, 1].map((fraction) => {
        const y = baseY - chartHeight * fraction;
        return <g key={fraction}><line x1="54" y1={y} x2="314" y2={y} className="chart-grid-line" /><text x="42" y={y + 4} className="chart-axis-label" textAnchor="end">{Math.round(max * fraction)}</text></g>;
      })}
      {bars.map((bar) => {
        const height = bar.value / max * chartHeight;
        return <g key={bar.label}><rect x={bar.x} y={baseY - height} width="42" height={Math.max(height, 1)} rx="6" className={`failure-chart-bar ${bar.className}`}><title>{`${bar.label}: ${bar.value} failed`}</title></rect><text x={bar.x + 21} y={Math.max(baseY - height - 8, 18)} className="chart-value-label" textAnchor="middle">{bar.value}</text><text x={bar.x + 21} y="151" className="chart-category-label" textAnchor="middle">{bar.label}</text></g>;
      })}
    </svg>
  );
}

export function FailureTrendChart({ points = [] }) {
  const width = 360;
  const left = 34;
  const right = 344;
  const top = 20;
  const bottom = 132;
  const max = Math.max(...points.map((point) => point.fail), 1);
  const coordinates = points.map((point, index) => ({
    ...point,
    x: points.length === 1 ? (left + right) / 2 : left + (right - left) * index / (points.length - 1),
    y: bottom - (bottom - top) * point.fail / max,
  }));
  const line = coordinates.map((point) => `${point.x},${point.y}`).join(" ");
  return (
    <svg className="trend-chart" viewBox={`0 0 ${width} 170`} role="img" aria-label={`Failures across ${points.length} comparable runs: ${points.map((point) => `${new Date(point.created_at * 1000).toLocaleDateString()} ${point.fail} failed`).join("; ")}`}>
      <title>Failures over comparable runs</title>
      {[0, 0.5, 1].map((fraction) => { const y = bottom - (bottom - top) * fraction; return <g key={fraction}><line x1={left} y1={y} x2={right} y2={y} className="chart-grid-line" /><text x="25" y={y + 4} className="chart-axis-label" textAnchor="end">{Math.round(max * fraction)}</text></g>; })}
      <polyline points={line} className="trend-chart-line" />
      {coordinates.map((point) => <g key={point.id}><circle cx={point.x} cy={point.y} r="5" className="trend-chart-point"><title>{`${new Date(point.created_at * 1000).toLocaleDateString()}: ${point.fail} failed of ${point.measured}`}</title></circle><text x={point.x} y="153" className="chart-category-label" textAnchor="middle">{new Date(point.created_at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</text></g>)}
    </svg>
  );
}

export function ScreenshotViewer({ screenshots = [], runId, scenarioName }) {
  const dialogRef = useRef(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const [imageError, setImageError] = useState(false);
  const [isOpen, setIsOpen] = useState(false);
  const [thumbnailErrors, setThumbnailErrors] = useState({});
  const active = screenshots[activeIndex];
  const source = active?.url || (runId && active?.filename
    ? `/api/evidence/${encodeURIComponent(runId)}/${encodeURIComponent(active.filename)}`
    : "");

  useEffect(() => {
    if (source) setImageError(false);
  }, [source]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !source || !isOpen) return;
    if (!dialog.open) dialog.showModal();
  }, [source, isOpen]);

  if (!screenshots.length) return null;
  return (
    <div className="evidence-gallery">
      <p className="detail-label">Browser evidence <span>{screenshots.length}</span></p>
      <div className="evidence-thumbnails">
        {screenshots.map((item, index) => {
          const url = item.url || `/api/evidence/${encodeURIComponent(runId || "")}/${encodeURIComponent(item.filename || "")}`;
          return (
            <button
              className="evidence-thumbnail-button"
              key={`${item.filename || item.label}-${index}`}
              type="button"
              onClick={() => { setActiveIndex(index); setImageError(false); setIsOpen(true); }}
              aria-label={`Open ${item.label || (index === 0 ? "final" : "failure")} screenshot for ${scenarioName}`}
            >
              {thumbnailErrors[index] ? <span className="evidence-thumbnail-error">Screenshot unavailable</span> : <img className="evidence-thumbnail" src={url} alt="" loading="lazy" onError={() => setThumbnailErrors((current) => ({ ...current, [index]: true }))} />}
              <span>{item.label || (item.filename?.includes("failure") ? "Failure" : "Final state")}</span>
            </button>
          );
        })}
      </div>
      <dialog className="screenshot-dialog" ref={dialogRef} aria-label={`Screenshot evidence: ${scenarioName}`} onClose={() => setIsOpen(false)} onKeyDown={(event) => {
        if (event.key === "ArrowRight" && activeIndex < screenshots.length - 1) setActiveIndex(activeIndex + 1);
        if (event.key === "ArrowLeft" && activeIndex > 0) setActiveIndex(activeIndex - 1);
      }}>
        <div className="screenshot-dialog-header">
          <div><strong>{scenarioName}</strong><span>{active?.label || "Browser evidence"} · {activeIndex + 1} of {screenshots.length}</span></div>
          <button type="button" className="icon-button" aria-label="Close screenshot viewer" onClick={() => dialogRef.current?.close()}>×</button>
        </div>
        {imageError ? (
          <div className="screenshot-error" role="status"><StatusRing status="blocked" /> Screenshot is unavailable. Check that this run belongs to your account.</div>
        ) : (
          <img className="screenshot-full" src={source} alt={`${active?.label || "Browser"} screenshot for ${scenarioName}`} onError={() => setImageError(true)} />
        )}
        {screenshots.length > 1 && <div className="screenshot-dialog-actions">
          <button className="quiet-button" type="button" disabled={activeIndex === 0} onClick={() => setActiveIndex((value) => Math.max(0, value - 1))}>Previous</button>
          <button className="quiet-button" type="button" disabled={activeIndex === screenshots.length - 1} onClick={() => setActiveIndex((value) => Math.min(screenshots.length - 1, value + 1))}>Next</button>
        </div>}
      </dialog>
    </div>
  );
}
