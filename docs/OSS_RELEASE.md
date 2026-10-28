# Full open-source edition

Prepared: 2026-10-28 (Asia/Shanghai).

This edition contains the full Shortdrama Project Agent implementation, including
the storyboard rule engine, skill instructions, source tracking, revision history,
independent review workflow, role controls and Excel exports. It is based on
commit `70d2044ab7842ac748a7af468ff294408b132d0b`, runtime version `0.5.68`.
It is distinct from the smaller `shortdrama-basic` project.

The `open-source` branch adds the MIT license, installation instructions,
contribution and security guidance, dependency notices, and portable local MCP
setup. All 193 server and skill files are byte-identical to the base commit.
The existing `main` branch is retained for the existing deployment; this release
does not change its code, credentials, member registry or stored projects.

The current installation documentation uses generic deployment and project
examples. The repository retains development history, which includes historical
compatibility/work-order identifiers, author metadata and an old deployment
address. These are not credentials and grant no service or project access.
Associated project records, manuscripts, account connections, local storage
secrets and live configuration are not included in the source archive.

The MIT license covers the project's code and bundled rules. Third-party
dependencies retain their own licenses. Novels, scripts, images and other
material imported by a user are not licensed by this repository. The included
spreadsheet fixture is synthetic test material.

## Validation scope

On Windows, a fresh installation completed successfully. All 572 bundled runtime
checks passed; 16 installation/packaging tests completed with 14 passes and two
platform-conditional skips. Both stdio and HTTP smoke checks exposed the expected
24 local-admin tools. Source packaging also passed. The complete unit-test suite
and macOS/Linux installation were not rerun for this documentation/setup release.

The release preparation checks installation helpers, source packaging, local
runtime self-tests and MCP transports. The development history also records
older validations; these are not claims about this release's production service
or a user's actual manuscript. Software checks do not establish narrative
quality, measured media timing, independent manuscript acceptance or public
adoption. This repository is not an official plugin-directory listing.

## Source archive

After installation, run `scripts/configure_local_mcp.py --portable` before
`scripts/package_plugin.py --profile source`. The resulting ZIP contains source
and installation instructions, without Git history or a Python environment.
Run `scripts/configure_local_mcp.py` again to restore your local MCP paths.

No subscription, API credit or hosted service is included. Any Codex for Open
Source application is reviewed separately by OpenAI; publication is not approval.
