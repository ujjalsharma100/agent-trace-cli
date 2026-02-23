import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react';
import ConversationModal, { ConversationPanel } from './ConversationModal';

const API = '';

/* ─── Helpers ───────────────────────────────────────────── */

function findSegmentForLine(segments, lineNum) {
  if (!Array.isArray(segments)) return null;
  for (const seg of segments) {
    const start = seg.start_line ?? seg.startLine;
    const end = seg.end_line ?? seg.endLine;
    if (lineNum >= start && lineNum <= end) return seg;
  }
  return null;
}

function findAttributionForLine(attributions, lineNum) {
  if (!Array.isArray(attributions)) return null;
  for (const a of attributions) {
    const start = a.start_line ?? a.startLine;
    const end = a.end_line ?? a.endLine;
    if (lineNum >= start && lineNum <= end) return a;
  }
  return null;
}

function formatAuthorTime(ts) {
  if (ts == null) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function countDistinctLinesCovered(attributions) {
  const lineSet = new Set();
  for (const a of attributions) {
    const start = a.start_line ?? a.startLine;
    const end = a.end_line ?? a.endLine;
    for (let L = start; L <= end; L++) lineSet.add(L);
  }
  return lineSet.size;
}

/** Line ranges (start, end) that have no attribution */
function getUncoveredLineRanges(totalLines, attributions) {
  const covered = new Set();
  for (const a of attributions) {
    const start = a.start_line ?? a.startLine;
    const end = a.end_line ?? a.endLine;
    for (let L = start; L <= end; L++) covered.add(L);
  }
  const ranges = [];
  let start = null;
  for (let L = 1; L <= totalLines; L++) {
    if (!covered.has(L)) {
      if (start === null) start = L;
    } else {
      if (start !== null) {
        ranges.push({ start, end: L - 1 });
        start = null;
      }
    }
  }
  if (start !== null) ranges.push({ start, end: totalLines });
  return ranges;
}

/** SVG pie slice path: angles in degrees, 0 = top (12 o'clock), clockwise */
function pieSlicePath(cx, cy, r, startDeg, endDeg) {
  const toRad = (d) => (d - 90) * (Math.PI / 180);
  const x = (deg) => cx + r * Math.cos(toRad(deg));
  const y = (deg) => cy + r * Math.sin(toRad(deg));
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${cx} ${cy} L ${x(startDeg)} ${y(startDeg)} A ${r} ${r} 0 ${large} 1 ${x(endDeg)} ${y(endDeg)} Z`;
}

/* ─── Color system ──────────────────────────────────────── */

// 8 hand-tuned green-family palettes with wide lightness & saturation spread
// Lightness ranges from 80% to 93%, saturation from 40% to 65%
// All hues stay in 120-175 range (clearly green, no blue/yellow confusion)
const AI_PALETTES = [
  { bg: 'hsl(142, 52%, 91%)', strip: 'hsl(142, 62%, 32%)' },   // light spring green
  { bg: 'hsl(162, 58%, 83%)', strip: 'hsl(162, 62%, 26%)' },   // deep teal-green
  { bg: 'hsl(125, 42%, 88%)', strip: 'hsl(125, 52%, 30%)' },   // grass green
  { bg: 'hsl(150, 65%, 80%)', strip: 'hsl(150, 70%, 24%)' },   // saturated emerald (darkest)
  { bg: 'hsl(172, 48%, 86%)', strip: 'hsl(172, 55%, 28%)' },   // cyan-green
  { bg: 'hsl(135, 60%, 84%)', strip: 'hsl(135, 65%, 27%)' },   // forest green
  { bg: 'hsl(155, 42%, 93%)', strip: 'hsl(155, 52%, 36%)' },   // pale jade (lightest)
  { bg: 'hsl(145, 62%, 81%)', strip: 'hsl(145, 68%, 22%)' },   // deep jade
];

function buildTraceColorMap(attributions) {
  const map = {};
  if (!Array.isArray(attributions)) return map;
  const traceIds = [...new Set(
    attributions
      .filter(a => a.trace_id && (a.attribution_label === 'AI' || (!a.attribution_label && a.model_id)))
      .map(a => a.trace_id)
  )];
  traceIds.forEach((tid, idx) => {
    map[tid] = { ...AI_PALETTES[idx % AI_PALETTES.length] };
  });
  return map;
}

const HUMAN_COLORS = { bg: 'hsl(220, 58%, 94%)', strip: 'hsl(220, 65%, 48%)' };
const MIXED_COLORS = { bg: 'hsl(28, 65%, 92%)', strip: 'hsl(24, 75%, 45%)' };
const NO_ATTRIBUTION_COLORS = { bg: 'hsl(0, 0%, 94%)', strip: 'hsl(0, 0%, 65%)' };
const AI_DEFAULT   = AI_PALETTES[0];

/** Darken an HSL strip color for use as pinned-line border (same hue, deeper). */
function deeperBorderColor(stripColor) {
  if (!stripColor || stripColor === 'transparent') return 'hsl(0, 0%, 55%)';
  const m = stripColor.match(/hsl\((\d+),\s*([\d.]+)%,\s*([\d.]+)%\)/);
  if (!m) return stripColor;
  const [, h, s, l] = m;
  const darkerL = Math.max(12, Math.min(50, Number(l) * 0.65));
  return `hsl(${h}, ${s}%, ${darkerL}%)`;
}

function getLineColors(attr, traceColorMap) {
  if (!attr) return { bg: 'transparent', strip: 'transparent', label: null };
  const label = attr.attribution_label;
  if (label === 'Human') return { ...HUMAN_COLORS, label: 'Human' };
  if (label === 'Mixed') return { ...MIXED_COLORS, label: 'Mixed' };
  if (attr.trace_id && traceColorMap[attr.trace_id]) {
    return { ...traceColorMap[attr.trace_id], label: label || 'AI' };
  }
  return { ...AI_DEFAULT, label: label || 'AI' };
}

/** Trace key for grouping (must match attributionsByTraceId). */
function getTraceKey(attr) {
  if (!attr) return '__no_attribution__';
  const label = attr.attribution_label ?? 'AI';
  return attr.trace_id ? `${attr.trace_id}:${label}` : `__no_trace__:${label}`;
}

/** Legend key for toolbar / model pie (must match legendItems). */
function getLegendKey(attr) {
  if (!attr) return 'No attribution';
  const label = attr.attribution_label ?? 'AI';
  if (label === 'AI') return `AI:${attr.model_id || '(unknown model)'}`;
  return label;
}

const SIDE_PANE_MIN = 320;
const SIDE_PANE_MAX = 600;

/* ─── Collapsible Section ───────────────────────────────── */

function CollapsibleSection({ title, headerClass, defaultOpen, children }) {
  const [open, setOpen] = useState(defaultOpen !== false);
  return (
    <div className="detail-card">
      <button
        type="button"
        className={`detail-card-header collapsible ${headerClass || ''}`}
        onClick={() => setOpen(!open)}
      >
        <span className="collapse-chevron">{open ? '▾' : '▸'}</span>
        {title}
      </button>
      {open && <div className="detail-card-body">{children}</div>}
    </div>
  );
}

/* ─── Main Component ────────────────────────────────────── */

export default function FileViewer({ path, content, gitBlameSegments, agentTraceBlame }) {
  const [hoverLine, setHoverLine] = useState(null);
  const [pinnedLine, setPinnedLine] = useState(null);
  const [showGitBlame, setShowGitBlame] = useState(false);
  const [showTraceBlame, setShowTraceBlame] = useState(true);
  const [sidePaneWidth, setSidePaneWidth] = useState(380);
  const [resizingSide, setResizingSide] = useState(false);
  const [blamePaneWidth] = useState(200);
  const [tracePaneWidth] = useState(160);
  const [conversationUrl, setConversationUrl] = useState(null);
  const [conversationContent, setConversationContent] = useState(null);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState(null);
  const [convMaximized, setConvMaximized] = useState(false);
  const [popover, setPopover] = useState(null);
  const [attrMapView, setAttrMapView] = useState('list');
  const [chartHover, setChartHover] = useState(null);
  const [modelChartHover, setModelChartHover] = useState(null);
  const dragStart = useRef({ x: 0, width: 0 });
  const popoverTimer = useRef(null);

  const segments = Array.isArray(gitBlameSegments) ? gitBlameSegments : [];
  const attributions = Array.isArray(agentTraceBlame?.attributions) ? agentTraceBlame.attributions : [];
  const traceColorMap = useMemo(() => buildTraceColorMap(attributions), [attributions]);

  const hasBlameOrTrace = showGitBlame || showTraceBlame;

  /* When both git and trace blame are off, close side pane and clear pinned line */
  useEffect(() => {
    if (!hasBlameOrTrace) {
      setPinnedLine(null);
      setConversationUrl(null);
      setConversationContent(null);
      clearTimeout(popoverTimer.current);
      setPopover(null);
    }
  }, [hasBlameOrTrace]);

  const lines = useMemo(() => {
    if (content == null || content === '') return [];
    return content.split('\n');
  }, [content]);

  /* ─── Conversation fetching ─────────────────────────── */
  const fetchConversation = useCallback((url) => {
    if (!url) return;
    setConversationLoading(true);
    setConversationError(null);
    setConversationContent(null);
    fetch(`${API}/api/conversation?url=${encodeURIComponent(url)}`)
      .then((r) => {
        if (!r.ok) return r.json().then((j) => Promise.reject(new Error(j.error || r.statusText)));
        return r.json();
      })
      .then((data) => {
        if (data.open_external && data.url) {
          window.open(data.url, '_blank', 'noopener,noreferrer');
          setConversationUrl(null);
          setConversationLoading(false);
          return;
        }
        setConversationContent(data.content ?? '');
        setConversationLoading(false);
      })
      .catch((e) => {
        setConversationError(e.message || 'Failed to load conversation');
        setConversationLoading(false);
      });
  }, []);

  useEffect(() => {
    if (conversationUrl) fetchConversation(conversationUrl);
  }, [conversationUrl, fetchConversation]);

  // Auto-switch conversation when pinned line changes (only when Trace Attribution is on)
  useEffect(() => {
    if (pinnedLine != null && showTraceBlame) {
      const attr = findAttributionForLine(attributions, pinnedLine);
      if (attr?.conversation_url) {
        setConversationUrl(attr.conversation_url);
      } else {
        setConversationUrl(null);
        setConversationContent(null);
        setConversationError(null);
      }
    } else if (!showTraceBlame) {
      setConversationUrl(null);
      setConversationContent(null);
      setConversationError(null);
    }
  }, [pinnedLine, attributions, showTraceBlame]);

  /* ─── Resize handling ───────────────────────────────── */
  useEffect(() => {
    if (!resizingSide) return;
    const onMove = (e) => {
      const dx = e.clientX - dragStart.current.x;
      setSidePaneWidth(Math.max(SIDE_PANE_MIN, Math.min(SIDE_PANE_MAX, dragStart.current.width - dx)));
    };
    const onUp = () => setResizingSide(false);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [resizingSide]);

  /* ─── Popover (only shows content for the enabled mode: trace and/or git) ── */
  const showPopoverForLine = useCallback((e, lineNum) => {
    clearTimeout(popoverTimer.current);
    const attr = findAttributionForLine(attributions, lineNum);
    const seg = findSegmentForLine(segments, lineNum);
    const hasTraceContent = showTraceBlame && attr;
    const hasGitContent = showGitBlame && seg;
    if (!hasTraceContent && !hasGitContent) { setPopover(null); return; }
    const rect = e.currentTarget.getBoundingClientRect();
    popoverTimer.current = setTimeout(() => {
      setPopover({
        x: Math.min(rect.left + 60, window.innerWidth - 370),
        y: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 240)),
        attr: showTraceBlame ? attr : null,
        seg: showGitBlame ? seg : null,
        lineNum,
      });
    }, 250);
  }, [attributions, segments, showTraceBlame, showGitBlame]);

  const hidePopover = useCallback(() => {
    clearTimeout(popoverTimer.current);
    setPopover(null);
  }, []);

  /* ─── Line interaction ──────────────────────────────── */
  const handleLineClick = useCallback((lineNum) => {
    if (!hasBlameOrTrace) return;
    setPinnedLine((prev) => (prev === lineNum ? null : lineNum));
    hidePopover();
  }, [hidePopover, hasBlameOrTrace]);

  const gitSegment = pinnedLine != null ? findSegmentForLine(segments, pinnedLine) : null;
  const detailAttr = pinnedLine != null ? findAttributionForLine(attributions, pinnedLine) : null;
  const showSidePane = pinnedLine != null && hasBlameOrTrace;

  /* Pinned line's trace/key for cross-highlighting (code, list, pie, legend) */
  const pinnedTraceKey = pinnedLine != null ? getTraceKey(detailAttr) : null;
  const pinnedLegendKey = pinnedLine != null ? getLegendKey(detailAttr) : null;

  /* Contiguous line ranges that share the pinned trace (for drawing a box around each range) */
  const pinnedSameTraceRanges = useMemo(() => {
    if (pinnedTraceKey == null || !lines.length) return [];
    const lineNums = [];
    for (let n = 1; n <= lines.length; n++) {
      const attr = findAttributionForLine(attributions, n);
      if (getTraceKey(attr) === pinnedTraceKey) lineNums.push(n);
    }
    if (lineNums.length === 0) return [];
    const ranges = [];
    let start = lineNums[0];
    let end = lineNums[0];
    for (let i = 1; i < lineNums.length; i++) {
      if (lineNums[i] === end + 1) {
        end = lineNums[i];
      } else {
        ranges.push({ start, end });
        start = lineNums[i];
        end = lineNums[i];
      }
    }
    ranges.push({ start, end });
    return ranges;
  }, [pinnedTraceKey, lines.length, attributions]);

  /* ─── Attributions grouped by trace ID and label (for side pane map) ── */
  const attributionsByTraceId = useMemo(() => {
    const groups = new Map();
    for (const a of attributions) {
      const label = a.attribution_label ?? 'AI';
      const key = a.trace_id ? `${a.trace_id}:${label}` : `__no_trace__:${label}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(a);
    }
    return groups;
  }, [attributions]);

  /* ─── No-attribution stats (for list, pie, legend) ── */
  const noAttributionStats = useMemo(() => {
    const totalLines = lines.length;
    if (!totalLines) return { pct: 0, ranges: [] };
    const attributed = countDistinctLinesCovered(attributions);
    const pct = ((totalLines - attributed) / totalLines) * 100;
    const ranges = getUncoveredLineRanges(totalLines, attributions);
    return { pct, ranges };
  }, [attributions, lines.length]);

  /* ─── Pie chart segments (for File Attribution chart view) ── */
  const pieSegments = useMemo(() => {
    const totalLines = lines.length;
    if (!totalLines) return [];
    const entries = [...attributionsByTraceId.entries()];
    const withPct = entries.map(([traceKey, attrs]) => {
      const firstAttr = attrs[0];
      const label = firstAttr.attribution_label || 'AI';
      const pct = countDistinctLinesCovered(attrs) / totalLines * 100;
      const c = getLineColors(firstAttr, traceColorMap);
      return { traceKey, attrs, label, pct, color: c.strip, firstAttr, gapRanges: null };
    }).filter((s) => s.pct > 0);
    const noAttrPct = noAttributionStats.pct;
    if (noAttrPct > 0) {
      withPct.push({
        traceKey: '__no_attribution__',
        attrs: [],
        label: 'No attribution',
        pct: noAttrPct,
        color: NO_ATTRIBUTION_COLORS.strip,
        firstAttr: null,
        gapRanges: noAttributionStats.ranges,
      });
    }
    let cum = 0;
    return withPct.map((s) => {
      const startAngle = cum;
      cum += s.pct;
      const endAngle = cum;
      return { ...s, startAngle: (startAngle / 100) * 360, endAngle: (endAngle / 100) * 360 };
    });
  }, [attributionsByTraceId, lines.length, traceColorMap, noAttributionStats]);

  /* ─── Toolbar legend: grouped by model (AI · model, Human, Mixed, No attribution) ── */
  const legendItems = useMemo(() => {
    const totalLines = lines.length;
    const keyToAttrs = new Map();
    for (const a of attributions) {
      const label = a.attribution_label || 'AI';
      const key = label === 'AI' ? `AI:${a.model_id || '(unknown model)'}` : label;
      if (!keyToAttrs.has(key)) keyToAttrs.set(key, []);
      keyToAttrs.get(key).push(a);
    }
    const items = [];
    const seenKeys = new Set();
    for (const a of attributions) {
      const label = a.attribution_label || 'AI';
      if (label === 'AI') {
        const modelKey = a.model_id || '(unknown model)';
        if (!seenKeys.has(`AI:${modelKey}`)) {
          seenKeys.add(`AI:${modelKey}`);
          const colors = getLineColors(a, traceColorMap);
          const attrs = keyToAttrs.get(`AI:${modelKey}`) ?? [];
          const pct = totalLines ? (countDistinctLinesCovered(attrs) / totalLines * 100) : 0;
          items.push({ key: `AI:${modelKey}`, label: 'AI', sublabel: modelKey, bg: colors.bg, strip: colors.strip, pct });
        }
      } else if (!seenKeys.has(label)) {
        seenKeys.add(label);
        const colors = getLineColors(a, traceColorMap);
        const attrs = keyToAttrs.get(label) ?? [];
        const pct = totalLines ? (countDistinctLinesCovered(attrs) / totalLines * 100) : 0;
        items.push({ key: label, label, sublabel: null, bg: colors.bg, strip: colors.strip, pct });
      }
    }
    const attributedLines = totalLines ? countDistinctLinesCovered(attributions) : 0;
    const noAttrPct = totalLines ? ((totalLines - attributedLines) / totalLines * 100) : 0;
    if (noAttrPct > 0) {
      items.push({
        key: 'No attribution',
        label: 'No attribution',
        sublabel: null,
        bg: NO_ATTRIBUTION_COLORS.bg,
        strip: NO_ATTRIBUTION_COLORS.strip,
        pct: noAttrPct,
      });
    }
    return items;
  }, [attributions, traceColorMap, lines.length]);

  /* ─── Pie chart legend: one row per label (AI as a whole, Human, Mixed, No attribution) ── */
  const pieLegendItems = useMemo(() => {
    const byLabel = new Map();
    for (const seg of pieSegments) {
      const label = seg.label;
      const cur = byLabel.get(label) ?? 0;
      byLabel.set(label, cur + seg.pct);
    }
    const order = ['AI', 'Human', 'Mixed', 'No attribution'];
    const colors = {
      AI: AI_DEFAULT.strip,
      Human: HUMAN_COLORS.strip,
      Mixed: MIXED_COLORS.strip,
      'No attribution': NO_ATTRIBUTION_COLORS.strip,
    };
    return order.filter((l) => byLabel.has(l)).map((label) => ({
      key: label,
      label,
      pct: byLabel.get(label),
      color: colors[label],
    }));
  }, [pieSegments]);

  /* ─── By-model pie (toolbar distribution as pie, for side pane) ── */
  const modelPieSegments = useMemo(() => {
    const withPct = legendItems.filter((item) => item.pct > 0);
    let cum = 0;
    return withPct.map((item) => {
      const startAngle = cum;
      cum += item.pct;
      const endAngle = cum;
      return {
        key: item.key,
        label: item.label,
        sublabel: item.sublabel,
        pct: item.pct,
        color: item.strip,
        startAngle: (startAngle / 100) * 360,
        endAngle: (endAngle / 100) * 360,
      };
    });
  }, [legendItems]);

  return (
    <div className="fv-container">
      {/* ─── Toolbar ────────────────────────────────────── */}
      <div className="fv-toolbar">
        <button
          type="button"
          className={`fv-toggle ${showGitBlame ? 'active' : ''}`}
          onClick={() => setShowGitBlame(!showGitBlame)}
        >
          <span className="dot" />
          Git Blame
        </button>
        <button
          type="button"
          className={`fv-toggle ${showTraceBlame ? 'active' : ''}`}
          onClick={() => setShowTraceBlame(!showTraceBlame)}
        >
          <span className="dot" />
          Trace Attribution
        </button>

        {showTraceBlame && legendItems.length > 0 && (
          <div className="fv-legend">
            {legendItems.map((item) => (
              <div key={item.key} className={`fv-legend-item ${pinnedLegendKey === item.key ? 'pinned-model' : ''}`}>
                <div
                  className="fv-legend-swatch"
                  style={{ background: item.bg, borderLeft: `3px solid ${item.strip}` }}
                />
                <span>{item.label}{item.sublabel ? ` · ${item.sublabel}` : ''}</span>
                <span className="fv-legend-pct">({item.pct.toFixed(1)}%)</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ─── Body: code + detail panel ──────────────────── */}
      <div className="fv-body">
        {/* Code pane */}
        <div className="fv-code-pane">
          <div className="fv-code-lines">
          {lines.map((line, i) => {
            const lineNum = i + 1;
            const seg = showGitBlame ? findSegmentForLine(segments, lineNum) : null;
            const attr = findAttributionForLine(attributions, lineNum);
            const colors = showTraceBlame ? getLineColors(attr, traceColorMap) : { bg: 'transparent', strip: 'transparent' };
            const isPinned = pinnedLine === lineNum;
            const sameTraceAsPinned = showTraceBlame && pinnedTraceKey != null && getTraceKey(attr) === pinnedTraceKey;

            let isRangeStart = false;
            let isRangeEnd = false;
            let inSameTraceRange = false;
            if (showTraceBlame) {
              for (const r of pinnedSameTraceRanges) {
                if (lineNum >= r.start && lineNum <= r.end) {
                  inSameTraceRange = true;
                  isRangeStart = lineNum === r.start;
                  isRangeEnd = lineNum === r.end;
                  break;
                }
              }
            }

            const pinnedBorder = showTraceBlame && isPinned ? deeperBorderColor(colors.strip) : null;
            const pinnedBorderGitOnly = !showTraceBlame && isPinned;
            const boxShadows = [];
            if (pinnedBorder) boxShadows.push(`inset 0 0 0 1px ${pinnedBorder}`);
            if (pinnedBorderGitOnly) boxShadows.push('inset 0 0 0 1px rgba(0,0,0,0.22)');
            const SAME_TRACE_RANGE_BORDER = '1px solid rgba(0,0,0,0.14)';
            const rangeBoxStyle = showTraceBlame && inSameTraceRange ? {
              borderLeft: SAME_TRACE_RANGE_BORDER,
              borderRight: SAME_TRACE_RANGE_BORDER,
              ...(isRangeStart && { borderTop: SAME_TRACE_RANGE_BORDER }),
              ...(isRangeEnd && { borderBottom: SAME_TRACE_RANGE_BORDER }),
            } : {};
            return (
              <div
                key={i}
                className={`fv-line ${isPinned ? 'pinned' : ''} ${sameTraceAsPinned ? 'same-trace' : ''} ${!hasBlameOrTrace ? 'fv-line-no-interact' : ''}`}
                style={{
                  backgroundColor: colors.bg,
                  ...(boxShadows.length > 0 && { boxShadow: boxShadows.join(', ') }),
                  ...rangeBoxStyle,
                }}
                {...(hasBlameOrTrace && {
                  onMouseEnter: (e) => {
                    setHoverLine(lineNum);
                    showPopoverForLine(e, lineNum);
                  },
                  onMouseLeave: () => {
                    setHoverLine(null);
                    hidePopover();
                  },
                  onClick: () => handleLineClick(lineNum),
                })}
              >
                <div
                  className="fv-attr-strip"
                  style={{ backgroundColor: showTraceBlame ? colors.strip : 'transparent' }}
                />
                <div className="fv-linenum">{lineNum}</div>

                {showGitBlame && (
                  <div className="fv-blame-col" style={{ width: blamePaneWidth, flexBasis: blamePaneWidth }}>
                    {seg ? (
                      <>
                        <span className="sha">{seg.commit_sha?.slice(0, 7) ?? '—'}</span>
                        <span className="author">{seg.author ?? '—'}</span>
                      </>
                    ) : (
                      <span style={{ color: '#d1d5db' }}>—</span>
                    )}
                  </div>
                )}

                {showTraceBlame && (
                  <div className="fv-trace-col" style={{ width: tracePaneWidth, flexBasis: tracePaneWidth }}>
                    {attr ? (
                      <>
                        <span className={`attr-badge ${(attr.attribution_label || 'ai').toLowerCase()}`}>
                          {attr.attribution_label || 'AI'}
                        </span>
                        {attr.model_id && <span className="model-name">{attr.model_id}</span>}
                      </>
                    ) : (
                      <span style={{ color: '#d1d5db', fontSize: 10 }}>—</span>
                    )}
                  </div>
                )}

                <div className="fv-code">{line || '\u00A0'}</div>
              </div>
            );
          })}
          </div>
        </div>

        {/* Resize handle */}
        {showSidePane && (
          <div
            className={`resize-handle ${resizingSide ? 'active' : ''}`}
            onMouseDown={(e) => {
              e.preventDefault();
              dragStart.current = { x: e.clientX, width: sidePaneWidth };
              setResizingSide(true);
            }}
          />
        )}

        {/* ─── Detail panel ───────────────────────────── */}
        {showSidePane && (
          <div className="fv-detail-panel" style={{ width: sidePaneWidth, flexBasis: sidePaneWidth }}>
            <div className="fv-detail-header">
              <div className="line-label">
                Line <span>{pinnedLine}</span>
              </div>
              <button type="button" className="unpin-btn" onClick={() => { setPinnedLine(null); setConversationUrl(null); setConversationContent(null); }}>
                Unpin &times;
              </button>
            </div>

            <div className="fv-detail-body">
              {showGitBlame && (
                <CollapsibleSection title="Git Blame" headerClass="git" defaultOpen={true}>
                  {gitSegment ? (
                    <>
                      <div className="detail-row"><span className="dl">Author</span><span className="dv">{gitSegment.author ?? '—'}</span></div>
                      <div className="detail-row"><span className="dl">Date</span><span className="dv">{formatAuthorTime(gitSegment.author_time)}</span></div>
                      <div className="detail-row"><span className="dl">Commit</span><span className="dv mono">{gitSegment.commit_sha ?? '—'}</span></div>
                      {gitSegment.summary && <div className="detail-row"><span className="dl">Message</span><span className="dv">{gitSegment.summary}</span></div>}
                      <div className="detail-row"><span className="dl">Lines</span><span className="dv mono">{gitSegment.start_line}–{gitSegment.end_line}</span></div>
                    </>
                  ) : (
                    <div style={{ color: '#9ca3af', fontSize: 11 }}>No git blame for this line.</div>
                  )}
                </CollapsibleSection>
              )}

              {showTraceBlame && (attributions.length > 0 || lines.length > 0) && (
                <CollapsibleSection title="Attribution by model" headerClass="trace" defaultOpen={false}>
                  <div
                    className="attr-pie-wrap attr-model-pie-wrap"
                    onMouseLeave={() => setModelChartHover(null)}
                  >
                    {modelPieSegments.length > 0 ? (
                      <>
                        <svg className="attr-pie attr-model-pie" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                          {modelPieSegments.map((seg) => {
                            const isPinnedSlice = pinnedLegendKey === seg.key;
                            return (
                              <path
                                key={seg.key}
                                d={pieSlicePath(50, 50, 45, seg.startAngle, seg.endAngle)}
                                fill={seg.color}
                                stroke={isPinnedSlice ? 'rgba(0,0,0,0.4)' : 'none'}
                                strokeWidth={isPinnedSlice ? 2 : 0}
                                strokeLinejoin="round"
                                className={`attr-pie-slice ${modelChartHover?.key === seg.key ? 'hover' : ''} ${isPinnedSlice ? 'pinned' : ''}`}
                                onMouseEnter={() => setModelChartHover(seg)}
                              />
                            );
                          })}
                        </svg>
                        {modelChartHover && (
                          <div className="attr-pie-tooltip attr-model-pie-tooltip">
                            <span className="attr-model-pie-tooltip-label">
                              {modelChartHover.label}{modelChartHover.sublabel ? ` · ${modelChartHover.sublabel}` : ''}
                            </span>
                            <span className="attr-pie-tooltip-pct">{modelChartHover.pct.toFixed(1)}%</span>
                          </div>
                        )}
                        <div className="attr-pie-legend attr-model-pie-legend">
                          {modelPieSegments.map((seg) => (
                            <div key={seg.key} className={`attr-pie-legend-item ${pinnedLegendKey === seg.key ? 'pinned' : ''}`}>
                              <div
                                className="attr-pie-legend-swatch"
                                style={{ background: seg.color }}
                              />
                              <span className="attr-pie-legend-label">
                                {seg.label}{seg.sublabel ? ` · ${seg.sublabel}` : ''}
                              </span>
                              <span className="attr-pie-legend-pct">({seg.pct.toFixed(1)}%)</span>
                            </div>
                          ))}
                        </div>
                      </>
                    ) : (
                      <div className="attr-pie-empty">No attribution data</div>
                    )}
                  </div>
                </CollapsibleSection>
              )}

              {showTraceBlame && (
                <CollapsibleSection title="Trace Attribution" headerClass="trace" defaultOpen={true}>
                  {detailAttr ? (
                    <>
                      {detailAttr.model_id && (
                        <div className="detail-row" style={{ marginBottom: 6 }}>
                          <span className="dl">Model</span>
                          <span className="dv mono" style={{ fontWeight: 700, fontSize: 12, color: '#1d4ed8' }}>{detailAttr.model_id}</span>
                        </div>
                      )}
                      {detailAttr.contributor_type && (
                        <div className="detail-row">
                          <span className="dl">Contributor</span>
                          <span className="dv">
                            <span className={`attr-badge ${(detailAttr.attribution_label || detailAttr.contributor_type || 'ai').toLowerCase()}`} style={{ fontSize: 10 }}>
                              {detailAttr.contributor_type}
                            </span>
                          </span>
                        </div>
                      )}
                      {detailAttr.trace_id && <div className="detail-row"><span className="dl">Trace ID</span><span className="dv mono">{detailAttr.trace_id}</span></div>}
                      {detailAttr.timestamp && <div className="detail-row"><span className="dl">Timestamp</span><span className="dv mono">{new Date(detailAttr.timestamp).toLocaleString()}</span></div>}
                      {detailAttr.tool && (
                        <div className="detail-row">
                          <span className="dl">Tool</span>
                          <span className="dv mono">
                            {typeof detailAttr.tool === 'object'
                              ? `${detailAttr.tool.name || '—'}${detailAttr.tool.version ? ` v${detailAttr.tool.version}` : ''}`
                              : detailAttr.tool}
                          </span>
                        </div>
                      )}
                      {detailAttr.commit_sha && <div className="detail-row"><span className="dl">Commit</span><span className="dv mono">{detailAttr.commit_sha}</span></div>}
                      <div className="detail-row"><span className="dl">Lines</span><span className="dv mono">{detailAttr.start_line ?? detailAttr.startLine}–{detailAttr.end_line ?? detailAttr.endLine}</span></div>
                      {detailAttr.conversation_summary && <div style={{ marginTop: 6, fontSize: 11, color: '#6b7280', lineHeight: 1.4 }}>{detailAttr.conversation_summary}</div>}
                    </>
                  ) : (
                    <div style={{ color: '#9ca3af', fontSize: 11 }}>No trace attribution for this line.</div>
                  )}
                </CollapsibleSection>
              )}

              {showTraceBlame && (attributions.length > 0 || lines.length > 0) && (
                <CollapsibleSection title="File Attribution (by trace)" headerClass="trace" defaultOpen={false}>
                  <div className="attr-map-view-toggle">
                    <button
                      type="button"
                      className={attrMapView === 'list' ? 'active' : ''}
                      onClick={() => setAttrMapView('list')}
                    >
                      List
                    </button>
                    <button
                      type="button"
                      className={attrMapView === 'chart' ? 'active' : ''}
                      onClick={() => setAttrMapView('chart')}
                    >
                      Chart
                    </button>
                  </div>
                  {attrMapView === 'list' ? (
                    <div className="attr-file-map attr-file-map-by-trace">
                      {[...attributionsByTraceId.entries()].map(([traceKey, attrs]) => {
                        const isTraceId = traceKey.startsWith('__no_trace__:') === false;
                        const firstAttr = attrs[0];
                        const label = firstAttr.attribution_label || 'AI';
                        const c = getLineColors(firstAttr, traceColorMap);
                        const totalLines = lines.length;
                        const pct = totalLines ? (countDistinctLinesCovered(attrs) / totalLines * 100) : 0;
                        const isPinnedTrace = pinnedTraceKey === traceKey;
                        return (
                          <div key={traceKey} className={`attr-trace-group ${isPinnedTrace ? 'pinned-trace' : ''}`}>
                            <div
                              className="attr-trace-group-header"
                              style={{ borderLeftColor: c.strip }}
                            >
                              <span className="attr-trace-label-row">
                                <span className={`attr-trace-label attr-badge ${label.toLowerCase()}`}>
                                  {label}
                                </span>
                                <span className="attr-trace-pct">({pct.toFixed(1)}%)</span>
                              </span>
                              {isTraceId && firstAttr.trace_id && (
                                <span className="attr-trace-id mono" title={firstAttr.trace_id}>
                                  {firstAttr.trace_id}
                                </span>
                              )}
                              {firstAttr.model_id && (
                                <span className="attr-trace-model">{firstAttr.model_id}</span>
                              )}
                            </div>
                            <div className="attr-trace-ranges">
                              {attrs.map((a, idx) => {
                                const start = a.start_line ?? a.startLine;
                                const end = a.end_line ?? a.endLine;
                                const isActive = pinnedLine != null && pinnedLine >= start && pinnedLine <= end;
                                return (
                                  <div
                                    key={idx}
                                    className={`attr-map-row ${isActive ? 'active' : ''}`}
                                    onClick={() => setPinnedLine(start)}
                                  >
                                    <div className="attr-map-swatch" style={{ background: c.bg, border: `1px solid ${c.strip}` }} />
                                    <span className="attr-map-lines">L{start}–{end}</span>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })}
                      {noAttributionStats.pct > 0 && (
                        <div key="__no_attribution__" className={`attr-trace-group ${pinnedTraceKey === '__no_attribution__' ? 'pinned-trace' : ''}`}>
                          <div
                            className="attr-trace-group-header"
                            style={{ borderLeftColor: NO_ATTRIBUTION_COLORS.strip }}
                          >
                            <span className="attr-trace-label-row">
                              <span className="attr-trace-label attr-badge none">No attribution</span>
                              <span className="attr-trace-pct">({noAttributionStats.pct.toFixed(1)}%)</span>
                            </span>
                          </div>
                          <div className="attr-trace-ranges">
                            {noAttributionStats.ranges.map((r, idx) => (
                              <div
                                key={idx}
                                className="attr-map-row"
                                onClick={() => setPinnedLine(r.start)}
                              >
                                <div className="attr-map-swatch" style={{ background: NO_ATTRIBUTION_COLORS.bg, border: `1px solid ${NO_ATTRIBUTION_COLORS.strip}` }} />
                                <span className="attr-map-lines">L{r.start}–{r.end}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div
                      className="attr-pie-wrap"
                      onMouseLeave={() => setChartHover(null)}
                    >
                      {pieSegments.length > 0 ? (
                        <>
                          <svg className="attr-pie" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
                            {pieSegments.map((seg) => {
                              const isPinnedSlice = pinnedTraceKey === seg.traceKey;
                              return (
                                <path
                                  key={seg.traceKey}
                                  d={pieSlicePath(50, 50, 45, seg.startAngle, seg.endAngle)}
                                  fill={seg.color}
                                  stroke={isPinnedSlice ? 'rgba(0,0,0,0.4)' : 'none'}
                                  strokeWidth={isPinnedSlice ? 2 : 0}
                                  strokeLinejoin="round"
                                  className={`attr-pie-slice ${chartHover?.traceKey === seg.traceKey ? 'hover' : ''} ${isPinnedSlice ? 'pinned' : ''}`}
                                  onMouseEnter={() => setChartHover(seg)}
                                  onClick={() => seg.attrs.length > 0 && setPinnedLine(seg.attrs[0].start_line ?? seg.attrs[0].startLine)}
                                />
                              );
                            })}
                          </svg>
                          <div className="attr-pie-legend">
                            {pieLegendItems.map((item) => (
                              <div key={item.key} className={`attr-pie-legend-item ${pieSegments.some((s) => s.traceKey === pinnedTraceKey && s.label === item.label) ? 'pinned' : ''}`}>
                                <div
                                  className="attr-pie-legend-swatch"
                                  style={{ background: item.color }}
                                />
                                <span className="attr-pie-legend-label">{item.label}</span>
                                <span className="attr-pie-legend-pct">({item.pct.toFixed(1)}%)</span>
                              </div>
                            ))}
                          </div>
                        </>
                      ) : (
                        <div className="attr-pie-empty">No attributed lines</div>
                      )}
                      {chartHover && (
                        <div className="attr-pie-tooltip">
                          <span className={`attr-badge ${chartHover.label === 'No attribution' ? 'none' : chartHover.label.toLowerCase()}`}>
                            {chartHover.label}
                          </span>
                          <span className="attr-pie-tooltip-pct">{chartHover.pct.toFixed(1)}%</span>
                          {chartHover.firstAttr?.trace_id && (
                            <div className="attr-pie-tooltip-trace mono">{chartHover.firstAttr.trace_id}</div>
                          )}
                          {chartHover.firstAttr?.model_id && (
                            <div className="attr-pie-tooltip-model">{chartHover.firstAttr.model_id}</div>
                          )}
                          <div className="attr-pie-tooltip-ranges">
                            {chartHover.label === 'No attribution' && chartHover.gapRanges
                              ? chartHover.gapRanges.map((r, i) => (
                                  <span key={i} className="attr-pie-tooltip-range" onClick={() => setPinnedLine(r.start)}>
                                    L{r.start}–{r.end}
                                  </span>
                                ))
                              : chartHover.attrs.map((a, i) => {
                                  const start = a.start_line ?? a.startLine;
                                  const end = a.end_line ?? a.endLine;
                                  return (
                                    <span key={i} className="attr-pie-tooltip-range" onClick={() => setPinnedLine(start)}>
                                      L{start}–{end}
                                    </span>
                                  );
                                })}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </CollapsibleSection>
              )}

              {showTraceBlame && conversationUrl != null && (
                <ConversationPanel
                  content={conversationContent}
                  loading={conversationLoading}
                  error={conversationError}
                  onRetry={() => fetchConversation(conversationUrl)}
                  onMaximize={() => setConvMaximized(true)}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* ─── Popover (shows even with sidepane) ───────── */}
      {popover && (
        <div className="attr-popover" style={{ left: popover.x, top: popover.y }}>
          {popover.attr && (
            <>
              <div className="pop-header">
                <span className={`pop-badge ${(popover.attr.attribution_label || popover.attr.contributor_type || 'ai').toLowerCase()}`}>
                  {popover.attr.contributor_type || popover.attr.attribution_label || 'AI'}
                </span>
                {popover.attr.model_id && <span style={{ fontSize: 12, fontWeight: 700, color: '#1d4ed8' }}>{popover.attr.model_id}</span>}
              </div>
              {popover.attr.timestamp && <div className="pop-row"><span className="label">Timestamp</span><span className="value">{new Date(popover.attr.timestamp).toLocaleString()}</span></div>}
              {popover.attr.tool && (
                <div className="pop-row">
                  <span className="label">Tool</span>
                  <span className="value">
                    {typeof popover.attr.tool === 'object'
                      ? `${popover.attr.tool.name || '—'}${popover.attr.tool.version ? ` v${popover.attr.tool.version}` : ''}`
                      : popover.attr.tool}
                  </span>
                </div>
              )}
              <div className="pop-row"><span className="label">Lines</span><span className="value">{popover.attr.start_line ?? popover.attr.startLine}–{popover.attr.end_line ?? popover.attr.endLine}</span></div>
              {popover.attr.trace_id && <div className="pop-row"><span className="label">Trace</span><span className="value">{popover.attr.trace_id}</span></div>}
            </>
          )}
          {popover.seg && (
            <>
              <div className="pop-row"><span className="label">Author</span><span className="value">{popover.seg.author ?? '—'}</span></div>
              <div className="pop-row"><span className="label">Commit</span><span className="value">{popover.seg.commit_sha ?? '—'}</span></div>
            </>
          )}
          <div className="pop-hint">Click to pin and see details</div>
        </div>
      )}

      {/* ─── Maximized conversation modal ─────────────── */}
      {convMaximized && conversationUrl && (
        <ConversationModal conversationUrl={conversationUrl} onClose={() => setConvMaximized(false)} />
      )}
    </div>
  );
}
