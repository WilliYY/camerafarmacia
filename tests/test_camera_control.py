import hashlib
import hmac
import json
import threading
import unittest

from wimi_analytics.camera_control import (
    TUYA_ENDPOINTS,
    TuyaCloudClient,
    TuyaPtzPulseController,
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
