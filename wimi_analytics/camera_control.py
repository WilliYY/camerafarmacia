import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


TUYA_ENDPOINTS = {
    "america": "https://openapi.tuyaus.com",
    "europe": "https://openapi.tuyaeu.com",
    "china": "https://openapi.tuyacn.com",
    "india": "https://openapi.tuyain.com",
}
TUYA_PTZ_DIRECTIONS = frozenset({
    "UP",
    "RIGHT_UP",
    "RIGHT",
    "RIGHT_DOWN",
    "DOWN",
    "LEFT_DOWN",
    "LEFT",
    "LEFT_UP",
    "STOP",
})
_TUYA_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_STREAM_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_RESPONSE_BYTES = 1024 * 1024
_LIVE_AUDIO_SAMPLE_RATE = 16000
_LIVE_AUDIO_CHUNK_BYTES = 6400


class _WaveFormatEx(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_ushort),
        ("nChannels", ctypes.c_ushort),
        ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_ushort),
        ("wBitsPerSample", ctypes.c_ushort),
        ("cbSize", ctypes.c_ushort),
    ]


class _WaveHeader(ctypes.Structure):
    pass


_WaveHeader._fields_ = [
    ("lpData", ctypes.c_void_p),
    ("dwBufferLength", ctypes.c_uint32),
    ("dwBytesRecorded", ctypes.c_uint32),
    ("dwUser", ctypes.c_size_t),
    ("dwFlags", ctypes.c_uint32),
    ("dwLoops", ctypes.c_uint32),
    ("lpNext", ctypes.POINTER(_WaveHeader)),
    ("reserved", ctypes.c_size_t),
]


def build_live_audio_ffmpeg_command(ffmpeg_path, stream_name):
    """Build a credential-free, audio-only decoder for the local go2rtc route."""
    stream_name = str(stream_name or "")
    if not _STREAM_NAME_PATTERN.fullmatch(stream_name):
        raise ValueError("nome de stream invalido para audio ao vivo")
    if not isinstance(ffmpeg_path, str) or not ffmpeg_path.strip():
        raise ValueError("caminho do ffmpeg invalido")
    stream_url = f"http://127.0.0.1:1984/api/stream.ts?src={stream_name}"
    return [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-loglevel", "error",
        "-fflags", "nobuffer",
        "-probesize", "262144",
        "-analyzeduration", "1000000",
        "-rw_timeout", "5000000",
        "-i", stream_url,
        "-map", "0:a:0",
        "-vn",
        "-c:a", "pcm_s16le",
        "-ac", "1",
        "-ar", str(_LIVE_AUDIO_SAMPLE_RATE),
        "-f", "s16le",
        "pipe:1",
    ]


class WindowsWaveOutSink:
    """Small bounded PCM queue backed by the native Windows waveOut API."""

    _WHDR_DONE = 0x00000001
    _WAVE_FORMAT_PCM = 1
    _WAVE_MAPPER = 0xFFFFFFFF
    _MAX_PENDING_BUFFERS = 4

    def __init__(self):
        if os.name != "nt" or not hasattr(ctypes, "windll"):
            raise OSError("saida de audio ao vivo requer Windows")
        self._winmm = ctypes.windll.winmm
        self._configure_api()
        self._handle = ctypes.c_void_p()
        self._pending = []
        bits = 16
        channels = 1
        block_align = channels * bits // 8
        wave_format = _WaveFormatEx(
            self._WAVE_FORMAT_PCM,
            channels,
            _LIVE_AUDIO_SAMPLE_RATE,
            _LIVE_AUDIO_SAMPLE_RATE * block_align,
            block_align,
            bits,
            0,
        )
        result = self._winmm.waveOutOpen(
            ctypes.byref(self._handle),
            self._WAVE_MAPPER,
            ctypes.byref(wave_format),
            None,
            None,
            0,
        )
        if result != 0:
            raise OSError(f"Windows recusou a saida de audio ({result})")

    def _configure_api(self):
        header_pointer = ctypes.POINTER(_WaveHeader)
        self._winmm.waveOutOpen.argtypes = [
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32,
            ctypes.POINTER(_WaveFormatEx), ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_uint32,
        ]
        self._winmm.waveOutOpen.restype = ctypes.c_uint32
        for name in ("waveOutPrepareHeader", "waveOutWrite", "waveOutUnprepareHeader"):
            function = getattr(self._winmm, name)
            function.argtypes = [ctypes.c_void_p, header_pointer, ctypes.c_uint32]
            function.restype = ctypes.c_uint32
        self._winmm.waveOutReset.argtypes = [ctypes.c_void_p]
        self._winmm.waveOutReset.restype = ctypes.c_uint32
        self._winmm.waveOutClose.argtypes = [ctypes.c_void_p]
        self._winmm.waveOutClose.restype = ctypes.c_uint32

    def _release_completed(self):
        while self._pending and self._pending[0][1].dwFlags & self._WHDR_DONE:
            _buffer, header = self._pending.pop(0)
            self._winmm.waveOutUnprepareHeader(
                self._handle, ctypes.byref(header), ctypes.sizeof(header)
            )

    def write(self, data, stop_event):
        if not data or not self._handle:
            return False
        self._release_completed()
        while len(self._pending) >= self._MAX_PENDING_BUFFERS:
            if stop_event.wait(0.01):
                return False
            self._release_completed()

        buffer = ctypes.create_string_buffer(data, len(data))
        header = _WaveHeader()
        header.lpData = ctypes.cast(buffer, ctypes.c_void_p).value
        header.dwBufferLength = len(data)
        header.dwFlags = 0
        header.dwLoops = 0
        size = ctypes.sizeof(header)
        result = self._winmm.waveOutPrepareHeader(
            self._handle, ctypes.byref(header), size
        )
        if result != 0:
            raise OSError(f"falha ao preparar audio do Windows ({result})")
        result = self._winmm.waveOutWrite(self._handle, ctypes.byref(header), size)
        if result != 0:
            self._winmm.waveOutUnprepareHeader(
                self._handle, ctypes.byref(header), size
            )
            raise OSError(f"falha ao reproduzir audio no Windows ({result})")
        self._pending.append((buffer, header))
        return True

    def close(self):
        if not self._handle:
            return
        self._winmm.waveOutReset(self._handle)
        for _buffer, header in self._pending:
            self._winmm.waveOutUnprepareHeader(
                self._handle, ctypes.byref(header), ctypes.sizeof(header)
            )
        self._pending.clear()
        self._winmm.waveOutClose(self._handle)
        self._handle = ctypes.c_void_p()


class LiveAudioPlayer:
    """Decode one local camera audio track without touching its recorder."""

    def __init__(
        self,
        ffmpeg_path,
        callback=None,
        process_factory=None,
        sink_factory=None,
    ):
        self.ffmpeg_path = ffmpeg_path
        self._callback = callback
        self._process_factory = process_factory or subprocess.Popen
        self._sink_factory = sink_factory or WindowsWaveOutSink
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._process = None
        self._state = None
        self._detail = None

    def _notify(self, state, detail=""):
        detail = re.sub(r"[\r\n]+", " ", str(detail or ""))[:160]
        with self._lock:
            if (state, detail) == (self._state, self._detail):
                return
            self._state, self._detail = state, detail
        if self._callback:
            try:
                self._callback(state, detail)
            except Exception:
                pass

    def is_running(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, stream_name):
        if not os.path.isfile(self.ffmpeg_path):
            self._notify("error", "ffmpeg local nao encontrado")
            return False
        try:
            command = build_live_audio_ffmpeg_command(self.ffmpeg_path, stream_name)
        except ValueError as error:
            self._notify("error", error)
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(command, stream_name),
                daemon=True,
                name=f"live-audio-{stream_name}",
            )
            self._thread.start()
        return True

    def _terminate_owned_process(self):
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=1.0)
            except Exception:
                pass

    def _run(self, command, stream_name):
        retry_delay = 1.0
        try:
            while not self._stop_event.is_set():
                process = None
                sink = None
                received_audio = False
                cycle_failed = False
                try:
                    self._notify("connecting")
                    process = self._process_factory(
                        command,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    with self._lock:
                        self._process = process
                    if process.stdout is None:
                        raise OSError("ffmpeg nao abriu o canal de audio")
                    while not self._stop_event.is_set():
                        chunk = process.stdout.read(_LIVE_AUDIO_CHUNK_BYTES)
                        if not chunk:
                            break
                        received_audio = True
                        retry_delay = 1.0
                        if sink is None:
                            sink = self._sink_factory()
                        if not sink.write(chunk, self._stop_event):
                            break
                        if self._state != "playing":
                            self._notify("playing")
                except Exception as error:
                    if not self._stop_event.is_set():
                        cycle_failed = True
                        self._notify("error", error)
                finally:
                    self._terminate_owned_process()
                    if sink is not None:
                        try:
                            sink.close()
                        except Exception:
                            pass
                    with self._lock:
                        if self._process is process:
                            self._process = None

                if self._stop_event.is_set():
                    break
                if cycle_failed:
                    retry_delay = min(retry_delay * 2.0, 15.0)
                elif received_audio:
                    self._notify("reconnecting", "audio interrompido")
                else:
                    self._notify("unavailable", "microfone nao recebido da camera")
                    retry_delay = min(retry_delay * 2.0, 15.0)
                self._stop_event.wait(retry_delay)
        finally:
            with self._lock:
                self._process = None
                self._thread = None
            self._notify("stopped")

    def stop(self, timeout=0.5):
        self._stop_event.set()
        self._terminate_owned_process()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread() and timeout > 0:
            thread.join(max(0.0, float(timeout)))
        return thread is None or not thread.is_alive()

    def close(self, timeout=3.0):
        return self.stop(timeout=timeout)


class TuyaCloudError(RuntimeError):
    pass


def _parse_media_description(media):
    if isinstance(media, str):
        parts = [part.strip() for part in media.split(",")]
        kind = parts[0].lower() if parts else ""
        direction = parts[1].lower() if len(parts) > 1 else ""
        codecs = parts[2:]
        return kind, direction, codecs
    if isinstance(media, dict):
        kind = str(media.get("kind") or media.get("type") or "").lower()
        direction = str(media.get("direction") or "").lower()
        codecs = media.get("codecs") or media.get("codec") or []
        if isinstance(codecs, str):
            codecs = [codecs]
        return kind, direction, list(codecs)
    return "", "", []


def get_go2rtc_stream_capabilities(streams_payload, stream_name):
    """Return capabilities offered by camera producers, not consumer wishes."""
    stream = streams_payload.get(stream_name, {}) if isinstance(streams_payload, dict) else {}
    producers = stream.get("producers") if isinstance(stream, dict) else []
    if not isinstance(producers, list):
        producers = []

    incoming_video = False
    incoming_audio = False
    talkback_audio = False
    audio_codecs = []
    media_active = False
    for producer in producers:
        medias = producer.get("medias", []) if isinstance(producer, dict) else []
        if not isinstance(medias, list):
            continue
        for media in medias:
            kind, direction, codecs = _parse_media_description(media)
            if not kind:
                continue
            media_active = True
            if kind == "video" and direction in {"recvonly", "sendrecv"}:
                incoming_video = True
            elif kind == "audio":
                if direction in {"recvonly", "sendrecv"}:
                    incoming_audio = True
                    audio_codecs.extend(str(codec) for codec in codecs)
                if direction in {"sendonly", "sendrecv"}:
                    talkback_audio = True

    return {
        "configured": bool(producers),
        "media_active": media_active,
        "incoming_video": incoming_video,
        "incoming_audio": incoming_audio,
        "talkback_audio": talkback_audio,
        "audio_codecs": tuple(dict.fromkeys(audio_codecs)),
    }


def extract_tuya_device_id(stream_url):
    try:
        parsed = urllib.parse.urlparse(stream_url)
        if parsed.scheme.lower() != "tuya":
            return None
        device_id = urllib.parse.parse_qs(parsed.query).get("device_id", [""])[0]
    except (TypeError, ValueError):
        return None
    return device_id if _TUYA_ID_PATTERN.fullmatch(device_id or "") else None


def infer_tuya_endpoint(stream_url):
    try:
        host = (urllib.parse.urlparse(stream_url).hostname or "").lower()
    except (TypeError, ValueError):
        host = ""
    if "-eu." in host:
        return TUYA_ENDPOINTS["europe"]
    if "-cn." in host:
        return TUYA_ENDPOINTS["china"]
    if "-in." in host:
        return TUYA_ENDPOINTS["india"]
    return TUYA_ENDPOINTS["america"]


def normalize_tuya_cloud_config(value):
    if not isinstance(value, dict):
        return {}
    access_id = str(value.get("access_id") or "").strip()
    endpoint = str(value.get("endpoint") or "").strip().rstrip("/")
    protected = str(value.get("secret_protected") or "").strip()
    if not _TUYA_ID_PATTERN.fullmatch(access_id):
        return {}
    if endpoint not in TUYA_ENDPOINTS.values():
        return {}
    try:
        raw = base64.b64decode(protected, validate=True)
    except (ValueError, TypeError):
        return {}
    if not 16 <= len(raw) <= 4096:
        return {}
    return {
        "access_id": access_id,
        "endpoint": endpoint,
        "secret_protected": protected,
    }


def protect_tuya_secret(secret, protector=None):
    if not isinstance(secret, str) or not 8 <= len(secret) <= 256:
        raise ValueError("Access Secret invalido")
    if any(char in secret for char in "\r\n\x00"):
        raise ValueError("Access Secret invalido")
    if protector is None:
        from wimi_analytics.privacy import protect_bytes
        protector = protect_bytes
    protected = protector(secret.encode("utf-8"))
    if not isinstance(protected, bytes) or not protected:
        raise ValueError("nao foi possivel proteger o Access Secret")
    return base64.b64encode(protected).decode("ascii")


def build_tuya_cloud_config(access_id, endpoint, secret="", existing=None, protector=None):
    access_id = str(access_id or "").strip()
    endpoint = str(endpoint or "").strip().rstrip("/")
    if not _TUYA_ID_PATTERN.fullmatch(access_id):
        raise ValueError("Access ID invalido")
    if endpoint not in TUYA_ENDPOINTS.values():
        raise ValueError("data center Tuya invalido")

    existing = normalize_tuya_cloud_config(existing)
    protected = protect_tuya_secret(secret, protector) if secret else existing.get("secret_protected")
    if not protected:
        raise ValueError("informe o Access Secret")
    return normalize_tuya_cloud_config({
        "access_id": access_id,
        "endpoint": endpoint,
        "secret_protected": protected,
    })


def load_tuya_cloud_credentials(value, unprotector=None):
    config = normalize_tuya_cloud_config(value)
    if not config:
        raise TuyaCloudError("controle Tuya Cloud nao configurado")
    if unprotector is None:
        from wimi_analytics.privacy import unprotect_bytes
        unprotector = unprotect_bytes
    try:
        protected = base64.b64decode(config["secret_protected"], validate=True)
        secret = unprotector(protected).decode("utf-8")
    except Exception as error:
        raise TuyaCloudError("Access Secret protegido nao pode ser aberto neste Windows") from error
    if not 8 <= len(secret) <= 256:
        raise TuyaCloudError("Access Secret protegido invalido")
    return config["access_id"], secret, config["endpoint"]


def build_tuya_signature(method, path, body, access_id, secret, timestamp_ms, nonce, access_token=""):
    if isinstance(body, str):
        body = body.encode("utf-8")
    body_hash = hashlib.sha256(body or b"").hexdigest()
    string_to_sign = f"{method.upper()}\n{body_hash}\n\n{path}"
    source = f"{access_id}{access_token}{timestamp_ms}{nonce}{string_to_sign}"
    return hmac.new(secret.encode("utf-8"), source.encode("utf-8"), hashlib.sha256).hexdigest().upper()


class TuyaCloudClient:
    def __init__(
        self,
        access_id,
        secret,
        endpoint,
        opener=None,
        clock=None,
        nonce_factory=None,
        timeout=5.0,
    ):
        if endpoint not in TUYA_ENDPOINTS.values():
            raise ValueError("data center Tuya invalido")
        self.access_id = access_id
        self._secret = secret
        self.endpoint = endpoint
        self._opener = opener or urllib.request.urlopen
        self._clock = clock or time.time
        self._nonce_factory = nonce_factory or (lambda: uuid.uuid4().hex)
        self.timeout = max(2.0, min(float(timeout), 10.0))
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def _request(self, method, path, body_obj=None, access_token=""):
        if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "\r\n"):
            raise ValueError("caminho Tuya invalido")
        body = b""
        if body_obj is not None:
            body = json.dumps(body_obj, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        timestamp_ms = str(int(self._clock() * 1000))
        nonce = str(self._nonce_factory())
        signature = build_tuya_signature(
            method, path, body, self.access_id, self._secret, timestamp_ms, nonce, access_token
        )
        headers = {
            "client_id": self.access_id,
            "sign": signature,
            "sign_method": "HMAC-SHA256",
            "t": timestamp_ms,
            "nonce": nonce,
            "Content-Type": "application/json",
            "User-Agent": "NVR-Camera-Farmacia/4.13",
        }
        if access_token:
            headers["access_token"] = access_token
        request = urllib.request.Request(
            self.endpoint + path,
            data=body if body_obj is not None else None,
            headers=headers,
            method=method.upper(),
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            with response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise TuyaCloudError(f"Tuya Cloud respondeu HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise TuyaCloudError("Tuya Cloud indisponivel ou sem conexao") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise TuyaCloudError("resposta Tuya excedeu o limite seguro")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TuyaCloudError("resposta Tuya invalida") from error
        if not isinstance(payload, dict) or payload.get("success") is not True:
            code = str(payload.get("code") or "erro_desconhecido") if isinstance(payload, dict) else "erro_desconhecido"
            message = str(payload.get("msg") or "comando recusado") if isinstance(payload, dict) else "comando recusado"
            message = re.sub(r"[^A-Za-z0-9 ._:-]", "", message)[:120]
            raise TuyaCloudError(f"Tuya recusou o comando ({code}): {message}")
        return payload

    def _get_access_token(self):
        with self._token_lock:
            now = self._clock()
            if self._token and now < self._token_expires_at:
                return self._token
            payload = self._request("GET", "/v1.0/token?grant_type=1")
            result = payload.get("result")
            token = result.get("access_token") if isinstance(result, dict) else None
            try:
                expires_in = int(result.get("expire_time", 3600))
            except (AttributeError, TypeError, ValueError):
                expires_in = 3600
            if not isinstance(token, str) or not 8 <= len(token) <= 512:
                raise TuyaCloudError("Tuya nao retornou um token valido")
            self._token = token
            self._token_expires_at = now + max(60, expires_in - 60)
            return token

    def move(self, device_id, direction):
        if not _TUYA_ID_PATTERN.fullmatch(device_id or ""):
            raise ValueError("device_id Tuya invalido")
        direction = str(direction or "").upper()
        if direction not in TUYA_PTZ_DIRECTIONS:
            raise ValueError("direcao PTZ invalida")
        token = self._get_access_token()
        path = f"/v1.0/cameras/{device_id}/configs/ptz"
        return self._request("POST", path, {"value": direction}, token)


class TuyaPtzPulseController:
    """Serialize a short camera move and always issue STOP in the same worker."""

    def __init__(self, sender, duration=0.45, callback=None):
        self._sender = sender
        self._duration = max(0.15, min(float(duration), 1.0))
        self._callback = callback
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    def pulse(self, direction):
        direction = str(direction or "").upper()
        if direction not in TUYA_PTZ_DIRECTIONS or direction == "STOP":
            raise ValueError("direcao PTZ invalida")
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, args=(direction,), daemon=True)
            self._thread.start()
            return True

    def _notify(self, state, detail=""):
        if self._callback:
            try:
                self._callback(state, detail)
            except Exception:
                pass

    def _run(self, direction):
        error = None
        self._notify("moving", direction)
        try:
            self._sender(direction)
            self._stop_event.wait(self._duration)
        except Exception as caught:
            error = caught
        finally:
            try:
                self._sender("STOP")
            except Exception as stop_error:
                if error is None:
                    error = stop_error
            self._notify("error" if error else "idle", str(error or ""))
            with self._state_lock:
                self._thread = None

    def request_stop(self):
        self._stop_event.set()

    def close(self, timeout=6.0):
        self.request_stop()
        with self._state_lock:
            thread = self._thread
        if thread is not None:
            thread.join(max(0.0, float(timeout)))
        return thread is None or not thread.is_alive()
