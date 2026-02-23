import React, { useCallback, useState, useEffect } from 'react';

const API = '';

function TreeNode({
  entry,
  level,
  expandedDirs,
  onToggleDir,
  onSelectFile,
  treeCache,
  selectedPath,
}) {
  const isDir = entry.type === 'dir';
  const isExpanded = expandedDirs.has(entry.path);
  const children = isDir ? treeCache[entry.path] : null;
  const isSelected = entry.type === 'file' && entry.path === selectedPath;

  const handleClick = () => {
    if (isDir) {
      onToggleDir(entry.path);
    } else {
      onSelectFile(entry.path);
    }
  };

  return (
    <div>
      <button
        type="button"
        onClick={handleClick}
        className={`tree-node-btn ${isDir ? 'dir-node' : ''} ${isSelected ? 'selected' : ''}`}
        style={{ paddingLeft: 12 + level * 16 }}
      >
        {isDir && (
          <span className="chevron">{isExpanded ? '▾' : '▸'}</span>
        )}
        {!isDir && <span className="chevron" />}
        <span className="icon">{isDir ? '📁' : '📄'}</span>
        <span className="name" title={entry.path}>{entry.name}</span>
      </button>
      {isDir && isExpanded && Array.isArray(children) && (
        <div>
          {children.map((child) => (
            <TreeNode
              key={child.path}
              entry={child}
              level={level + 1}
              expandedDirs={expandedDirs}
              onToggleDir={onToggleDir}
              onSelectFile={onSelectFile}
              treeCache={treeCache}
              selectedPath={selectedPath}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function FileTree({ selectedPath, onSelectFile, project }) {
  const [rootEntries, setRootEntries] = useState([]);
  const [expandedDirs, setExpandedDirs] = useState(new Set());
  const [treeCache, setTreeCache] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchTree = useCallback(async (path) => {
    const res = await fetch(`${API}/api/tree?path=${encodeURIComponent(path)}`);
    if (!res.ok) throw new Error(res.statusText);
    const data = await res.json();
    return data.entries || [];
  }, []);

  useEffect(() => {
    if (!project) return;
    setLoading(true);
    setError(null);
    fetchTree('')
      .then((entries) => {
        setRootEntries(entries);
        setTreeCache((c) => ({ ...c, '': entries }));
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [project, fetchTree]);

  const onToggleDir = useCallback(
    async (path) => {
      setExpandedDirs((prev) => {
        const next = new Set(prev);
        if (next.has(path)) {
          next.delete(path);
        } else {
          next.add(path);
        }
        return next;
      });
      if (treeCache[path] !== undefined) return;
      try {
        const entries = await fetchTree(path);
        setTreeCache((c) => ({ ...c, [path]: entries }));
      } catch (e) {
        setError(String(e));
      }
    },
    [fetchTree, treeCache]
  );

  if (error) return <div style={{ padding: 8, fontSize: 12, color: '#ef4444' }}>{error}</div>;
  if (loading) return <div style={{ padding: 12, fontSize: 12, color: 'rgba(205,214,244,0.5)' }}>Loading...</div>;

  return (
    <div>
      {rootEntries.map((entry) => (
        <TreeNode
          key={entry.path}
          entry={entry}
          level={0}
          expandedDirs={expandedDirs}
          onToggleDir={onToggleDir}
          onSelectFile={onSelectFile}
          treeCache={treeCache}
          selectedPath={selectedPath}
        />
      ))}
    </div>
  );
}
