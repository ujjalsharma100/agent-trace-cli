import React, { useState } from 'react';
import {
  buildPieSegmentsFromLegendItems,
  legendItemLabel,
  pieSlicePath,
} from '../utils/attributionStats';

export default function ModelAttributionChart({
  legendItems,
  className = '',
  pinnedKey = null,
  showLegend = true,
  emptyMessage = 'No attribution data',
}) {
  const [hover, setHover] = useState(null);
  const segments = buildPieSegmentsFromLegendItems(legendItems);

  if (segments.length === 0) {
    return <div className="attr-pie-empty">{emptyMessage}</div>;
  }

  return (
    <div
      className={`attr-pie-wrap attr-model-pie-wrap ${className}`.trim()}
      onMouseLeave={() => setHover(null)}
    >
      <svg className="attr-pie attr-model-pie" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        {segments.map((seg) => {
          const isPinnedSlice = pinnedKey === seg.key;
          return (
            <path
              key={seg.key}
              d={pieSlicePath(50, 50, 45, seg.startAngle, seg.endAngle)}
              fill={seg.color}
              stroke={isPinnedSlice ? 'rgba(0,0,0,0.4)' : 'none'}
              strokeWidth={isPinnedSlice ? 2 : 0}
              strokeLinejoin="round"
              className={`attr-pie-slice ${hover?.key === seg.key ? 'hover' : ''} ${isPinnedSlice ? 'pinned' : ''}`}
              onMouseEnter={() => setHover(seg)}
            />
          );
        })}
      </svg>
      {hover && (
        <div className="attr-pie-tooltip attr-model-pie-tooltip">
          <span className="attr-model-pie-tooltip-label">
            {legendItemLabel(hover)}
          </span>
          <span className="attr-pie-tooltip-pct">{hover.pct.toFixed(1)}%</span>
        </div>
      )}
      {showLegend && (
        <div className="attr-pie-legend attr-model-pie-legend">
          {segments.map((seg) => (
            <div key={seg.key} className={`attr-pie-legend-item ${pinnedKey === seg.key ? 'pinned' : ''}`}>
              <div className="attr-pie-legend-swatch" style={{ background: seg.color }} />
              <span className="attr-pie-legend-label">{legendItemLabel(seg)}</span>
              <span className="attr-pie-legend-pct">({seg.pct.toFixed(1)}%)</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
