"""Tests for entrypoint.sh config.js generation escaping (issue #59).

Env vars containing quotes, backslashes, or JS-breaking characters
must be properly escaped when written into config.js.

These tests run the actual entrypoint.sh config generation block
via subprocess to verify real behavior.
"""

import json
import os
import subprocess
import tempfile
import pytest


# Path from package/src/inferia/tests/ -> repo root docker/entrypoint.sh
REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
ENTRYPOINT_PATH = os.path.join(REPO_ROOT, "docker", "entrypoint.sh")


def generate_config_js(env_vars: dict) -> str:
    """
    Run the config.js generation section of entrypoint.sh in a subprocess
    with the given env vars, writing to a temp file.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        outpath = os.path.join(tmpdir, "config.js")

        # Read the entrypoint and extract only the config generation block
        with open(ENTRYPOINT_PATH) as f:
            content = f.read()

        # Build a script that sets DASHBOARD_DIR to our temp dir and
        # sources just the config generation part of the entrypoint
        script = f'''
set -e
export DASHBOARD_DIR="{tmpdir}"
'''
        # Find and extract the config generation block (between "if [ -d" and "fi")
        lines = content.split("\n")
        in_block = False
        for line in lines:
            if 'if [ -d "$DASHBOARD_DIR" ]' in line:
                in_block = True
                script += line + "\n"
                continue
            if in_block:
                script += line + "\n"
                if line.strip() == "fi":
                    break

        env = {**os.environ, **env_vars}
        result = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.fail(f"Script failed: {result.stderr}")

        with open(outpath) as f:
            return f.read()


class TestEntrypointConfigEscaping:

    def test_normal_urls_work(self):
        """Normal URL values produce valid config."""
        js = generate_config_js({
            "API_GATEWAY_URL": "https://api.example.com",
            "INFERENCE_URL": "https://inference.example.com",
        })
        assert "https://api.example.com" in js
        assert "https://inference.example.com" in js

    def test_double_quotes_in_env_var_escaped(self):
        """Double quotes in env var must not break the JS string."""
        js = generate_config_js({
            "API_GATEWAY_URL": 'https://evil.com", injected: "pwned',
        })
        # The output must NOT contain an unescaped injection that creates
        # a separate JS property
        assert 'injected: "pwned"' not in js

    def test_backslash_in_env_var_escaped(self):
        """Backslashes must not produce broken JS."""
        js = generate_config_js({
            "API_GATEWAY_URL": "C:\\Users\\test\\path",
        })
        assert "window.__RUNTIME_CONFIG__" in js

    def test_empty_values_work(self):
        """Empty/unset env vars produce valid config."""
        js = generate_config_js({})
        assert "window.__RUNTIME_CONFIG__" in js
