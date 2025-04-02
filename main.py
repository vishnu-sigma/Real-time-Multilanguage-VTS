import os
import tempfile
import whisper
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp as youtube_dl
import torch
import logging
import asyncio
import subprocess
import numpy as np
from datetime import datetime
from pathlib import Path
import time
from typing import Optional
from googletrans import Translator
import nltk
from nltk.tokenize import sent_tokenize
import signal
from contextlib import asynccontextmanager
from fastapi.responses import JSONResponse
import random

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global flag for shutdown
shutdown_event = asyncio.Event()

# Download NLTK data with robust error handling
try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    try:
        nltk.download('punkt', quiet=True)
        nltk.download('averaged_perceptron_tagger', quiet=True)
    except Exception as e:
        logger.warning(f"Failed to download NLTK data: {str(e)}")
        logger.warning("Using fallback text processing methods")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application lifespan events"""
    # Startup code
    logger.info("Starting up...")
    
    # Initialize Whisper model
    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Loading Whisper model on device: {device}")
    model = whisper.load_model("base", device=device)
    logger.info("Whisper model loaded successfully")
    
    # Initialize translator
    translator = Translator()
    
    # Store in app state
    app.state.model = model
    app.state.translator = translator
    app.state.processor = YouTubeLiveProcessor()
    
    yield
    
    # Shutdown code
    logger.info("Shutting down...")
    shutdown_event.set()

app = FastAPI(
    title="Real-Time Multi-Language Video Summarizer API",
    description="API for processing videos and generating multilingual summaries in real-time",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class YouTubeLiveProcessor:
    def __init__(self):
        self.stop_flag = False
        self.sample_rate = 16000
        self.max_retries = 3
        self.current_retries = 0
        self.last_update_time = 0
        self.transcript_buffer = []
        self.last_request_time = 0
        self.request_interval = 5  # Minimum seconds between requests
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0"
        ]

    async def process_stream(self, url: str, websocket: WebSocket, model):
        """Process YouTube live stream with robust format handling"""
        logger.info(f"Starting stream processing for URL: {url}")
        
        while not self.stop_flag and not shutdown_event.is_set() and self.current_retries < self.max_retries:
            current_time = time.time()
            if current_time - self.last_request_time < self.request_interval:
                await asyncio.sleep(self.request_interval - (current_time - self.last_request_time))
                continue
                
            temp_audio = None
            try:
                temp_audio = os.path.join(tempfile.gettempdir(), f"live_audio_{datetime.now().timestamp()}.wav")
                
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': temp_audio,
                    'extractaudio': True,
                    'audioformat': 'wav',
                    'quiet': True,
                    'no_warnings': True,
                    'live_from_start': True,
                    'retries': 10,
                    'socket_timeout': 30,
                    'ratelimit': 1000000,  # Limit download speed to avoid rate limiting
                    'sleep_interval': 5,    # Add delay between requests
                    'user_agent': random.choice(self.user_agents),
                    'postprocessor_args': {
                        'key': 'FFmpegExtractAudio',
                        'opts': ['-ac', '1', '-ar', str(self.sample_rate)]
                    },
                }

                with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                    self.last_request_time = time.time()
                    await websocket.send_json({"type": "status", "data": "Fetching stream data..."})
                    
                    try:
                        info = await asyncio.to_thread(ydl.extract_info, url, download=False)
                        if not info.get('url'):
                            raise Exception("No stream URL found")
                            
                        if not info.get('is_live', False):
                            await websocket.send_json({"type": "error", "data": "This doesn't appear to be a live stream"})
                            return
                            
                        await websocket.send_json({"type": "status", "data": "Stream found, starting processing..."})
                        
                        download_task = asyncio.create_task(
                            self.run_ydl_download(ydl, url, temp_audio)
                        )
                        await self.process_audio(temp_audio, websocket, model)
                        download_task.cancel()
                        return
                        
                    except Exception as e:
                        logger.warning(f"Stream extraction failed: {str(e)}")
                        raise

            except Exception as e:
                logger.error(f"Stream processing error: {str(e)}")
                self.current_retries += 1
                if self.current_retries < self.max_retries:
                    await websocket.send_json({
                        "type": "status", 
                        "data": f"Retrying... ({self.current_retries}/{self.max_retries})"
                    })
                    await asyncio.sleep(min(5 * self.current_retries, 30))  # Exponential backoff
                else:
                    await websocket.send_json({
                        "type": "error",
                        "data": f"Failed after {self.max_retries} attempts. Please try again later."
                    })
            finally:
                if temp_audio and os.path.exists(temp_audio):
                    try:
                        os.remove(temp_audio)
                    except:
                        pass
                await asyncio.sleep(1)  # Add small delay between attempts

    async def run_ydl_download(self, ydl, url: str, temp_audio: str):
        """Run yt-dlp download in background"""
        try:
            logger.info(f"Starting background download for {url}")
            await asyncio.to_thread(ydl.download, [url])
        except asyncio.CancelledError:
            logger.info("Download task cancelled")
        except Exception as e:
            logger.error(f"Background download failed: {str(e)}")

    async def process_audio(self, temp_audio: str, websocket: WebSocket, model):
        """Process audio chunks from temporary file"""
        logger.info(f"Starting audio processing for {temp_audio}")
        
        while not self.stop_flag and not shutdown_event.is_set():
            try:
                if not os.path.exists(temp_audio):
                    await asyncio.sleep(1)
                    continue

                current_time = time.time()
                if current_time - self.last_update_time > 5:
                    await websocket.send_json({
                        "type": "status",
                        "data": "Receiving stream data..."
                    })
                    self.last_update_time = current_time

                audio = self.load_audio_chunk(temp_audio)
                if audio is not None and len(audio) >= self.sample_rate * 5:
                    start_time = time.time()
                    result = model.transcribe(
                        audio,
                        fp16=torch.cuda.is_available()
                    )
                    
                    # Send transcription
                    await websocket.send_json({
                        "type": "transcription",
                        "data": result["text"],
                        "language": result["language"],
                        "processing_time": f"{time.time() - start_time:.1f}s"
                    })
                    
                    # Buffer transcripts for summary generation
                    self.transcript_buffer.append(result["text"])
                    if len(self.transcript_buffer) >= 3:  # Generate summary every 3 transcripts
                        combined_text = " ".join(self.transcript_buffer)
                        summary = summarize_text(combined_text)
                        await websocket.send_json({
                            "type": "summary",
                            "data": summary,
                            "timestamp": datetime.now().isoformat()
                        })
                        self.transcript_buffer = []
                    
                    try:
                        os.remove(temp_audio)
                    except:
                        pass
                    
            except Exception as e:
                logger.error(f"Audio processing error: {str(e)}")
                await asyncio.sleep(1)

    def load_audio_chunk(self, file_path: str) -> Optional[np.ndarray]:
        """Load audio chunk with FFmpeg"""
        try:
            cmd = [
                "ffmpeg",
                "-i", file_path,
                "-f", "s16le",
                "-ac", "1",
                "-ar", str(self.sample_rate),
                "-hide_banner",
                "-loglevel", "error",
                "-"
            ]
            out = subprocess.run(cmd, capture_output=True, check=True)
            return np.frombuffer(out.stdout, np.int16).flatten().astype(np.float32) / 32768.0
        except Exception as e:
            logger.error(f"Audio loading error: {str(e)}")
            return None

async def get_audio_duration(file_path: Path) -> float:
    """Get audio duration using ffprobe"""
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Could not get duration: {str(e)}")
        return 0.0

def summarize_text(text: str) -> str:
    """Robust text summarization with multiple fallback methods"""
    try:
        # Try NLTK sentence tokenizer first
        try:
            sentences = sent_tokenize(text)
        except:
            # Fallback to simple period-based splitting
            sentences = [s.strip() for s in text.split('.') if s.strip()]
        
        # Take first 3 sentences or first 200 chars
        if len(sentences) > 3:
            summary = ' '.join(sentences[:3]) + '...'
        else:
            summary = text
        
        # Ensure reasonable length
        return summary[:500] if len(summary) > 500 else summary
    except Exception as e:
        logger.error(f"Summarization error: {str(e)}")
        return text[:200] + '...' if len(text) > 200 else text

def translate_text(text: str, translator: Translator, target_lang: str) -> str:
    """Translation with chunking and error handling"""
    try:
        if not text.strip():
            return ""
            
        # Split long text into chunks
        chunks = [text[i:i+5000] for i in range(0, len(text), 5000)]
        translated_chunks = []
        
        for chunk in chunks:
            try:
                translation = translator.translate(chunk, dest=target_lang)
                translated_chunks.append(translation.text)
            except Exception as e:
                logger.warning(f"Translation chunk failed: {str(e)}")
                translated_chunks.append(f"[Partial translation: {chunk[:100]}...]")
                
        return ' '.join(translated_chunks)
    except Exception as e:
        logger.error(f"Translation error: {str(e)}")
        return f"[Translation failed for: {text[:50]}...]"

@app.post("/upload/")
async def upload_video(file: UploadFile = File(...)):
    if shutdown_event.is_set():
        raise HTTPException(503, "Service is shutting down")
    
    start_time = time.time()
    temp_path = None
    
    try:
        # Validate file type
        if not file.filename.lower().endswith(('.mp4', '.mov', '.avi')):
            raise HTTPException(400, "Only MP4, MOV, and AVI files are supported")

        # Validate file size (100MB limit)
        max_size = 100 * 1024 * 1024
        file_size = 0
        temp_path = Path(tempfile.gettempdir()) / f"upload_{int(time.time())}_{file.filename}"
        
        # Save file in chunks with size validation
        with open(temp_path, "wb") as buffer:
            while content := await file.read(1024 * 1024):  # 1MB chunks
                file_size += len(content)
                if file_size > max_size:
                    raise HTTPException(413, "File too large (max 100MB)")
                if shutdown_event.is_set():
                    raise HTTPException(503, "Service is shutting down")
                buffer.write(content)

        # Get audio duration for logging
        duration = await get_audio_duration(temp_path)
        logger.info(f"Processing file: {file.filename} ({file_size/1024/1024:.1f}MB, {duration:.1f}s)")

        # Transcribe using Whisper with automatic language detection
        result = await asyncio.to_thread(
            app.state.model.transcribe,
            str(temp_path),
            fp16=torch.cuda.is_available(),
            verbose=True
        )
        
        detected_lang = result["language"]
        original_text = result["text"]
        original_summary = summarize_text(original_text)

        # Define target languages for translation
        target_languages = {
            'en': 'english',
            'hi': 'hindi',
            'ml': 'malayalam'
        }
        
        # Remove detected language from targets
        target_languages.pop(detected_lang, None)
        
        # Generate translations
        translated_transcriptions = {}
        translated_summaries = {}
        
        for lang_code, lang_name in target_languages.items():
            translated_transcriptions[lang_name] = translate_text(original_text, app.state.translator, lang_code)
            translated_summaries[lang_name] = translate_text(original_summary, app.state.translator, lang_code)

        # Generate results
        processing_time = time.time() - start_time
        logger.info(f"Processing completed in {processing_time:.1f} seconds")
        
        return {
            "status": "completed",
            "filename": file.filename,
            "size_mb": file_size/1024/1024,
            "duration_seconds": duration,
            "detected_language": detected_lang,
            "original_transcription": original_text,
            "original_summary": original_summary,
            "processing_time_seconds": processing_time,
            "translated_transcriptions": translated_transcriptions,
            "translated_summaries": translated_summaries
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload processing error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": str(e),
                "filename": file.filename if file else "unknown"
            }
        )
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete temp file: {str(e)}")

@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    if shutdown_event.is_set():
        await websocket.close(code=1012)  # Service Restart
        return
        
    await websocket.accept()
    processor = app.state.processor
    processor.stop_flag = False
    processor.current_retries = 0
    processor.transcript_buffer = []
    
    try:
        data = await websocket.receive_json()
        url = data.get('url')
        
        if not url:
            await websocket.send_json({"type": "error", "data": "No URL provided"})
            return
            
        if "youtube.com" not in url and "youtu.be" not in url:
            await websocket.send_json({"type": "error", "data": "Only YouTube URLs are supported"})
            return
            
        try:
            with youtube_dl.YoutubeDL({'quiet': True}) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
                if not info.get('is_live', False):
                    await websocket.send_json({"type": "error", "data": "This doesn't appear to be a live stream"})
                    return
        except Exception as e:
            await websocket.send_json({"type": "error", "data": f"Could not verify stream: {str(e)}"})
            return
            
        await websocket.send_json({"type": "status", "data": "Starting live stream processing..."})
        
        # Process stream and send periodic updates
        await processor.process_stream(url, websocket, app.state.model)
        
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {str(e)}")
        try:
            await websocket.send_json({"type": "error", "data": str(e)})
        except:
            pass
    finally:
        processor.stop_flag = True
        try:
            await websocket.close()
        except:
            pass

def handle_shutdown(signum, frame):
    logger.info("Received shutdown signal")
    shutdown_event.set()

if __name__ == "__main__":
    import uvicorn
    
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
    
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        timeout_keep_alive=300,
        lifespan="on"
    )
    server = uvicorn.Server(config)
    
    try:
        server.run()
    except asyncio.CancelledError:
        logger.info("Server shutdown complete")
    except Exception as e:
        logger.error(f"Server error: {str(e)}")