#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runs scripts/setup_datastores.py end to end off-line.

Nothing in the repository imports this script - the generated setup script
invokes it as a program - so `py_compile` was the only gate it passed, and
`py_compile` checks syntax, not whether a name is bound. The Firestore section
is reached only when ENABLE_DATASTORE_FS is set, but the two module-level lines
that read that environment variable sit outside the guard and run on every
invocation, so an unbound name there also takes out the DataStore-to-Engine
attachment behind it. `setup_and_deploy.sh` calls the script as
`... >/dev/null 2>&1 || true`, so the run stays green and the only symptom is an
app with no DataStores attached to it.

The test substitutes argv, environment and the network, then asserts the run
reaches the end and writes `.datastores.json` naming the DataStores it created.

    python3 test_setup_datastores.py
"""

import json
import os
import runpy
import subprocess
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "skills", "ge-demo-generator", "templates",
                      "scripts", "setup_datastores.py")

SERVICE = "demo-svc"


class _Completed:
    """What `bq` returns when the CLI is not on PATH in the test environment."""

    returncode = 1
    stdout = ""
    stderr = ""


class _Requests:
    """Collects outbound calls instead of making them."""

    def __init__(self):
        self.calls = []

    def __call__(self, request, *args, **kwargs):
        body = request.data.decode("utf-8") if request.data else ""
        self.calls.append((request.full_url, json.loads(body) if body else None))
        raise OSError("offline")

    def payload_for(self, suffix):
        for url, body in self.calls:
            if url.endswith(suffix):
                return body
        raise AssertionError("no request to %s in %r" % (suffix, self.calls))


def run_script(extra_argv, env_extra):
    """Runs the script in a throwaway cwd and returns (.datastores.json, requests)."""
    requests = _Requests()
    saved_run = subprocess.run
    saved_urlopen = urllib.request.urlopen
    saved_argv = list(sys.argv)
    saved_cwd = os.getcwd()
    saved_env = {k: os.environ.get(k)
                 for k in ("ENABLE_DATASTORE_FS", "FIRESTORE_COLLECTION",
                           "GCS_BUCKET_NAME")}

    with tempfile.TemporaryDirectory() as tmp:
        sys.argv = ["setup_datastores.py", "global", "demo-project", "global",
                    "demo-app", "fake-token", SERVICE] + extra_argv
        os.environ["ENABLE_DATASTORE_FS"] = "1"
        os.environ["FIRESTORE_COLLECTION"] = "incidents"
        os.environ["GCS_BUCKET_NAME"] = "demo-bucket"
        os.environ.update(env_extra)
        os.chdir(tmp)
        subprocess.run = lambda *a, **k: _Completed()
        urllib.request.urlopen = requests
        try:
            runpy.run_path(SCRIPT, run_name="__main__")
            with open(".datastores.json") as fh:
                written = json.load(fh)
        finally:
            subprocess.run = saved_run
            urllib.request.urlopen = saved_urlopen
            sys.argv = saved_argv
            os.chdir(saved_cwd)
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    return written, requests


def test_deploy_invocation():
    """The argv `setup_and_deploy.sh` passes: no enable_* flags, names only."""
    written, _ = run_script(["demo-bucket", "demo_dataset"], {})
    assert written["datastores"] == [
        "ds-%s-gcs" % SERVICE, "ds-%s-bq" % SERVICE, "ds-%s-fs" % SERVICE,
    ], written
    assert written["app_id"] == "demo-app", written


def test_env_fallback_for_staging_bucket():
    """An explicitly enabled GCS side with no bucket falls back to the environment.

    That fallback is the third `os.environ` read in the file, and it is only
    evaluated when the bucket argument is empty.
    """
    written, requests = run_script(["", "demo_dataset", "true"], {})
    assert written["datastores"] == [
        "ds-%s-bq" % SERVICE, "ds-%s-fs" % SERVICE,
    ], written
    payload = requests.payload_for("/dataStores/ds-%s-fs/branches/0/"
                                   "documents:import" % SERVICE)
    assert payload["firestoreSource"]["gcsStagingDir"] == (
        "gs://demo-bucket/firestore_staging"), payload


def main():
    if not os.path.exists(SCRIPT):
        print("SKIP: %s not found" % SCRIPT)
        return 0
    test_deploy_invocation()
    test_env_fallback_for_staging_bucket()
    print("setup_datastores: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
