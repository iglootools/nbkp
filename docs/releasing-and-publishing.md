# Releasing and Publishing

The build uses the [uv-dynamic-versioning](https://github.com/ninoseki/uv-dynamic-versioning)
hatchling plugin to automatically set the version based on git tags. It is powered by the same
[dunamai](https://github.com/mtkennerly/dunamai) library as the `poetry-dynamic-versioning` plugin
used before the move to uv, so version strings are unchanged: a tagged commit builds as `0.2.1`, and
an untagged one as `0.2.1.post5.dev0+4de5b15`.

Because the version comes from git rather than from `pyproject.toml`, a build that cannot see the
repository history has no version to report. `[tool.uv-dynamic-versioning] fallback-version` makes
that case produce `0.0.0` instead of failing — which is required, since Renovate builds the project
without git context — so a build from a shallow checkout or an exported archive succeeds while
producing a mis-versioned artifact. `mise run build-verify` (wired into `ci-build`) inspects the built
wheel's metadata and fails on `0.0.0`, because PyPI does not allow a version to be re-uploaded once
taken. That is also why the publish workflows check out with `fetch-depth: 0` and `fetch-tags: true`. 

The following GitHub workflows are set up to automate the release and publishing process:
1. The `release` workflow takes care of pushing a tag based on conventional commits and creating the Github release.
   - This workflow uses the [github-tag](https://github.com/marketplace/actions/github-tag) action
   - It is triggered manually using `gh workflow run release.yml`
2. The `publish` workflow takes care of publishing the package to PyPI
   - This workflow uses the [pypa/gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish) action
   - It is triggerred automatically when the `release` workflow completes, 
   - But it can also be re-triggered manually if needed using `gh workflow run publish.yml --ref <tag>`

## PyPI Config

[PyPI](https://pypi.org/) and [Test PyPI](https://test.pypi.org/) have been configured to allow the `nbkp` project to be published using OpenID Connect (OIDC) authentication:
- Github project name: `iglootools/nbkp`
- Workflow: `publish.yml`
- Github Environment: `pypi` for production releases (to PyPI), `testpypi` for testing releases (to Test PyPI)

Check [Publishing to PyPI with a Trusted Publisher](https://docs.pypi.org/trusted-publishers/) for more details on OIDC authentication.