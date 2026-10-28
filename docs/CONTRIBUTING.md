# Contributing

Use an issue or pull request in the repository that distributes your copy.
Describe the input, expected behavior, actual behavior, and a minimal synthetic
example. Do not attach private manuscripts, credentials, production snapshots,
customer information, or generated signing material.

Create a separate branch and install dependencies using [INSTALL.md](INSTALL.md).
Run the affected unittest modules, `tests/stdio_smoke.py`, and the source-package
check. For changes to production or review behavior, run the complete existing
suite with `scripts/run_tests.ps1` or `scripts/run_tests.sh`.

Preserve source dialogue, existing workbook contracts, permissions, signatures,
and the distinction between machine validation and independent semantic review.
Explain intentional contract changes and migrations in the pull request.
Contributions must be yours to submit under the repository's MIT license;
preserve separate notices for third-party material.
