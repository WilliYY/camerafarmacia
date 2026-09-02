import hashlib
import hmac
import json
import tempfile
import threading
import unittest

from wimi_analytics.camera_control import (
    TUYA_ENDPOINTS,
    TuyaCloudClient,
    TuyaPtzPulseController,
    LiveAudioPlayer,
    build_live_audio_ffmpeg_command,
    build_tuya_cloud_config,
    build_tuya_signature,
    extract_tuya_device_id,
    get_go2rtc_stream_capabilities,
    infer_tuya_endpoint,
    load_tuya_cloud_credentials,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


class CameraControlTests(unittest.TestCase):
    def test_live_audio_command_decodes_only_local_incoming_audio(self):
        command = build_live_audio_ffmpeg_command(
            r"C:\NVR\ffmpeg.exe",
            "farmacia2",
        )

        self.assertIn("http://127.0.0.1:1984/api/stream.ts?src=farmacia2", command)
        self.assertIn("-vn", command)
        self.assertIn("pcm_s16le", command)
        self.assertIn("pipe:1", command)
        self.assertNotIn("password", " ".join(command).lower())

    def test_live_audio_command_rejects_untrusted_stream_name(self):
        with self.assertRaisesRegex(ValueError, "stream"):
            build_live_audio_ffmpeg_command("ffmpeg.exe", "../camera")

    def test_live_audio_player_stops_only_its_owned_process(self):
        played = threading.Event()
        process_created = threading.Event()

        class FakeStdout:
            def __init__(self):
                self._first = True

            def read(self, _size):
                if self._first:
                    self._first = False
                    return b"\x00" * 3200
                played.wait(1.0)
                return b""

        class FakeProcess:
            def __init__(self):
                self.stdout = FakeStdout()
                self.terminated = False

            def poll(self):
                return None if not self.terminated else 0

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.terminated = True

        class FakeSink:
            def write(self, data, _stop_event):
                self.data = data
                played.set()
                return True

            def close(self):
                pass

        processes = []

        def process_factory(_command, **_kwargs):
            process = FakeProcess()
            processes.append(process)
            process_created.set()
            return process

        with tempfile.NamedTemporaryFile(suffix=".exe") as ffmpeg:
            player = LiveAudioPlayer(
                ffmpeg.name,
                process_factory=process_factory,
                sink_factory=lambda: FakeSink(),
            )
            self.assertTrue(player.start("farmacia"))
            self.assertFalse(player.start("farmacia"))
            self.assertTrue(process_created.wait(1.0))
            self.assertTrue(played.wait(1.0))
            self.assertTrue(player.close(timeout=2.0))

        self.assertEqual(len(processes), 1)
        self.assertTrue(processes[0].terminated)

    def test_live_audio_player_does_not_misreport_output_failure_as_missing_microphone(self):
        error_seen = threading.Event()
        states = []

        class FakeProcess:
            def __init__(self):
                self.stdout = self
                self.terminated = False
                self.sent = False

            def read(self, _size):
                if not self.sent:
                    self.sent = True
                    return b"\x00" * 3200
                return b""

            def poll(self):
                return None if not self.terminated else 0

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.terminated = True

        def callback(state, _detail):
            states.append(state)
            if state == "error":
                error_seen.set()

        with tempfile.NamedTemporaryFile(suffix=".exe") as ffmpeg:
            player = LiveAudioPlayer(
                ffmpeg.name,
                callback=callback,
                process_factory=lambda _command, **_kwargs: FakeProcess(),
                sink_factory=lambda: (_ for _ in ()).throw(OSError("sem saida")),
            )
            self.assertTrue(player.start("farmacia"))
            self.assertTrue(error_seen.wait(1.0))
            player.close(timeout=2.0)

        self.assertIn("error", states)
        self.assertNotIn("unavailable", states)

    def test_media_capabilities_distinguish_microphone_from_talkback(self):
        payload = {
            "camera": {
                "producers": [{
                    "medias": [
                        "video, recvonly, H265, H264",
                        "audio, sendonly, PCML/8000/1",
                    ]
                }]
            }
        }

        result = get_go2rtc_stream_capabilities(payload, "camera")

        self.assertTrue(result["incoming_video"])
        self.assertFalse(result["incoming_audio"])
        self.assertTrue(result["talkback_audio"])

    def test_media_capabilities_report_recordable_audio(self):
        payload = {
            "camera": {
                "producers": [{"medias": ["audio, recvonly, AAC/16000/1"]}]
            }
        }

        result = get_go2rtc_stream_capabilities(payload, "camera")

        self.assertTrue(result["incoming_audio"])
        self.assertEqual(result["audio_codecs"], ("AAC/16000/1",))

    def test_tuya_stream_yields_device_and_american_endpoint(self):
        url = "tuya://protect-us.ismartlife.me?device_id=abc123456&email=x&password=y"

        self.assertEqual(extract_tuya_device_id(url), "abc123456")
        self.assertEqual(infer_tuya_endpoint(url), TUYA_ENDPOINTS["america"])
        self.assertIsNone(extract_tuya_device_id("rtsp://camera/live"))

    def test_cloud_secret_is_stored_protected_and_can_be_loaded(self):
        protect = lambda value: b"protected:" + value[::-1]
        unprotect = lambda value: value.removeprefix(b"protected:")[::-1]

        config = build_tuya_cloud_config(
            "access123456",
            TUYA_ENDPOINTS["america"],
            "secret123456",
            protector=protect,
        )
        access_id, secret, endpoint = load_tuya_cloud_credentials(config, unprotect)

        self.assertNotIn("secret123456", json.dumps(config))
        self.assertEqual((access_id, secret, endpoint), (
            "access123456", "secret123456", TUYA_ENDPOINTS["america"]
        ))

    def test_cloud_config_rejects_custom_endpoint(self):
        with self.assertRaisesRegex(ValueError, "data center"):
            build_tuya_cloud_config(
                "access123456",
                "https://example.invalid",
                "secret123456",
                protector=lambda value: b"protected:" + value,
            )

    def test_signature_matches_documented_formula(self):
        path = "/v1.0/cameras/device123/configs/ptz"
        body = b'{"value":"LEFT"}'
        timestamp = "1700000000000"
        nonce = "nonce123"
        content_hash = hashlib.sha256(body).hexdigest()
        string_to_sign = f"POST\n{content_hash}\n\n{path}"
        source = f"access123token123{timestamp}{nonce}{string_to_sign}"
        expected = hmac.new(b"secret123", source.encode(), hashlib.sha256).hexdigest().upper()

        result = build_tuya_signature(
            "POST", path, body, "access123", "secret123", timestamp, nonce, "token123"
        )

        self.assertEqual(result, expected)

    def test_client_gets_token_then_sends_ptz_command(self):
        requests = []
        responses = [
            {"success": True, "result": {"access_token": "token-123456", "expire_time": 3600}},
            {"success": True, "result": {"sn": "command-1"}},
        ]

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(responses.pop(0))

        client = TuyaCloudClient(
            "access123456",
            "secret123456",
            TUYA_ENDPOINTS["america"],
            opener=opener,
            clock=lambda: 1700000000.0,
            nonce_factory=lambda: "nonce123",
        )

        client.move("device123456", "RIGHT")

        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0][0].full_url.endswith("/v1.0/token?grant_type=1"))
        self.assertTrue(requests[1][0].full_url.endswith("/v1.0/cameras/device123456/configs/ptz"))
        self.assertEqual(json.loads(requests[1][0].data), {"value": "RIGHT"})
        self.assertEqual(requests[1][0].headers["Access_token"], "token-123456")

    def test_client_reuses_unexpired_access_token(self):
        requests = []
        responses = [
            {"success": True, "result": {"access_token": "token-123456", "expire_time": 3600}},
            {"success": True, "result": {"sn": "command-1"}},
            {"success": True, "result": {"sn": "command-2"}},
        ]

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(responses.pop(0))

        client = TuyaCloudClient(
            "access123456",
            "secret123456",
            TUYA_ENDPOINTS["america"],
            opener=opener,
            clock=lambda: 1700000000.0,
            nonce_factory=lambda: "nonce123",
        )

        client.move("device123456", "LEFT")
        client.move("device123456", "STOP")

        token_requests = [request for request, _timeout in requests if "/token?" in request.full_url]
        self.assertEqual(len(token_requests), 1)

    def test_ptz_pulse_always_sends_stop(self):
        commands = []
        finished = threading.Event()

        def sender(direction):
            commands.append(direction)

        controller = TuyaPtzPulseController(
            sender,
            duration=0.15,
            callback=lambda state, _detail: finished.set() if state == "idle" else None,
        )

        self.assertTrue(controller.pulse("LEFT"))
        self.assertFalse(controller.pulse("RIGHT"))
        self.assertTrue(finished.wait(2.0))
        self.assertTrue(controller.close())
        self.assertEqual(commands, ["LEFT", "STOP"])

    def test_ptz_pulse_stops_even_when_start_fails(self):
        commands = []
        finished = threading.Event()

        def sender(direction):
            commands.append(direction)
            if direction == "UP":
                raise RuntimeError("start failed")

        controller = TuyaPtzPulseController(
            sender,
            callback=lambda state, _detail: finished.set() if state == "error" else None,
        )

        self.assertTrue(controller.pulse("UP"))
        self.assertTrue(finished.wait(2.0))
        self.assertEqual(commands, ["UP", "STOP"])


if __name__ == "__main__":
    unittest.main()
