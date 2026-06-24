/* Shared attribution stats helpers for file and project views. */

export const AI_PALETTES = [
  { bg: 'hsl(142, 52%, 91%)', strip: 'hsl(142, 62%, 32%)' },
  { bg: 'hsl(162, 58%, 83%)', strip: 'hsl(162, 62%, 26%)' },
  { bg: 'hsl(125, 42%, 88%)', strip: 'hsl(125, 52%, 30%)' },
  { bg: 'hsl(150, 65%, 80%)', strip: 'hsl(150, 70%, 24%)' },
  { bg: 'hsl(172, 48%, 86%)', strip: 'hsl(172, 55%, 28%)' },
  { bg: 'hsl(135, 60%, 84%)', strip: 'hsl(135, 65%, 27%)' },
  { bg: 'hsl(155, 42%, 93%)', strip: 'hsl(155, 52%, 36%)' },
  { bg: 'hsl(145, 62%, 81%)', strip: 'hsl(145, 68%, 22%)' },
];

export const NO_ATTRIBUTION_COLORS = { bg: 'hsl(0, 0%, 94%)', strip: 'hsl(0, 0%, 65%)' };
export const AI_DEFAULT = AI_PALETTES[0];

export function isNoAttribution(attr) {
  if (!attr) return true;
  if (attr.kind === 'NO_ATTRIBUTION') return true;
  if (attr.kind === 'AI') return false;
  const hasTrace = attr.trace_id != null && attr.trace_id !== '';
  if (hasTrace && attr.attribution_label === 'AI') return false;
  return true;
}

export function countDistinctLinesCovered(attributions) {
  const lineSet = new Set();
  for (const a of attributions) {
    const start = a.start_line ?? a.startLine;
    const end = a.end_line ?? a.endLine;
    for (let L = start; L <= end; L++) lineSet.add(L);
  }
  return lineSet.size;
}

export function getToolKey(tool) {
  if (!tool) return '';
  if (typeof tool === 'object') {
    const name = tool.name || '';
    const version = tool.version || '';
    return version ? `${name}@${version}` : name;
  }
  return String(tool);
}

export function formatToolLabel(tool) {
  if (!tool) return null;
  if (typeof tool === 'object') {
    const name = tool.name || '—';
    return tool.version ? `${name} v${tool.version}` : name;
  }
  return String(tool);
}

export function getLegendKey(attr) {
  if (isNoAttribution(attr)) return 'No attribution';
  const model = attr.model_id || '(unknown model)';
  const toolKey = getToolKey(attr.tool);
  return toolKey ? `AI:${model}:${toolKey}` : `AI:${model}`;
}

export function getTraceKey(attr) {
  if (isNoAttribution(attr)) return '__no_attribution__';
  return attr.trace_id ? `${attr.trace_id}:AI` : `__no_trace__:AI`;
}

export function getDisplayLabel(attr) {
  if (isNoAttribution(attr)) return 'No attribution';
  return 'AI';
}

export function formatLegendSublabel(attr) {
  if (isNoAttribution(attr)) return null;
  const model = attr.model_id || '(unknown model)';
  const tool = formatToolLabel(attr.tool);
  return tool ? `${model} · ${tool}` : model;
}

function getNoAttributionPct(totalLines, attributions) {
  if (!totalLines) return 0;
  const attributedOnly = attributions.filter((a) => !isNoAttribution(a));
  const attributed = countDistinctLinesCovered(attributedOnly);
  return ((totalLines - attributed) / totalLines) * 100;
}

function paletteForKey(key, keyIndex) {
  if (key === 'No attribution') return NO_ATTRIBUTION_COLORS;
  return AI_PALETTES[keyIndex % AI_PALETTES.length];
}

export function buildLegendItemsFromAttributions(attributions, totalLines) {
  const keyToAttrs = new Map();
  for (const a of attributions) {
    const key = getLegendKey(a);
    if (!keyToAttrs.has(key)) keyToAttrs.set(key, []);
    keyToAttrs.get(key).push(a);
  }

  const noAttributionPct = getNoAttributionPct(totalLines, attributions);
  const items = [];
  const seenKeys = new Set();
  let aiIndex = 0;

  for (const a of attributions) {
    const key = getLegendKey(a);
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    const attrs = keyToAttrs.get(key) ?? [];
    const colors = key === 'No attribution'
      ? NO_ATTRIBUTION_COLORS
      : paletteForKey(key, aiIndex++);

    const pct = key === 'No attribution'
      ? noAttributionPct
      : (totalLines ? (countDistinctLinesCovered(attrs) / totalLines) * 100 : 0);

    if (key === 'No attribution') {
      items.push({
        key,
        label: 'No attribution',
        sublabel: null,
        bg: colors.bg,
        strip: colors.strip,
        pct,
        lines: totalLines ? Math.round((pct / 100) * totalLines) : 0,
      });
    } else {
      items.push({
        key,
        label: 'AI',
        sublabel: formatLegendSublabel(a),
        bg: colors.bg,
        strip: colors.strip,
        pct,
        lines: countDistinctLinesCovered(attrs),
      });
    }
  }

  if (noAttributionPct > 0 && !seenKeys.has('No attribution')) {
    items.push({
      key: 'No attribution',
      label: 'No attribution',
      sublabel: null,
      bg: NO_ATTRIBUTION_COLORS.bg,
      strip: NO_ATTRIBUTION_COLORS.strip,
      pct: noAttributionPct,
      lines: totalLines ? Math.round((noAttributionPct / 100) * totalLines) : 0,
    });
  }

  return items;
}

export function buildLegendItemsFromBreakdown(breakdown, totalLines) {
  let aiIndex = 0;
  return breakdown.map((entry) => {
    const key = entry.key;
    const colors = key === 'No attribution'
      ? NO_ATTRIBUTION_COLORS
      : paletteForKey(key, aiIndex++);
    const lines = entry.lines ?? 0;
    const pct = totalLines ? (lines / totalLines) * 100 : 0;
    const sublabel = entry.sublabel
      ?? (entry.model_id
        ? (entry.tool ? `${entry.model_id} · ${formatToolLabel(entry.tool)}` : entry.model_id)
        : null);

    return {
      key,
      label: entry.label ?? (key === 'No attribution' ? 'No attribution' : 'AI'),
      sublabel: key === 'No attribution' ? null : sublabel,
      bg: colors.bg,
      strip: colors.strip,
      pct,
      lines,
    };
  });
}

export function buildPieSegmentsFromLegendItems(legendItems) {
  const withPct = legendItems.filter((item) => item.pct > 0);
  let cum = 0;
  return withPct.map((item) => {
    const startAngle = cum;
    cum += item.pct;
    const endAngle = cum;
    let startDeg = (startAngle / 100) * 360;
    let endDeg = (endAngle / 100) * 360;
    if (endDeg >= 360) endDeg = 359.99;
    return {
      key: item.key,
      label: item.label,
      sublabel: item.sublabel,
      pct: item.pct,
      color: item.strip,
      startAngle: startDeg,
      endAngle: endDeg,
    };
  });
}

export function pieSlicePath(cx, cy, r, startDeg, endDeg) {
  const toRad = (d) => (d - 90) * (Math.PI / 180);
  const x = (deg) => cx + r * Math.cos(toRad(deg));
  const y = (deg) => cy + r * Math.sin(toRad(deg));
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x(startDeg)} ${y(startDeg)} A ${r} ${r} 0 ${large} 1 ${x(endDeg)} ${y(endDeg)} Z`;
}

export function legendItemLabel(item) {
  return `${item.label}${item.sublabel ? ` · ${item.sublabel}` : ''}`;
}
