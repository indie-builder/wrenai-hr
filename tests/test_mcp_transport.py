"""HTTP authentication, private routes, rebinding and request-size protection."""
import json
import os
from unittest.mock import patch

import anyio

from fixtures import AUTH, HEADERS, TOKEN, ServerCase, initialize, rpc
from hr_mcp.contracts import MAX_BODY_BYTES, SNAPSHOT_DATE

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT")


class TransportTests(ServerCase):
    def test_health_is_public_minimal_and_private_routes_are_404(self):
        with self.client() as client:
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(set(health.json()), {"status", "version", "snapshot"})
            self.assertEqual(health.json()["snapshot"], SNAPSHOT_DATE)
            for path in ("/", "/docs", "/redoc", "/openapi.json", "/data/public.duckdb", "/mdl.json", "/mcp/", "/mcp/data"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path, headers=AUTH).status_code, 404)
            self.assertEqual(self.engine.calls, [])

    def test_authentication_precedes_methods_and_missing_configuration_fails_closed(self):
        with self.client() as client:
            for method in METHODS:
                for authorization in (None, "Bearer incorrect", "Basic abc", "Bearer", f"Bearer {TOKEN} extra"):
                    with self.subTest(method=method, authorization=authorization):
                        response = client.request(method, "/mcp", headers={} if authorization is None else
                                                  {"Authorization": authorization}, content=b"not-json")
                        self.assertEqual(response.status_code, 401)
                        self.assertEqual(response.headers["www-authenticate"], "Bearer")
                        self.assertNotIn(TOKEN, response.text)
                if method != "POST":
                    response = client.request(method, "/mcp", headers=HEADERS)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response.headers["allow"], "POST")
            self.assertEqual(self.engine.calls, [])
            self.post(client, headers={**HEADERS, "Authorization": f"bEaReR {TOKEN}"}, request=rpc("ping"))
        for token in (None, "", "a" * 31, " " * 32, "密" * 32):
            with self.subTest(token=token), self.client(token=token) as client:
                health = client.get("/health")
                self.assertEqual(health.status_code, 503)
                self.assertEqual(health.json()["status"], "not_ready")
                for method in ("POST", "GET", "DELETE", "OPTIONS"):
                    self.assertEqual(client.request(method, "/mcp", headers=AUTH).status_code, 503)
        with patch.dict(os.environ, {"MCP_AUTH_TOKEN": TOKEN}), self.client(token=None) as client:
            self.post(client)

    def test_host_origin_and_duplicate_headers_fail_closed(self):
        with self.client(allowed_origins=["https://client.example"]) as client:
            for name, values, status in (
                ("Origin", ("https://evil.example", "null", "https://client.example.evil", "https://client.example/path", ""), 403),
                ("Host", ("evil.example", "testserver.evil", "testserver:abc", "testserver:99999", "testserver@evil.example"), 421),
            ):
                for value in values:
                    with self.subTest(header=name, value=value):
                        self.post(client, status=status, headers={**HEADERS, name: value})
            for name, value, status in (("Authorization", f"Bearer {TOKEN}", 401), ("Host", "testserver", 421),
                                        ("Origin", "https://client.example", 403)):
                self.post(client, status=status, headers=[(key, val) for key, val in HEADERS.items()
                          if key.lower() != name.lower()] + [(name, value)] * 2)
            headers = {**HEADERS, "Origin": "https://client.example", "Host": "TESTSERVER"}
            self.assertNotIn("access-control-allow-origin", self.post(client, headers=headers).headers)
            self.assertEqual(client.options("/mcp", headers=headers).status_code, 405)

    def test_environment_allowlists_vercel_and_local_ports(self):
        env = {"VERCEL_URL": "hr-preview-123.vercel.app", "VERCEL_PROJECT_PRODUCTION_URL": "hr-demo.vercel.app",
               "MCP_ALLOWED_HOSTS": " hr.example, custom.example:8443,*.vercel.app ",
               "MCP_ALLOWED_ORIGINS": " https://client.example, https://custom.example:8443 "}
        with patch.dict(os.environ, env), self.client(allowed_hosts=None) as client:
            for host, origin in (("hr-preview-123.vercel.app", "https://hr-preview-123.vercel.app"),
                                 ("hr-demo.vercel.app", "https://hr-demo.vercel.app"), ("hr.example", "https://client.example"),
                                 ("custom.example:8443", "https://custom.example:8443"), ("localhost:8317", "http://localhost:8317"),
                                 ("127.0.0.1:53123", "http://127.0.0.1:53123"), ("[::1]:8000", "http://[::1]:8000")):
                with self.subTest(host=host):
                    self.post(client, headers={**HEADERS, "Host": host, "Origin": origin})
            self.post(client, status=421, headers={**HEADERS, "Host": "unrelated.vercel.app"})
            self.post(client, status=403, headers={**HEADERS, "Host": "hr-demo.vercel.app", "Origin": "https://unrelated.vercel.app"})

    def test_body_limit_at_boundary_and_authentication_before_read(self):
        with self.client() as client:
            payload = b"x" * (MAX_BODY_BYTES + 1)
            for headers, content, status in ((HEADERS, payload, 413), ({}, payload, 401),
                                             ({**HEADERS, "Content-Length": "1"}, payload, 413),
                                             (HEADERS, iter([b"x" * 32768, b"x" * 32769]), 413)):
                self.post(client, status=status, headers=headers, content=content)
            valid = json.dumps(initialize()).encode()
            self.post(client, headers={**HEADERS, "Content-Type": "application/json"},
                      content=valid + b" " * (MAX_BODY_BYTES - len(valid)))
        async def exercise():
            for headers, chunks, status, expected_reads in (([], [b"unread"], 401, 0),
                    ([(b"authorization", f"Bearer {TOKEN}".encode())], [b"x" * 32768, b"x" * 32769], 413, 2),
                    ([(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-length", b"1")], [payload], 413, 1)):
                reads, messages = [], []
                async def receive():
                    self.assertLess(len(reads), len(chunks), "Read beyond rejected body")
                    reads.append(chunks[len(reads)])
                    return {"type": "http.request", "body": reads[-1], "more_body": len(reads) < len(chunks)}
                async def send(message):
                    messages.append(message)
                await self.client().app({"type": "http", "method": "POST", "path": "/mcp",
                                        "headers": [(b"host", b"testserver"), *headers]}, receive, send)
                self.assertEqual(messages[0]["status"], status)
                self.assertEqual(len(reads), expected_reads)
        anyio.run(exercise)
