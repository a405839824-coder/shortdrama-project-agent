# Security and data handling

The local server processes files and project records on the machine where it
runs. Use synthetic or authorized source material. Runtime data belongs under
`data/` or an explicitly configured private data directory, outside source
distribution. The software license does not grant rights in imported manuscripts.

Local HTTP is intended for loopback (`127.0.0.1`). Shared or internet-facing
deployments require the existing OAuth configuration; follow [CLOUD_MCP.md](CLOUD_MCP.md).
Do not expose unauthenticated local mode on a public interface.

Keep `.env*`, `.app.json`, tokens, signing keys, OAuth configuration, runtime
snapshots, logs, and account-specific MCP launch files out of public releases.
The source packager checks for several credential patterns and unsafe paths;
this is a bounded check, not a guarantee that every secret can be detected.

For a vulnerability, use GitHub's private vulnerability reporting if it is
enabled on the published repository. If it is unavailable, open a minimal issue
asking the maintainer for a private reporting channel, without exploit details
or sensitive data. A private reporting channel and response timeframe have not
yet been established for this local release candidate.
