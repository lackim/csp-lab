# Versioning policy

Three version numbers matter here and they have different meanings:

| Layer | Current support | How it is selected |
| --- | --- | --- |
| libcsp library and API | v2.1, pinned as a Git submodule commit | Build time |
| CSP wire protocol | v1 or v2 | `csp-lab up --protocol 1` or `--protocol 2` |
| csp-lab CLI, Python, pytest, and MCP API | 0.2.x prototype | Project release |

The current image always builds against libcsp v2.1. Selecting `--protocol 1` changes the wire format used by that library; it does **not** build libcsp 1.x. The two simulated nodes and the probe always use the same protocol setting. A running lab must be stopped before switching protocols.

## Updating libcsp 2.x

Update the pinned submodule in a dedicated change. Check the C API and ZMQ build, then run `uv run --locked pytest` and `bash test/integration.sh` for both wire protocols. Review the generated Docker image and record the new libcsp commit in the change. Keep the CLI and MCP output compatible within a csp-lab release unless a breaking change is intentional.

## Historical libcsp 1.x

libcsp 1.x has materially different build and API surfaces. If support becomes useful, add a separate legacy native build and adapter, with its own pinned revision and CI job. Do not switch native library generations implicitly based on `--protocol`; that option is reserved for the wire protocol. A mixed v1/v2 network is outside this lab's scope.

## Support matrix

| Native library | Wire protocol | Status |
| --- | --- | --- |
| libcsp v2.1 | CSP v1 | Tested locally; covered by the CI workflow |
| libcsp v2.1 | CSP v2 | Tested locally; covered by the CI workflow |
| libcsp 1.x | CSP v1 | Unsupported |
| Any library | Mixed CSP v1/v2 in one lab | Unsupported |
