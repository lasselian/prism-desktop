"""
Assist Controller for Prism Desktop
Orchestrates text and push-to-talk voice sessions against Home Assistant's
Assist pipeline API over the shared HAWebSocket connection.

Typed and spoken requests both go through `assist_pipeline/run` (typed ones
start at the intent stage), so they reach the same preferred pipeline and
conversation agent — and share one conversation_id.
"""

import asyncio
import logging
import math
import re
import time
from array import array
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, QBuffer, QIODeviceBase
from PyQt6.QtMultimedia import (
    QAudio,
    QAudioFormat,
    QAudioSource,
    QMediaDevices,
    QMediaPlayer,
    QAudioOutput,
)

from core.ha_client import HAClient
from core.ha_websocket import HAWebSocket
from core.localization_manager import t

logger = logging.getLogger(__name__)

_AUDIO_SAMPLE_RATE = 16000
_LEVEL_INTERVAL = 0.05  # seconds between input_level emissions
_SSML_TAG = re.compile(r"<[^>]+>")


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


def _pcm16_level(data: bytes) -> float:
    """Rough 0..1 loudness of a PCM16LE chunk (-50 dBFS → 0, -10 dBFS → 1)."""
    usable = len(data) - (len(data) % 2)
    if usable <= 0:
        return 0.0
    samples = array('h')
    samples.frombytes(data[:usable])
    mean_sq = sum(s * s for s in samples) / len(samples)
    if mean_sq <= 0:
        return 0.0
    db = 10 * math.log10(mean_sq / (32768.0 * 32768.0))
    return max(0.0, min(1.0, (db + 50.0) / 40.0))


def _speech_from_response(response: dict) -> str:
    speech = response.get('speech') or {}
    text = (speech.get('plain') or {}).get('speech') or ''
    if not text:
        ssml = (speech.get('ssml') or {}).get('speech') or ''
        text = _SSML_TAG.sub('', ssml).strip()
    return text


@dataclass
class _PipelineRun:
    """What one assist_pipeline/run has produced so far."""

    ws: HAWebSocket
    speak: bool
    handler_id: Optional[int] = None
    sender_task: Optional[asyncio.Task] = None
    got_result: bool = False
    had_error: bool = False
    continue_conversation: bool = False
    partial: str = ""


class AssistController(QObject):
    """Owns one Assist "session": text submissions and push-to-talk voice
    recording, both routed through the shared HAWebSocket connection.

    Only one request runs at a time (`busy`). The WebSocket connection is
    recreated on reconnect/settings changes, so it's looked up lazily via
    `get_websocket` rather than held directly.
    """

    listening_started = pyqtSignal()
    listening_stopped = pyqtSignal()
    processing = pyqtSignal()  # mic closed, waiting for speech-to-text
    heard = pyqtSignal(str)
    thinking = pyqtSignal()
    reply_partial = pyqtSignal(str)  # streamed reply so far (agents that stream)
    reply_ready = pyqtSignal(str, bool)  # final reply, is_error (agent couldn't handle it)
    notice = pyqtSignal(str)  # transient status, not a chat message
    error = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    speaking_changed = pyqtSignal(bool)
    input_level = pyqtSignal(float)

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
        self._recording = False
        self._bytes_captured = 0
        self._capture_sample_rate = _AUDIO_SAMPLE_RATE
        self._capture_is_int16 = True
        self._last_level_emit = 0.0

        self._run_task: Optional[asyncio.Task] = None
        self._run_token: Optional[object] = None
        self._busy = False
        self._follow_up_pending = False

        self._media_player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._tts_buffer: Optional[QBuffer] = None
        self._tts_task: Optional[asyncio.Task] = None
        self._speaking = False

    # ── Public API ──

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def speaking(self) -> bool:
        return self._speaking

    def reset_conversation(self):
        """Start a fresh conversation (popup opened, or "new chat")."""
        self.conversation_id = None

    def cancel(self):
        """Abort any in-flight recording/request/playback (popup closed, or
        the user pressed stop). Keeps the conversation_id."""
        self._follow_up_pending = False
        self._run_token = None  # late events from the old run are ignored
        if self._recording:
            self._stop_recording()
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
        self._run_task = None
        self._stop_tts()
        self._set_busy(False)

    def stop_speaking(self):
        self._follow_up_pending = False
        self._stop_tts()

    def submit_text(self, text: str):
        text = text.strip()
        if not text:
            return
        if self._busy:
            self.notice.emit(t("assist.status.busy"))
            return
        ws = self._get_websocket()
        if not ws:
            self.error.emit(t("assist.error.not_connected"))
            return
        self._follow_up_pending = False
        self._stop_tts()
        self.thinking.emit()
        self._start_run(ws, text=text)

    def start_voice(self):
        if self._recording:
            return
        if self._busy:
            self.notice.emit(t("assist.status.busy"))
            return
        ws = self._get_websocket()
        if not ws:
            self.error.emit(t("assist.error.not_connected"))
            return

        assist_cfg = self._assist_config()
        device = (
            _find_audio_device(QMediaDevices.audioInputs(), assist_cfg.get('mic_device_id', ''))
            or QMediaDevices.defaultAudioInput()
        )
        if device.isNull():
            self.error.emit(t("assist.error.no_microphone"))
            return
        logger.info(f"Assist: using microphone '{device.description()}'")

        # Talking over the previous reply cuts it off, like a voice satellite.
        self._follow_up_pending = False
        self._stop_tts()

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
            self.error.emit(t("assist.error.mic_access", error=self._audio_source.error().name))
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
        self._capture_is_int16 = actual_fmt.sampleFormat() == QAudioFormat.SampleFormat.Int16
        logger.info(
            f"Assist: mic capture format — {actual_fmt.sampleRate()}Hz, "
            f"{actual_fmt.channelCount()}ch, {actual_fmt.sampleFormat().name}"
        )
        if actual_fmt.channelCount() != 1 or not self._capture_is_int16:
            logger.warning(
                "Assist: mic capture format isn't mono Int16 — HA will likely fail to transcribe this stream"
            )

        if self._audio_device is not None:
            self._audio_device.readyRead.connect(self._on_audio_ready)

        self.listening_started.emit()
        self._start_run(ws, voice=True)

    def stop_voice(self):
        """Manual early-stop: user pressed stop before HA's VAD ended it."""
        self._finish_listening()

    # ── Internals ──

    def _assist_config(self) -> dict:
        return (self._get_config() or {}).get('assist', {}) or {}

    def _set_busy(self, busy: bool):
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)

    def _set_speaking(self, speaking: bool):
        if speaking != self._speaking:
            self._speaking = speaking
            self.speaking_changed.emit(speaking)

    def _start_run(self, ws: HAWebSocket, text: Optional[str] = None, voice: bool = False):
        token = object()
        self._run_token = token
        self._set_busy(True)
        self._run_task = asyncio.create_task(self._run(ws, token, text=text, voice=voice))

    def _on_audio_ready(self):
        if not self._audio_device or self._audio_queue is None:
            return
        data = bytes(self._audio_device.readAll())
        if not data:
            return
        self._bytes_captured += len(data)
        self._audio_queue.put_nowait(data)
        now = time.monotonic()
        if self._capture_is_int16 and now - self._last_level_emit >= _LEVEL_INTERVAL:
            self._last_level_emit = now
            self.input_level.emit(_pcm16_level(data))

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
        self.input_level.emit(0.0)
        self.listening_stopped.emit()
        if self._bytes_captured == 0:
            logger.warning("Assist: mic stopped with zero bytes captured — check input device/permissions")
        else:
            logger.info(f"Assist: captured {self._bytes_captured} bytes of mic audio")
        if self._audio_queue is not None:
            self._audio_queue.put_nowait(None)  # sentinel: end of mic input

    def _finish_listening(self):
        """Mic is done (VAD end, STT end or manual stop) — show processing."""
        if self._recording:
            self._stop_recording()
            self.processing.emit()

    async def _run(self, ws: HAWebSocket, token: object, text: Optional[str] = None, voice: bool = False):
        speak = voice and self._assist_config().get('speak_replies', True)
        run = _PipelineRun(ws=ws, speak=speak)
        try:
            async for event in ws.run_assist_pipeline(**self._pipeline_kwargs(text, speak)):
                if self._run_token is not token or self._on_pipeline_event(run, event):
                    break
            if self._run_token is token and not run.got_result and not run.had_error:
                # HA ends a run that hit its own timeout with run-end *before*
                # the error event, so an empty run is reported here.
                self.error.emit(t("assist.error.no_response"))
        except asyncio.TimeoutError:
            if self._run_token is token:
                self.error.emit(t("assist.error.timeout"))
        except Exception as e:
            logger.exception("Assist request failed")
            if self._run_token is token:
                self.error.emit(str(e) or t("assist.error.generic"))
        finally:
            self._finish_run(run, token, voice)

    def _pipeline_kwargs(self, text: Optional[str], speak: bool) -> dict:
        kwargs = {'conversation_id': self.conversation_id}
        if text is not None:
            kwargs.update(text=text, end_stage='intent')
        else:
            kwargs.update(sample_rate=self._capture_sample_rate, end_stage='tts' if speak else 'intent')
        return kwargs

    def _on_pipeline_event(self, run: _PipelineRun, event: dict) -> bool:
        """Apply one pipeline event; True when the run is over."""
        etype = event.get('type')
        if etype == 'run-end':
            return True
        handler = self._PIPELINE_EVENT_HANDLERS.get(etype)
        if handler is not None:
            handler(self, run, event.get('data', {}) or {})
        return False

    def _on_run_start(self, run: _PipelineRun, data: dict):
        run.handler_id = (data.get('runner_data', {}) or {}).get('stt_binary_handler_id')

    def _on_stt_start(self, run: _PipelineRun, data: dict):
        if run.handler_id is not None and self._audio_queue is not None:
            run.sender_task = asyncio.create_task(self._stream_audio(run.ws, run.handler_id))

    def _on_stt_vad_end(self, run: _PipelineRun, data: dict):
        self._finish_listening()

    def _on_stt_end(self, run: _PipelineRun, data: dict):
        stt_text = (data.get('stt_output', {}) or {}).get('text', '')
        if stt_text:
            self.heard.emit(stt_text)
        self._finish_listening()

    def _on_intent_start(self, run: _PipelineRun, data: dict):
        self.thinking.emit()

    def _on_intent_progress(self, run: _PipelineRun, data: dict):
        delta = data.get('chat_log_delta') or {}
        # A delta with a role starts a new message; only assistant
        # text is shown (tool_result deltas are skipped).
        if not isinstance(delta, dict) or delta.get('role') not in (None, 'assistant'):
            return
        if delta.get('role') == 'assistant':
            run.partial = ""  # a new assistant message (e.g. after a tool call)
        content = delta.get('content')
        if isinstance(content, str) and content:
            run.partial += content
            self.reply_partial.emit(run.partial)

    def _on_intent_end(self, run: _PipelineRun, data: dict):
        run.got_result = True
        run.continue_conversation = self._handle_conversation_result(data.get('intent_output', {}) or {})

    def _on_tts_end(self, run: _PipelineRun, data: dict):
        url = (data.get('tts_output', {}) or {}).get('url')
        if url and run.speak:
            self._tts_task = asyncio.create_task(self._play_tts(url))

    def _on_pipeline_error(self, run: _PipelineRun, data: dict):
        run.had_error = True
        self._report_pipeline_error(data)

    _PIPELINE_EVENT_HANDLERS = {
        'run-start': _on_run_start,
        'stt-start': _on_stt_start,
        'stt-vad-end': _on_stt_vad_end,
        'stt-end': _on_stt_end,
        'intent-start': _on_intent_start,
        'intent-progress': _on_intent_progress,
        'intent-end': _on_intent_end,
        'tts-end': _on_tts_end,
        'error': _on_pipeline_error,
    }

    def _finish_run(self, run: _PipelineRun, token: object, voice: bool):
        if self._recording and self._run_token is token:
            self._stop_recording()
        if run.sender_task and not run.sender_task.done():
            run.sender_task.cancel()
        if self._run_token is token:
            self._run_token = None
            self._run_task = None
            self._set_busy(False)
            if voice and run.continue_conversation:
                self._follow_up_pending = True
                self._maybe_start_follow_up()

    def _report_pipeline_error(self, data: dict):
        code = data.get('code', '')
        message = data.get('message') or ''
        logger.error(f"Assist pipeline error [{code}]: {message}")
        if code == 'stt-no-text-recognized':
            self.notice.emit(t("assist.notice.no_speech"))
        elif code == 'timeout':
            self.error.emit(t("assist.error.timeout"))
        else:
            self.error.emit(message or t("assist.error.generic"))

    def _handle_conversation_result(self, result: dict) -> bool:
        """Emit the reply; returns True if the agent expects an answer."""
        self.conversation_id = result.get('conversation_id') or self.conversation_id
        response = result.get('response', {}) or {}
        is_error = response.get('response_type') == 'error'
        speech = _speech_from_response(response)
        if not speech:
            speech = t("assist.reply.error") if is_error else t("assist.reply.done")
        self.reply_ready.emit(speech, is_error)
        return bool(result.get('continue_conversation')) and not is_error

    def _maybe_start_follow_up(self):
        """Re-open the mic for an agent that asked a question — once its
        spoken reply (if any) has finished, so it doesn't hear itself."""
        if not self._follow_up_pending or self._busy or self._recording:
            return
        if self._speaking or (self._tts_task is not None and not self._tts_task.done()):
            return  # resumed from _on_tts_finished
        self._follow_up_pending = False
        QTimer.singleShot(0, self.start_voice)

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
        except Exception:
            logger.exception("Failed to stream Assist audio")

    async def _play_tts(self, url: str):
        """Fetch the TTS reply and play it back.

        Routed through HAClient.fetch_media so the HA bearer token is only ever
        sent to the configured HA origin; a pipeline that returns an absolute
        third-party audio URL is fetched without credentials.
        """
        try:
            audio_bytes = await self._ha_client.fetch_media(url, kind="TTS audio")
        finally:
            if self._tts_task is asyncio.current_task():
                self._tts_task = None
        if not audio_bytes:
            self._maybe_start_follow_up()
            return

        # A previous reply's player/buffer would otherwise just get overwritten below —
        # still playing, still holding its FFmpeg decoder pipeline — leaking a bit more
        # of both on every voice turn.
        self._stop_tts_playback()

        self._tts_buffer = QBuffer()
        self._tts_buffer.setData(audio_bytes)
        self._tts_buffer.open(QIODeviceBase.OpenModeFlag.ReadOnly)

        speaker = _find_audio_device(QMediaDevices.audioOutputs(), self._assist_config().get('speaker_device_id', ''))

        self._media_player = QMediaPlayer()
        self._audio_output = QAudioOutput(speaker) if speaker else QAudioOutput()
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setSourceDevice(self._tts_buffer)
        self._media_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._media_player.errorOccurred.connect(self._on_media_error)
        self._set_speaking(True)
        self._media_player.play()

    def _on_media_status_changed(self, status):
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            self._on_tts_finished()

    def _on_media_error(self, *_args):
        self._on_tts_finished()

    def _on_tts_finished(self):
        if not self._speaking:
            return
        self._set_speaking(False)
        self._maybe_start_follow_up()

    def _stop_tts(self):
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        self._tts_task = None
        self._stop_tts_playback()

    def _stop_tts_playback(self):
        if self._media_player is not None:
            try:
                self._media_player.mediaStatusChanged.disconnect(self._on_media_status_changed)
                self._media_player.errorOccurred.disconnect(self._on_media_error)
            except (TypeError, RuntimeError):
                pass
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
        self._set_speaking(False)
