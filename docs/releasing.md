# Releasing csp-lab

The release workflow publishes the same `vX.Y.Z` version to GHCR and PyPI. It
builds and tests the Python distributions first, then publishes the multi-platform
image, then uploads the distributions. The image has Buildx provenance, an SBOM,
and a GitHub attestation. PyPI trusted publishing provides package attestations.

## One-time setup

1. Create a GitHub environment named `release`. Require a maintainer review and
   restrict deployments to protected tags matching `v*`.
2. Protect `main` and the `v*` tag namespace with repository rulesets. Release
   tags must point to a commit already on `main`.
3. On PyPI, configure a trusted publisher for project `csp-lab` with owner
   `lackim`, repository `csp-lab`, workflow `release.yml`, and environment
   `release`. If the project does not yet exist, create it with PyPI's pending
   publisher flow. No PyPI API token is required in GitHub secrets.
4. Allow the workflow's `GITHUB_TOKEN` to publish the `ghcr.io/lackim/csp-lab`
   package. Make the package public after its first successful publication so
   anonymous `uvx csp-lab up` users can pull it.
5. When these protections are in place, set repository Actions variable
   `RELEASE_ENABLED` to `true`. Until then, a release tag fails before either
   registry is contacted.

The `release` environment and PyPI trusted publisher must be configured before
pushing the first tag. The workflow deliberately fails if the tag does not match
the version in `pyproject.toml` or points outside `main`.

## Publish

1. Update `pyproject.toml` and `uv.lock` to the desired version in a reviewed PR.
2. Merge the PR and confirm CI on `main` is green.
3. Create and push a protected annotated tag, for example:

   ```sh
   git tag -a v0.1.0 -m 'csp-lab v0.1.0'
   git push origin v0.1.0
   ```

4. Approve the `release` environment deployment after checking the tag and
   workflow run. Wait for both publication jobs to finish.
5. Confirm the GHCR package is public, then verify from a clean directory:

   ```sh
   uvx csp-lab==0.1.0 up --protocol 2
   uvx csp-lab==0.1.0 ping 2
   uvx csp-lab==0.1.0 down
   gh attestation verify oci://ghcr.io/lackim/csp-lab:v0.1.0 --owner lackim
   ```

If the image job succeeds and the PyPI job fails, correct the trusted publisher
or package issue and rerun the failed job. The tag and version must stay the same;
do not overwrite a published PyPI release.
