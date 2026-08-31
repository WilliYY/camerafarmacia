import base64
import hashlib
import hmac
import json
import re
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
_MAX_RESPONSE_BYTES = 1024 * 1024


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
