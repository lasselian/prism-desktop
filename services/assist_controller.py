"""
Assist Controller for Prism Desktop
Orchestrates text and push-to-talk voice sessions against Home Assistant's
conversation/Assist-pipeline APIs over the shared HAWebSocket connection.
"""

import asyncio
import logging
from typing import Callable, Optional

import aiohttp
from PyQt6.QtCore import QObject, pyqtSignal, QBuffer, QIODeviceBase
from PyQt6.QtMultimedia import (
    QAudio, QAudioFormat, QAudioSource, QMediaDevices, QMediaPlayer, QAudioOutput,
)

from core.ha_client import HAClient
from core.ha_websocket import HAWebSocket

logger = logging.getLogger(__name__)

_AUDIO_SAMPLE_RATE = 16000


def _pipeline_audio_format() -> QAudioFormat:
    fmt = QAudioFormat()
    fmt.setSampleRate(_AUDIO_SAMPLE_RATE)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    return fmt


def _find_audio_device(devices, device_id: str):
    """Match a QAudioDevice by the id string persisted in config."""
    if not device_id:
        return None
    for device in devices:
        if bytes(device.id()).decode('utf-8', errors='surrogateescape') == device_id:
            return device
    return None


class AssistController(QObject):
    """Owns one Assist "session": text submissions and push-to-talk voice
    recording, both routed through the shared HAWebSocket connection.

    The WebSocket connection is recreated on reconnect/settings changes, so
    it's looked up lazily via `get_websocket` rather than held directly.
    """

    listening_started = pyqtSignal()
    listening_stopped = pyqtSignal()
    heard = pyqtSignal(str)
    thinking = pyqtSignal()
    reply_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        get_websocket: Callable[[], Optional[HAWebSocket]],
        ha_client: HAClient,
        get_config: Callable[[], dict] = lambda: {},
    ):
        super().__init__()
        self._get_websocket = get_websocket
        self._ha_client = ha_client
        self._get_config = get_config
        self.conversation_id: Optional[str] = None

        self._audio_source: Optional[QAudioSource] = None
        self._audio_device = None  # QIODevice
        self._audio_queue: Optional[asyncio.Queue] = None
        self._pipeline_task: Optional[asyncio.Task] = None
        self._recording = False
        self._bytes_captured = 0
        self._capture_sample_rate = _AUDIO_SAMPLE_RATE

        self._media_player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._tts_buffer: Optional[QBuffer] = None
        self._tts_task: Optional[asyncio.Task] = None

    def reset_conversation(self):
        """Start a fresh conversation (call when the Assist popup opens)."""
        self.conversation_id = None

    def cancel(self):
        """Abort any in-flight recording/pipeline/playback (call when the popup closes)."""
        if self._recording:
            self._stop_recording()
        if self._pipeline_task and not self._pipeline_task.done():
            self._pipeline_task.cancel()
        self._pipeline_task = None
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        self._tts_task = None
        self._stop_tts_playback()

    def submit_text(self, text: str):
        text = text.strip()
        if not text:
            return
        ws = self._get_websocket()
        if not ws:
            self.error.emit("Not connected to Home Assistant")
            return
        self.thinking.emit()
        asyncio.create_task(self._submit_text_async(ws, text))

    async def _submit_text_async(self, ws: HAWebSocket, text: str):
        try:
            result = await ws.conversation_process(text, conversation_id=self.conversation_id)
        except Exception as e:
            logger.error(f"Assist conversation/process failed: {e}")
            self.error.emit(str(e) or "Assist request failed")
            return
        self._handle_conversation_result(result)

    def _handle_conversation_result(self, result: dict):
        self.conversation_id = result.get('conversation_id') or self.conversation_id
        response = result.get('response', {}) or {}
        speech = response.get('speech', {}).get('plain', {}).get('speech', '')
        self.reply_ready.emit(speech or "Done.")

    def start_voice(self):
        if self._recording:
            return
        if self._pipeline_task is not None and not self._pipeline_task.done():
            # Previous run (e.g. still streaming TTS back) hasn't finished — starting
            # a second one now would race on shared state (_audio_queue, conversation_id).
            self.error.emit("Assist is still finishing the previous request")
            return
        ws = self._get_websocket()
        if not ws:
            self.error.emit("Not connected to Home Assistant")
            return

        assist_cfg = (self._get_config() or {}).get('assist', {})
        device = (
            _find_audio_device(QMediaDevices.audioInputs(), assist_cfg.get('mic_device_id', ''))
            or QMediaDevices.defaultAudioInput()
        )
        if device.isNull():
            self.error.emit("No microphone found")
            return
        logger.info(f"Assist: using microphone '{device.description()}'")

        self._recording = True
        self._bytes_captured = 0
        self._capture_sample_rate = _AUDIO_SAMPLE_RATE
        self._audio_queue = asyncio.Queue()

        requested_fmt = _pipeline_audio_format()
        if not device.isFormatSupported(requested_fmt):
            logger.warning(
                f"Assist: '{device.description()}' doesn't natively support "
                f"16000Hz/1ch/Int16 — capture may be resampled by the backend"
            )

        self._audio_source = QAudioSource(device, requested_fmt)
        self._audio_device = self._audio_source.start()
        if self._audio_source.error() != QAudio.Error.NoError:
            logger.error(f"Failed to open microphone '{device.description()}': {self._audio_source.error()}")
            self.error.emit(f"Could not access microphone: {self._audio_source.error().name}")
            self._recording = False
            self._audio_source.deleteLater()
            self._audio_source = None
            self._audio_device = None
            return

        # The backend may not honor the requested format exactly — use whatever
        # it actually negotiated so the pipeline tells HA the true sample rate,
        # rather than assuming the request was granted and sending a mismatched
        # rate (which decodes real audio at the wrong speed/pitch).
        actual_fmt = self._audio_source.format()
        self._capture_sample_rate = actual_fmt.sampleRate()
        logger.info(
            f"Assist: mic capture format — {actual_fmt.sampleRate()}Hz, "
            f"{actual_fmt.channelCount()}ch, {actual_fmt.sampleFormat().name}"
        )
        if actual_fmt.channelCount() != 1 or actual_fmt.sampleFormat() != QAudioFormat.SampleFormat.Int16:
            logger.warning(
                "Assist: mic capture format isn't mono Int16 — HA will likely "
                "fail to transcribe this stream"
            )

        if self._audio_device is not None:
            self._audio_device.readyRead.connect(self._on_audio_ready)

        self.listening_started.emit()
        self._pipeline_task = asyncio.create_task(self._run_pipeline(ws))

    def stop_voice(self):
        """Manual early-stop: user released push-to-talk before HA's VAD ended it."""
        if not self._recording:
            return
        self._stop_recording()

    def _on_audio_ready(self):
        if not self._audio_device or self._audio_queue is None:
            return
        data = bytes(self._audio_device.readAll())
        if data:
            self._bytes_captured += len(data)
            self._audio_queue.put_nowait(data)

    def _stop_recording(self):
        self._recording = False
        if self._audio_device is not None:
            try:
                self._audio_device.readyRead.disconnect(self._on_audio_ready)
            except (TypeError, RuntimeError):
                pass
        if self._audio_source:
            self._audio_source.stop()
            self._audio_source.deleteLater()
        self._audio_source = None
        self._audio_device = None
        self.listening_stopped.emit()
        if self._bytes_captured == 0:
            logger.warning("Assist: mic stopped with zero bytes captured — check input device/permissions")
        else:
            logger.info(f"Assist: captured {self._bytes_captured} bytes of mic audio")
        if self._audio_queue is not None:
            self._audio_queue.put_nowait(None)  # sentinel: end of mic input

    async def _run_pipeline(self, ws: HAWebSocket):
        handler_id: Optional[int] = None
        sender_task: Optional[asyncio.Task] = None
        try:
            async for event in ws.run_assist_pipeline(
                conversation_id=self.conversation_id, sample_rate=self._capture_sample_rate
            ):
                etype = event.get('type')
                data = event.get('data', {}) or {}

                if etype == 'run-start':
                    handler_id = data.get('runner_data', {}).get('stt_binary_handler_id')
                elif etype == 'stt-start':
                    if handler_id is not None and self._audio_queue is not None:
                        sender_task = asyncio.create_task(self._stream_audio(ws, handler_id))
                elif etype in ('stt-end', 'stt-vad-end'):
                    stt_text = data.get('stt_output', {}).get('text', '')
                    if stt_text:
                        self.heard.emit(stt_text)
                    if self._recording:
                        self._stop_recording()
                elif etype == 'intent-start':
                    self.thinking.emit()
                elif etype == 'intent-end':
                    self._handle_conversation_result(data.get('intent_output', {}) or {})
                elif etype == 'tts-end':
                    url = (data.get('tts_output', {}) or {}).get('url')
                    if url:
                        self._tts_task = asyncio.create_task(self._play_tts(url))
                elif etype == 'error':
                    message = data.get('message', 'Assist pipeline error')
                    logger.error(f"Assist pipeline error: {message}")
                    self.error.emit(message)
                elif etype == 'run-end':
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Assist pipeline failed: {e}")
            self.error.emit(str(e) or "Assist voice request failed")
        finally:
            if self._recording:
                self._stop_recording()
            if sender_task and not sender_task.done():
                sender_task.cancel()

    async def _stream_audio(self, ws: HAWebSocket, handler_id: int):
        """Drain captured mic chunks to HA until recording stops (sentinel=None)."""
        assert self._audio_queue is not None
        try:
            while True:
                chunk = await self._audio_queue.get()
                if chunk is None:
                    break
                await ws.send_stt_audio(handler_id, chunk)
            await ws.end_stt_audio(handler_id)
        except Exception as e:
            logger.error(f"Failed to stream Assist audio: {e}")

    async def _play_tts(self, url: str):
        """Fetch the TTS reply and play it back."""
        base_url = self._ha_client.url
        full_url = url if url.startswith('http') else f"{base_url}{url}"
        try:
            async with aiohttp.ClientSession(headers=self._ha_client.headers) as session:
                async with session.get(full_url, timeout=10) as response:
                    if response.status != 200:
                        return
                    audio_bytes = await response.read()
        except Exception as e:
            logger.error(f"Failed to fetch Assist TTS audio: {e}")
            return

        # A previous reply's player/buffer would otherwise just get overwritten below —
        # still playing, still holding its FFmpeg decoder pipeline — leaking a bit more
        # of both on every voice turn.
        self._stop_tts_playback()

        self._tts_buffer = QBuffer()
        self._tts_buffer.setData(audio_bytes)
        self._tts_buffer.open(QIODeviceBase.OpenModeFlag.ReadOnly)

        assist_cfg = (self._get_config() or {}).get('assist', {})
        speaker = _find_audio_device(QMediaDevices.audioOutputs(), assist_cfg.get('speaker_device_id', ''))

        self._media_player = QMediaPlayer()
        self._audio_output = QAudioOutput(speaker) if speaker else QAudioOutput()
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setSourceDevice(self._tts_buffer)
        self._media_player.play()

    def _stop_tts_playback(self):
        if self._media_player is not None:
            self._media_player.stop()
            self._media_player.deleteLater()
            self._media_player = None
        if self._audio_output is not None:
            self._audio_output.deleteLater()
            self._audio_output = None
        if self._tts_buffer is not None:
            self._tts_buffer.close()
            self._tts_buffer.deleteLater()
            self._tts_buffer = None
