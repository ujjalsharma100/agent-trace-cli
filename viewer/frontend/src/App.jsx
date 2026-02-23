import React, { useEffect, useState } from 'react';
import FileTree from './components/FileTree';
import FileViewer from './components/FileViewer';

const API = '';

export default function App() {
  const [project, setProject] = useState(null);
  const [selectedPath, setSelectedPath] = useState('');
  const [fileContent, setFileContent] = useState('');
  const [gitBlameSegments, setGitBlameSegments] = useState(null);
  const [agentTraceBlame, setAgentTraceBlame] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    fetch(`${API}/api/project`)
      .then((r) => r.json())
      .then(setProject)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (!selectedPath) {
      setFileContent('');
      setGitBlameSegments(null);
      setAgentTraceBlame(null);
      return;
    }
    setFileContent('Loading...');
    setGitBlameSegments(null);
    setAgentTraceBlame(null);
    fetch(`${API}/api/file?path=${encodeURIComponent(selectedPath)}`)
      .then((r) => {
        if (!r.ok) throw new Error(r.statusText);
        return r.text();
      })
      .then(setFileContent)
      .catch((e) => setFileContent(`Error: ${e.message}`));

    Promise.all([
      fetch(`${API}/api/git-blame?path=${encodeURIComponent(selectedPath)}`).then((r) =>
        r.ok ? r.json() : { segments: [] }
      ),
      fetch(`${API}/api/agent-trace-blame?path=${encodeURIComponent(selectedPath)}`).then((r) =>
        r.ok ? r.json() : { file: selectedPath, attributions: [] }
      ),
    ])
      .then(([gitData, agentData]) => {
        setGitBlameSegments(gitData.segments ?? []);
        setAgentTraceBlame(agentData);
      })
      .catch(() => {
        setGitBlameSegments([]);
        setAgentTraceBlame({ file: selectedPath, attributions: [] });
      });
  }, [selectedPath]);

  if (error) return <div style={{ padding: 16, color: 'red' }}>{error}</div>;
  if (!project) {
    return (
      <div className="app-layout">
        <div className="empty-state">
          <div style={{ color: '#6366f1', fontSize: 14 }}>Loading project...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>Agent Trace</h2>
          <div className="project-name" title={project.root}>
            {project.root.replace(/^.*\//, '') || project.root}
          </div>
          <div className="project-meta">
            {project.storage} · {project.has_agent_trace ? 'Traced' : 'No traces'}
          </div>
        </div>
        <div className="sidebar-files">
          <FileTree
            selectedPath={selectedPath}
            onSelectFile={setSelectedPath}
            project={project}
          />
        </div>
        <div className="sidebar-footer">
          © Ujjal Sharma 2026
        </div>
      </aside>
      <main className="main-area">
        {selectedPath ? (
          <>
            <div className="file-path-bar">{selectedPath}</div>
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              {fileContent.startsWith('Error:') || fileContent === 'Loading...' ? (
                <div className="empty-state">
                  <div style={{ color: fileContent.startsWith('Error:') ? '#ef4444' : '#6b7280', fontSize: 13 }}>
                    {fileContent}
                  </div>
                </div>
              ) : (
                <FileViewer
                  path={selectedPath}
                  content={fileContent}
                  gitBlameSegments={gitBlameSegments}
                  agentTraceBlame={agentTraceBlame}
                />
              )}
            </div>
          </>
        ) : (
          <div className="empty-state">
            <div className="icon">📄</div>
            <div>Select a file from the sidebar</div>
          </div>
        )}
      </main>
    </div>
  );
}
