import React, { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';

const API = '';

/* ─── Parsing ───────────────────────────────────────────── */

const USER_LINE_RE = /^\s*(user|human):\s*$/i;
const ASSISTANT_LINE_RE = /^\s*assistant:\s*$/i;

function parseCursorTranscript(content) {
  if (!content || typeof content !== 'string') return [];
  const lines = content.split('\n');
  let userStart = -1;
  let assistantStart = -1;
  for (let i = 0; i < lines.length; i++) {
    if (USER_LINE_RE.test(lines[i]) && userStart === -1) userStart = i;
    if (ASSISTANT_LINE_RE.test(lines[i]) && assistantStart === -1) assistantStart = i;
    if (userStart !== -1 && assistantStart !== -1) break;
  }
  const blocks = [];
  if (userStart !== -1 && (assistantStart === -1 || userStart < assistantStart)) {
    const end = assistantStart > userStart ? assistantStart : lines.length;
    const userContent = lines.slice(userStart + 1, end).join('\n').trim();
    if (userContent) blocks.push({ role: 'user', content: userContent, format: 'cursor' });
  }
  if (assistantStart !== -1) {
    const agentContent = lines.slice(assistantStart + 1).join('\n').trim();
    if (agentContent) blocks.push({ role: 'assistant', content: agentContent, format: 'cursor' });
  }
  if (blocks.length === 0 && content.trim()) {
    blocks.push({ role: 'raw', content: content.trim() });
  }
  return blocks;
}

function parseTaggedConversation(content) {
  if (!content || typeof content !== 'string') return [];
  const blocks = [];
  const tagNames = ['user', 'assistant', 'human', 'ai', 'message', 'system'];
  let pos = 0;
  const s = content;
  while (pos < s.length) {
    const open = s.indexOf('<', pos);
    if (open === -1) break;
    const closeBracket = s.indexOf('>', open);
    if (closeBracket === -1) break;
    const tagPart = s.slice(open + 1, closeBracket).trim();
    const tagName = (tagPart.split(/\s/)[0] || '').toLowerCase();
    if (!tagNames.includes(tagName)) { pos = open + 1; continue; }
    const closeTag = `</${tagName}>`;
    const end = s.indexOf(closeTag, closeBracket + 1);
    if (end === -1) break;
    const body = s.slice(closeBracket + 1, end).trim();
    const role = tagName === 'message' ? (tagPart.match(/role\s*=\s*["']?(\w+)/i)?.[1] || 'message') : tagName;
    blocks.push({ role, rawTag: tagName, content: body, format: 'tag' });
    pos = end + closeTag.length;
  }
  if (blocks.length === 0 && content.trim()) {
    blocks.push({ role: 'raw', content: content.trim() });
  }
  return blocks;
}

function parseConversation(content) {
  if (!content || typeof content !== 'string') return [];
  const s = content.trim();
  if (!s) return [];
  if (s.split('\n').some((line) => USER_LINE_RE.test(line) || ASSISTANT_LINE_RE.test(line))) {
    const cursorBlocks = parseCursorTranscript(content);
    if (cursorBlocks.length > 0) return cursorBlocks;
  }
  const tagBlocks = parseTaggedConversation(content);
  if (tagBlocks.length > 0) return tagBlocks;
  return [{ role: 'raw', content: s }];
}

/* ─── Formatting ────────────────────────────────────────── */

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br/>');
}

function applyMarkdown(text) {
  if (!text) return '';
  const placeholders = [];
  let out = String(text)
    .replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      const id = placeholders.length;
      placeholders.push(
        '<pre class="conv-code-block" data-lang="' + escapeHtml(lang) + '"><code>' +
          escapeHtml(code.trim()) + '</code></pre>'
      );
      return '\u0001' + id + '\u0001';
    });
  out = escapeHtml(out);
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/`([^`]+)`/g, '<code class="conv-inline-code">$1</code>');
  placeholders.forEach((ph, i) => {
    out = out.split('\u0001' + i + '\u0001').join(ph);
  });
  return out;
}

function formatUserContent(text) {
  if (!text) return '';
  const m = text.match(/<user_query>([\s\S]*?)<\/user_query>/i);
  const plain = m ? m[1].trim() : text;
  return escapeHtml(plain);
}

function formatAgentContent(text) {
  return applyMarkdown(text || '');
}

/* ─── Components ────────────────────────────────────────── */

const AGENT_ROLES = new Set(['assistant', 'ai', 'message']);
const COLLAPSE_THRESHOLD = 300; // characters before auto-collapse

/**
 * ChatBubble — each message is collapsible like a section.
 * Long messages start collapsed; short ones start expanded.
 * When expanded, full content is visible with no max-height cap.
 */
function ChatBubble({ block, index }) {
  const isAgent = AGENT_ROLES.has(block.role);
  const isUser = block.role === 'user' || block.role === 'human';
  const isCursor = block.format === 'cursor';
  const isLong = block.content.length > COLLAPSE_THRESHOLD;
  const [collapsed, setCollapsed] = useState(isLong);

  const formatted =
    isCursor && isUser
      ? formatUserContent(block.content)
      : isCursor && block.role === 'assistant'
        ? formatAgentContent(block.content)
        : block.format === 'tag' || block.role === 'raw'
          ? escapeHtml(block.content)
          : formatAgentContent(block.content);

  const roleClass = isUser ? 'user' : isAgent ? 'assistant' : 'raw';
  const label = isUser ? 'You' : isAgent ? 'Agent' : block.role;
  const initial = isUser ? 'U' : isAgent ? 'A' : '?';

  // Preview: first ~120 chars for collapsed long messages
  const previewText = isLong && collapsed ? block.content.slice(0, 120).replace(/\n/g, ' ') + '...' : null;

  return (
    <div className={`chat-bubble ${roleClass}`}>
      {/* Clickable header — toggles collapse */}
      <button
        type="button"
        className="chat-bubble-header"
        onClick={() => setCollapsed(!collapsed)}
      >
        <span className="collapse-chevron">{collapsed ? '▸' : '▾'}</span>
        <div className="chat-bubble-avatar">{initial}</div>
        <span className="chat-role-label">{label}</span>
        {previewText && <span className="chat-preview">{previewText}</span>}
      </button>

      {/* Body — only rendered when expanded */}
      {!collapsed && (
        <div
          className="chat-bubble-body"
          dangerouslySetInnerHTML={{ __html: formatted }}
        />
      )}
    </div>
  );
}

/* ─── SVG Icons ─────────────────────────────────────────── */

const ChatIcon = () => (
  <svg className="chat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
);

const MaximizeIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="15 3 21 3 21 9" />
    <polyline points="9 21 3 21 3 15" />
    <line x1="21" y1="3" x2="14" y2="10" />
    <line x1="3" y1="21" x2="10" y2="14" />
  </svg>
);

/* ─── Inline conversation panel (for side pane) ─────────── */

export function ConversationPanel({ content, loading, error, onRetry, onMaximize }) {
  const [open, setOpen] = useState(true);
  const blocks = content != null ? parseConversation(content) : [];

  return (
    <div className="detail-card conv-card">
      {/* Collapsible header */}
      <button
        type="button"
        className="detail-card-header collapsible conv-header-btn"
        onClick={() => setOpen(!open)}
      >
        <span className="collapse-chevron">{open ? '▾' : '▸'}</span>
        <ChatIcon />
        <span>Conversation</span>
        {/* Maximize button in the header bar */}
        {onMaximize && (
          <span
            className="conv-maximize-inline"
            onClick={(e) => { e.stopPropagation(); onMaximize(); }}
            title="Open in full view"
          >
            <MaximizeIcon />
          </span>
        )}
      </button>

      {open && (
        <div className="conv-body">
          {loading && (
            <div style={{ color: '#9ca3af', fontSize: 12, textAlign: 'center', padding: 20 }}>
              Loading conversation...
            </div>
          )}
          {error && (
            <div style={{ color: '#ef4444', fontSize: 12, textAlign: 'center', padding: 20 }}>
              {error}
              {onRetry && <button type="button" onClick={onRetry} className="conv-retry-btn">Retry</button>}
            </div>
          )}
          {!loading && !error && blocks.length === 0 && content != null && (
            <pre style={{
              margin: 0, fontSize: 12,
              fontFamily: "'SF Mono', ui-monospace, monospace",
              whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#374151',
            }}>
              {content}
            </pre>
          )}
          {!loading && !error && blocks.length > 0 &&
            blocks.map((block, i) => (
              <ChatBubble key={i} block={block} index={i} />
            ))}
        </div>
      )}
    </div>
  );
}

/* ─── Full-screen modal version ─────────────────────────── */

export default function ConversationModal({ conversationUrl, onClose }) {
  const [content, setContent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchContent = useCallback(() => {
    if (!conversationUrl) return;
    setLoading(true);
    setError(null);
    setContent(null);
    fetch(`${API}/api/conversation?url=${encodeURIComponent(conversationUrl)}`)
      .then((r) => {
        if (!r.ok) return r.json().then((j) => Promise.reject(new Error(j.error || r.statusText)));
        return r.json();
      })
      .then((data) => {
        if (data.open_external && data.url) {
          window.open(data.url, '_blank', 'noopener,noreferrer');
          onClose();
          return;
        }
        setContent(data.content ?? '');
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message || 'Failed to load conversation');
        setLoading(false);
      });
  }, [conversationUrl]);

  useEffect(() => { fetchContent(); }, [fetchContent]);

  // Close on Escape key
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // Lock body scroll so only the modal body scrolls (stops background from scrolling)
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  const blocks = content != null ? parseConversation(content) : [];
  const overlayRef = useRef(null);

  // Only prevent wheel when scrolling over the backdrop (not the modal content), so modal body scrolls
  const handleOverlayWheel = (e) => {
    if (e.target === overlayRef.current) e.preventDefault();
  };

  const modal = (
    <div
      ref={overlayRef}
      className="conv-modal-overlay"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      onWheel={handleOverlayWheel}
      style={{ overflow: 'hidden' }}
    >
      <div className="conv-modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="conv-modal-header">
          <h3 className="conv-modal-title">
            <ChatIcon />
            Conversation
          </h3>
          <button type="button" className="conv-modal-close-btn" onClick={onClose}>
            Close &times;
          </button>
        </div>
        <div className="conv-modal-scroll">
          <div className="conv-modal-body">
            {loading && <div style={{ color: '#9ca3af', fontSize: 13, textAlign: 'center', padding: 40 }}>Loading conversation...</div>}
            {error && (
              <div style={{ color: '#ef4444', fontSize: 13, textAlign: 'center', padding: 40 }}>
                {error}
                <button type="button" onClick={fetchContent} className="conv-retry-btn" style={{ marginLeft: 8 }}>Retry</button>
              </div>
            )}
            {!loading && !error && blocks.length === 0 && (
              <pre style={{ margin: 0, fontSize: 12, fontFamily: "'SF Mono', ui-monospace, monospace", whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {content}
              </pre>
            )}
            {!loading && !error && blocks.length > 0 &&
              blocks.map((block, i) => (
                <ChatBubble key={i} block={block} index={i} />
              ))}
          </div>
        </div>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}
