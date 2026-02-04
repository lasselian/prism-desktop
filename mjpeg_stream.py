"""
MJPEG Stream Handler for Camera Buttons
Provides efficient streaming by maintaining a persistent HTTP connection
and parsing multipart MJPEG frames as they arrive.
"""

import base64
import requests
from typing import Optional
from PyQt6.QtCore import QObject, QThread, pyqtSignal, QMutex


class MJPEGStreamWorker(QObject):
    """Worker that runs in a thread to handle MJPEG streaming."""
    
    frame_received = pyqtSignal(str, str)  # entity_id, base64_data
    error_occurred = pyqtSignal(str, str)  # entity_id, error_message
    stopped = pyqtSignal(str)  # entity_id
    
    def __init__(self, entity_id: str, stream_url: str, token: str):
        super().__init__()
        self.entity_id = entity_id
        self.stream_url = stream_url
        self.token = token
        self._running = False
        self._mutex = QMutex()
    
    def start_streaming(self):
        """Start the MJPEG stream."""
        self._running = True
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "multipart/x-mixed-replace"
        }
        
        try:
            response = requests.get(
                self.stream_url,
                headers=headers,
                stream=True,
                timeout=30
            )
            
            if response.status_code != 200:
                self.error_occurred.emit(
                    self.entity_id, 
                    f"HTTP {response.status_code}"
                )
                return
            
            # Parse multipart MJPEG stream
            buffer = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                self._mutex.lock()
                running = self._running
                self._mutex.unlock()
                
                if not running:
                    break
                
                buffer.extend(chunk)
                
                # Look for JPEG frame boundaries
                # MJPEG streams use multipart boundaries like --boundary\r\n
                while True:
                    # Find start of JPEG (FFD8)
                    start = buffer.find(b'\xff\xd8')
                    if start == -1:
                        # Keep last 2 bytes in case boundary is split
                        if len(buffer) > 2:
                            del buffer[:len(buffer) - 2]
                        break
                    
                    # Find end of JPEG (FFD9)
                    end = buffer.find(b'\xff\xd9', start + 2)
                    if end == -1:
                        # Incomplete frame, wait for more data
                        break
                    
                    # Extract complete JPEG frame
                    frame = bytes(buffer[start:end + 2])
                    del buffer[:end + 2]
                    
                    # Convert to base64 and emit
                    b64_data = base64.b64encode(frame).decode('utf-8')
                    data_uri = f"data:image/jpeg;base64,{b64_data}"
                    self.frame_received.emit(self.entity_id, data_uri)
            
            response.close()
            
        except requests.exceptions.Timeout:
            self.error_occurred.emit(self.entity_id, "Connection timeout")
        except requests.exceptions.ConnectionError as e:
            self.error_occurred.emit(self.entity_id, f"Connection error: {e}")
        except Exception as e:
            self.error_occurred.emit(self.entity_id, f"Stream error: {e}")
        
        self.stopped.emit(self.entity_id)
    
    def stop_streaming(self):
        """Stop the MJPEG stream."""
        self._mutex.lock()
        self._running = False
        self._mutex.unlock()


class MJPEGStreamManager(QObject):
    """Manages multiple MJPEG streams for camera buttons."""
    
    frame_ready = pyqtSignal(str, str)  # entity_id, base64_data
    stream_error = pyqtSignal(str, str)  # entity_id, error_message
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._streams: dict[str, tuple[QThread, MJPEGStreamWorker]] = {}
    
    def start_stream(self, entity_id: str, base_url: str, token: str):
        """Start streaming for a camera entity."""
        if entity_id in self._streams:
            self.stop_stream(entity_id)
        
        # Build stream URL
        stream_url = f"{base_url}/api/camera_proxy_stream/{entity_id}"
        
        # Create worker and thread
        thread = QThread()
        worker = MJPEGStreamWorker(entity_id, stream_url, token)
        worker.moveToThread(thread)
        
        # Connect signals
        thread.started.connect(worker.start_streaming)
        worker.frame_received.connect(self._on_frame_received)
        worker.error_occurred.connect(self._on_error)
        worker.stopped.connect(self._on_stopped)
        
        # Store and start
        self._streams[entity_id] = (thread, worker)
        thread.start()
    
    def stop_stream(self, entity_id: str):
        """Stop streaming for a camera entity."""
        if entity_id not in self._streams:
            return
        
        thread, worker = self._streams.pop(entity_id)
        worker.stop_streaming()
        thread.quit()
        thread.wait(2000)
    
    def stop_all(self):
        """Stop all active streams."""
        for entity_id in list(self._streams.keys()):
            self.stop_stream(entity_id)
    
    def _on_frame_received(self, entity_id: str, base64_data: str):
        """Forward frame to connected slots."""
        self.frame_ready.emit(entity_id, base64_data)
    
    def _on_error(self, entity_id: str, error_message: str):
        """Handle stream errors."""
        self.stream_error.emit(entity_id, error_message)
    
    def _on_stopped(self, entity_id: str):
        """Clean up when stream stops."""
        if entity_id in self._streams:
            thread, _ = self._streams.pop(entity_id)
            thread.quit()
            thread.wait(1000)
