"""Remote end-to-end test: real sync service, push, fresh client, pull, blame/context.

Requires a running `agent-trace-service` (see ``tests/docker-compose.test.yml``).

Set ``AGENT_TRACE_E2E_SERVICE_URL`` to the service base URL (no trailing slash),
e.g. ``http://127.0.0.1:8765``. If unset, tests are skipped so default CI/local
pytest runs stay offline.

Flow (per M0 plan Step 1.6): ``init`` → synthetic hook events → ``commit-link``
(via git hook) → ``push`` → wipe local state → ``pull`` on a copied repo with a
fresh ``AGENT_TRACE_HOME`` → ``blame`` / ``context`` JSON matches the original.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.test_e2e import E2EContext, _CLIError


def _e2e_service_url() -> str:
    return os.environ.get("AGENT_TRACE_E2E_SERVICE_URL", "").strip().rstrip("/")


def _skip_remote_e2e() -> bool:
    return not _e2e_service_url()


_ADMIN_SECRET_ENV = "AGENT_TRACE_E2E_ADMIN_SECRET"


def _http_json(
    method: str,
    url: str,
    body: dict | None = None,
    *,
    timeout: int = 30,
    headers: dict | None = None,
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode() if e.fp else ""
        raise AssertionError(f"{method} {url} failed: HTTP {e.code}\n{raw}") from e


def _admin_secret() -> str:
    return os.environ.get(_ADMIN_SECRET_ENV, "e2e-docker-compose-admin-secret")


def _fetch_bearer_token(base_url: str) -> str:
    """Mint a fresh bearer token via the admin API.

    The compose stack sets ``ADMIN_SECRET=e2e-docker-compose-admin-secret``;
    callers can override it with ``AGENT_TRACE_E2E_ADMIN_SECRET`` for ad-hoc
    runs against a non-compose service.
    """
    out = _http_json(
        "POST",
        f"{base_url}/api/v1/tokens",
        {"name": "e2e-remote-test"},
        headers={"X-Admin-Secret": _admin_secret()},
    )
    token = out.get("token")
    if not isinstance(token, str) or not token:
        raise AssertionError(f"token missing in response: {out!r}")
    return token


def _register_project(base_url: str, slug: str) -> None:
    """Create a project via the admin path so the slug-bearing URL works."""
    _http_json(
        "POST",
        f"{base_url}/api/v1/projects",
        {"project_id": slug, "name": "e2e-remote-test"},
        headers={"X-Admin-Secret": _admin_secret()},
    )


def _cli_json_env(
    env: dict[str, str],
    cwd: Path,
    *args: str,
) -> object:
    r = subprocess.run(
        [sys.executable, "-m", "agent_trace.cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise _CLIError(
            "agent-trace " + " ".join(args)
            + f"\n  rc={r.returncode}\n  stderr:\n{r.stderr}\n  stdout:\n{r.stdout}",
        )
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise _CLIError(
            f"agent-trace {' '.join(args)} returned non-JSON stdout:\n{r.stdout}\nstderr:\n{r.stderr}\n",
        ) from e


def _stable_json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, indent=2)


@unittest.skipIf(_skip_remote_e2e(), "set AGENT_TRACE_E2E_SERVICE_URL to run (see tests/docker-compose.test.yml)")
class TestRemotePushPullRoundTrip(unittest.TestCase):
    def test_push_pull_blame_and_context_match(self) -> None:
        base_url = _e2e_service_url()

        # Fail fast if nothing is listening (clearer than CLI sync errors).
        try:
            health = _http_json("GET", f"{base_url}/health", None, timeout=5)
        except (urllib.error.URLError, TimeoutError, AssertionError) as e:
            self.fail(
                f"Service not reachable at {base_url!r} ({e}). "
                "Start the stack: docker compose -f tests/docker-compose.test.yml "
                "--project-directory tests up -d --build",
            )
        self.assertEqual(health.get("status"), "ok")

        token = _fetch_bearer_token(base_url)
        slug = f"e2e-remote-{int(time.time() * 1000)}"
        _register_project(base_url, slug)
        remote_url = f"{base_url}/default/{slug}"

        with E2EContext() as ctx:
            ctx.git_init()
            ctx.write("seed.txt", "seed\n")
            ctx.git("add", "-A")
            ctx.git("commit", "-m", "seed")
            ctx.init()

            content = "def hello():\n    return 'world'\n"
            ctx.claude_write("hello.py", content)
            ctx.commit("ai write hello")

            ctx.cli("remote", "add", "origin", remote_url, "--token", token)
            push_r = ctx.cli("push", "--full")
            self.assertEqual(push_r.returncode, 0, push_r.stderr + push_r.stdout)
            self.assertNotIn("Error:", push_r.stderr)

            blame_before = _cli_json_env(
                ctx.env, ctx.repo,
                "blame", "hello.py", "--json", "--show-no-attribution",
            )
            ctx_before = _cli_json_env(ctx.env, ctx.repo, "context", "hello.py", "--json")

            # Fresh AGENT_TRACE_HOME + copy of the same git tree (stable project id).
            home2 = Path(tempfile.mkdtemp(prefix="at-remote-home2-"))
            parent2 = Path(tempfile.mkdtemp(prefix="at-remote-parent2-"))
            repo2 = parent2 / "repo"
            try:
                shutil.copytree(ctx.repo, repo2, symlinks=True)

                env2 = {
                    **{
                        k: v
                        for k, v in os.environ.items()
                        if k not in ("GIT_DIR", "GIT_WORK_TREE")
                    },
                    "AGENT_TRACE_HOME": str(home2),
                    "PATH": str(ctx.bin) + os.pathsep + os.environ.get("PATH", ""),
                    "PYTHONPATH": ctx.env["PYTHONPATH"],
                    "HOME": str(parent2),
                }

                r_add = subprocess.run(
                    [sys.executable, "-m", "agent_trace.cli", "remote", "add", "origin", remote_url, "--token", token],
                    cwd=str(repo2),
                    env=env2,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(r_add.returncode, 0, r_add.stderr + r_add.stdout)

                pull_r = subprocess.run(
                    [sys.executable, "-m", "agent_trace.cli", "pull"],
                    cwd=str(repo2),
                    env=env2,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(pull_r.returncode, 0, pull_r.stderr + pull_r.stdout)
                self.assertNotIn("Error:", pull_r.stderr)

                blame_after = _cli_json_env(
                    env2, repo2,
                    "blame", "hello.py", "--json", "--show-no-attribution",
                )
                ctx_after = _cli_json_env(env2, repo2, "context", "hello.py", "--json")

                self.assertEqual(_stable_json(blame_before), _stable_json(blame_after))
                self.assertEqual(_stable_json(ctx_before), _stable_json(ctx_after))
            finally:
                shutil.rmtree(home2, ignore_errors=True)
                shutil.rmtree(parent2, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
