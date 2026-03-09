# Releasing and Publishing

The build uses the [poetry-dynamic-versioning](https://github.com/mtkennerly/poetry-dynamic-versioning) plugin 
to automatically set the version based on git tags. 

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