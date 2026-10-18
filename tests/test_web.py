import json
import threading
import unittest
import urllib.error
import urllib.request

from gpu_fan_controller.web import FanHttpServer


class FakeController:
    def __init__(self):
        self.calls = []
        self.curve_calls = []
        self.reset_calls = 0

    def snapshot(self):
        return {
            "timestamp": 1700000000.0,
            "dry_run": True,
            "control_enabled": True,
            "errors": {},
            "limits": {"min_manual_duty": 20},
            "gpus": [],
        }

    def set_control(self, gpu_id, mode, duty_percent=None):
        if gpu_id == 99:
            raise KeyError("未知 GPU：99")
        if mode not in {"auto", "manual"}:
            raise ValueError("mode 必须是 auto 或 manual")
        self.calls.append((gpu_id, mode, duty_percent))

    def set_auto_curve(self, curve):
        self.curve_calls.append(curve)

    def reset_auto_curve(self):
        self.reset_calls += 1


class WebTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.server = FanHttpServer(("127.0.0.1", 0), self.controller)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.addCleanup(self._stop)

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, *, data=None, headers=None, method=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def post(self, path, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return self.request(path, data=body, headers={"Content-Type": content_type}, method="POST")

    def test_state(self):
        status, headers, body = self.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertTrue(json.loads(body)["dry_run"])
        self.assertIn("Content-Security-Policy", headers)

    def test_health(self):
        status, _, body = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})

    def test_index_and_assets(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("风扇控制器", body.decode("utf-8"))
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        status, _, _ = self.request("/app.js")
        self.assertEqual(status, 200)
        status, _, _ = self.request("/styles.css")
        self.assertEqual(status, 200)

    def test_unknown_get(self):
        status, _, body = self.request("/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    def test_control_post(self):
        status, _, body = self.post("/api/gpus/0/control", {"mode": "manual", "duty_percent": 55})
        self.assertEqual(status, 200)
        self.assertEqual(self.controller.calls, [(0, "manual", 55)])
        self.assertEqual(json.loads(body)["control_enabled"], True)

    def test_control_wrong_content_type(self):
        status, _, body = self.post(
            "/api/gpus/0/control", {"mode": "manual"}, content_type="text/plain"
        )
        self.assertEqual(status, 415)
        self.assertEqual(self.controller.calls, [])
        self.assertIn("application/json", json.loads(body)["error"])

    def test_control_invalid_json(self):
        status, _, _ = self.post("/api/gpus/0/control", b"{not json")
        self.assertEqual(status, 400)

    def test_control_invalid_mode(self):
        status, _, body = self.post("/api/gpus/0/control", {"mode": "bogus"})
        self.assertEqual(status, 400)
        self.assertIn("auto", json.loads(body)["error"])

    def test_control_unknown_gpu(self):
        status, _, _ = self.post("/api/gpus/99/control", {"mode": "auto"})
        self.assertEqual(status, 404)

    def test_control_unknown_path(self):
        status, _, _ = self.post("/api/gpus/0/nope", {"mode": "auto"})
        self.assertEqual(status, 404)

    def test_control_non_object_payload(self):
        status, _, _ = self.post("/api/gpus/0/control", [1, 2, 3])
        self.assertEqual(status, 400)

    def test_missing_content_length(self):
        status, _, _ = self.request(
            "/api/gpus/0/control",
            data=None,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        self.assertEqual(status, 400)

    def put(self, path, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return self.request(path, data=body, headers={"Content-Type": content_type}, method="PUT")

    def test_curve_put(self):
        curve = [[30, 40], [60, 55], [80, 90]]
        status, _, body = self.put("/api/curve", {"curve": curve})
        self.assertEqual(status, 200)
        self.assertEqual(self.controller.curve_calls, [curve])
        self.assertEqual(json.loads(body)["control_enabled"], True)

    def test_curve_put_invalid_payload(self):
        status, _, _ = self.put("/api/curve", {"curve": "not-a-list"})
        self.assertEqual(status, 400)
        self.assertEqual(self.controller.curve_calls, [])

    def test_curve_put_wrong_content_type(self):
        status, _, _ = self.put("/api/curve", {"curve": [[30, 40], [60, 55]]},
                                content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(self.controller.curve_calls, [])

    def test_curve_put_unknown_path(self):
        status, _, _ = self.put("/api/other", {"curve": [[30, 40], [60, 55]]})
        self.assertEqual(status, 404)

    def test_curve_reset(self):
        status, _, body = self.request("/api/curve/reset", data=b"", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(self.controller.reset_calls, 1)
        self.assertEqual(json.loads(body)["control_enabled"], True)


if __name__ == "__main__":
    unittest.main()
