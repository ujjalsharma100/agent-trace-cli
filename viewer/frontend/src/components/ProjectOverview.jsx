import React, { useMemo } from 'react';
import ModelAttributionChart from './ModelAttributionChart';
import { buildLegendItemsFromBreakdown } from '../utils/attributionStats';

export default function ProjectOverview({ project, attribution, loading, error }) {
  const legendItems = useMemo(() => {
    if (!attribution?.breakdown?.length) return [];
    return buildLegendItemsFromBreakdown(attribution.breakdown, attribution.total_lines ?? 0);
  }, [attribution]);

  if (loading) {
    return (
      <div className="project-overview">
        <div className="empty-state">
          <div style={{ color: '#6b7280', fontSize: 13 }}>Computing project attribution…</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="project-overview">
        <div className="empty-state">
          <div style={{ color: '#ef4444', fontSize: 13 }}>{error}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="project-overview">
      <div className="project-overview-header">
        <h1>Project overview</h1>
        <p className="project-overview-subtitle">
          Attribution across all tracked files in this repository.
        </p>
      </div>

      <div className="project-overview-stats">
        <div className="project-stat-card">
          <span className="project-stat-value">{attribution?.total_lines?.toLocaleString() ?? '—'}</span>
          <span className="project-stat-label">Total lines</span>
        </div>
        <div className="project-stat-card">
          <span className="project-stat-value">{attribution?.file_count?.toLocaleString() ?? '—'}</span>
          <span className="project-stat-label">Tracked files</span>
        </div>
        <div className="project-stat-card">
          <span className="project-stat-value">{attribution?.files_scanned?.toLocaleString() ?? '—'}</span>
          <span className="project-stat-label">Files scanned</span>
        </div>
        <div className="project-stat-card">
          <span className="project-stat-value">{project?.has_agent_trace ? 'Yes' : 'No'}</span>
          <span className="project-stat-label">Agent traces</span>
        </div>
      </div>

      <div className="project-overview-chart-card">
        <h2>Attribution by model &amp; tool</h2>
        <p className="project-overview-chart-note">
          Distribution of all lines in the project, grouped by AI model and the tool that produced each trace.
        </p>
        <ModelAttributionChart legendItems={legendItems} />
      </div>

      {legendItems.length > 0 && (
        <div className="project-overview-table-card">
          <h2>Breakdown</h2>
          <table className="project-attribution-table">
            <thead>
              <tr>
                <th>Attribution</th>
                <th>Model</th>
                <th>Tool</th>
                <th>Lines</th>
                <th>Share</th>
              </tr>
            </thead>
            <tbody>
              {legendItems.map((item) => {
                const parts = item.sublabel?.split(' · ') ?? [];
                const model = item.key === 'No attribution' ? '—' : (parts[0] ?? '—');
                const tool = item.key === 'No attribution' ? '—' : (parts[1] ?? '—');
                return (
                  <tr key={item.key}>
                    <td>
                      <span className="project-table-swatch" style={{ background: item.strip }} />
                      {item.label}
                    </td>
                    <td className="mono">{model}</td>
                    <td>{tool}</td>
                    <td className="mono">{item.lines?.toLocaleString() ?? '—'}</td>
                    <td className="mono">{item.pct.toFixed(1)}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
