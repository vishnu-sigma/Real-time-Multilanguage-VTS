import React, { useState, useRef } from 'react';
import axios from 'axios';
import { uploadVideo } from './api';
import './index.css';

const App = () => {
  const [file, setFile] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [streamUrl, setStreamUrl] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [liveTranscripts, setLiveTranscripts] = useState([]);
  const [liveSummaries, setLiveSummaries] = useState([]);
  const fileInputRef = useRef(null);
  const cancelToken = useRef(null);
  const wsRef = useRef(null);

  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!file) return;
    
    setIsUploading(true);
    setProgress(0);
    setError(null);
    cancelToken.current = axios.CancelToken.source();
    
    try {
      const result = await uploadVideo(file, {
        onUploadProgress: (progressEvent) => {
          const percentCompleted = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total
          );
          setProgress(percentCompleted);
        },
        cancelToken: cancelToken.current.token
      });
      
      setResult(result);
    } catch (err) {
      if (!axios.isCancel(err)) {
        setError(err.message);
      }
    } finally {
      setIsUploading(false);
    }
  };

  const handleCancel = () => {
    if (cancelToken.current) {
      cancelToken.current.cancel('Upload cancelled by user');
      setIsUploading(false);
      setProgress(0);
    }
  };

  const handleStreamSubmit = async (e) => {
    e.preventDefault();
    if (!streamUrl) return;
    
    setIsStreaming(true);
    setLiveTranscripts([]);
    setLiveSummaries([]);
    setError(null);
    
    try {
      const ws = new WebSocket(`ws://localhost:8000/ws/live`);
      
      ws.onopen = () => {
        ws.send(JSON.stringify({ url: streamUrl }));
      };
      
      ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'transcription') {
          setLiveTranscripts(prev => [...prev, {
            text: data.data,
            language: data.language,
            timestamp: new Date().toLocaleTimeString()
          }]);
        } else if (data.type === 'summary') {
          setLiveSummaries(prev => [...prev, {
            text: data.data,
            timestamp: new Date().toLocaleTimeString()
          }]);
        } else if (data.type === 'error') {
          setError(data.data);
          setIsStreaming(false);
        }
      };
      
      ws.onclose = () => {
        setIsStreaming(false);
      };
      
      ws.onerror = (error) => {
        setError('WebSocket connection error');
        setIsStreaming(false);
      };

      wsRef.current = ws;
    } catch (err) {
      setError(err.message);
      setIsStreaming(false);
    }
  };

  const handleStopStream = () => {
    if (wsRef.current) {
      wsRef.current.close();
    }
    setIsStreaming(false);
  };

  return (
    <div className="app-container">
      <header className="app-header">
        <h1 className="gradient-title">Real-Time Video Transcript Summarizer</h1>
        <p className="subtitle">Upload videos or stream live content to get multilingual transcriptions and summaries</p>
      </header>

      <div className={`upload-section ${result ? 'collapsed' : ''}`}>
        <div className="upload-card" onClick={() => fileInputRef.current.click()}>
          <input
            type="file"
            ref={fileInputRef}
            className="hidden-input"
            onChange={handleFileChange}
            accept=".mp4,.mov,.avi"
          />
          <div className="upload-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M19 13H13V19H11V13H5V11H11V5H13V11H19V13Z" fill="currentColor"/>
            </svg>
          </div>
          <div className="upload-text">
            <h3>{file ? file.name : 'Click to select a video file'}</h3>
            <p>Supported formats: MP4, MOV, AVI (max 100MB)</p>
            {file && file.size > 100 * 1024 * 1024 && (
              <p className="warning">File exceeds 100MB limit</p>
            )}
          </div>
        </div>

        {file && !isUploading && (
          <button
            className="upload-btn"
            onClick={handleUpload}
            disabled={file.size > 100 * 1024 * 1024}
          >
            Process Video
          </button>
        )}

        {isUploading && (
          <div className="upload-controls">
            <button className="upload-btn processing" disabled>
              <span className="spinner"></span>
              Processing... {progress}%
            </button>
            <button className="cancel-btn" onClick={handleCancel}>
              Cancel
            </button>
          </div>
        )}

        {progress > 0 && progress < 100 && (
          <div className="progress-indicator">
            <div className="progress-bar" style={{ width: `${progress}%` }}></div>
          </div>
        )}
      </div>

      <div className="stream-section">
        <h2>Live Stream Processing</h2>
        <form className="stream-form" onSubmit={handleStreamSubmit}>
          <input
            type="text"
            className="stream-input"
            placeholder="Enter YouTube live stream URL"
            value={streamUrl}
            onChange={(e) => setStreamUrl(e.target.value)}
            disabled={isStreaming}
          />
          <button
            type={isStreaming ? 'button' : 'submit'}
            className={`stream-button ${isStreaming ? 'stop' : ''}`}
            onClick={isStreaming ? handleStopStream : null}
          >
            {isStreaming ? 'Stop' : 'Start'}
          </button>
        </form>

        {(liveTranscripts.length > 0 || liveSummaries.length > 0) && (
          <div className="live-results-grid">
            <div className="live-transcript-container">
              <h3>Live Transcript</h3>
              <div className="live-transcripts">
                {liveTranscripts.map((transcript, index) => (
                  <div key={index} className="transcript-item">
                    <div className="meta">
                      <span className="timestamp">{transcript.timestamp}</span>
                      <span className="language-badge">{transcript.language}</span>
                    </div>
                    <p>{transcript.text}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="live-summary-container">
              <h3>Live Summary</h3>
              <div className="live-summary">
                {liveSummaries.map((summary, index) => (
                  <div key={index} className="summary-item">
                    <div className="meta">
                      <span className="timestamp">{summary.timestamp}</span>
                    </div>
                    <p>{summary.text}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {error && (
        <div className="error-message">
          <p>{error}</p>
        </div>
      )}

      {result && (
        <div className="results-section">
          <div className="result-header">
            <h2>Analysis Results</h2>
            <span className="language-tag">
              {result.detected_language === 'en' ? 'English' : 
               result.detected_language === 'hi' ? 'Hindi' : 'Malayalam'}
            </span>
          </div>

          <div className="results-grid">
            <div className="result-card">
              <h3>Original Transcription ({result.detected_language === 'en' ? 'English' : 
                  result.detected_language === 'hi' ? 'Hindi' : 'Malayalam'})</h3>
              <div className="scrollable-content">
                {result.original_transcription}
              </div>
            </div>

            <div className="result-card">
              <h3>Original Summary ({result.detected_language === 'en' ? 'English' : 
                  result.detected_language === 'hi' ? 'Hindi' : 'Malayalam'})</h3>
              <div className="content">
                {result.original_summary}
              </div>
            </div>

            {result.translated_transcriptions && (
              <div className="result-card translations">
                <h3>Translations</h3>
                <div className="translation-list">
                  {Object.entries(result.translated_transcriptions).map(([lang, text]) => (
                    <div key={lang} className="translation-item">
                      <h4>{lang.charAt(0).toUpperCase() + lang.slice(1)} Transcription</h4>
                      <div className="scrollable-content">
                        <p>{text}</p>
                      </div>
                    </div>
                  ))}
                  {Object.entries(result.translated_summaries).map(([lang, text]) => (
                    <div key={`summary-${lang}`} className="translation-item">
                      <h4>{lang.charAt(0).toUpperCase() + lang.slice(1)} Summary</h4>
                      <div className="scrollable-content">
                        <p>{text}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default App;