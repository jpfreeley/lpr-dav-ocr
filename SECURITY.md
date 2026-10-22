# Security

The review page has no login and relies on binding `127.0.0.1`. State-changing requests require an
`X-Requested-With` header, and image paths are confined to the frames folder. Do not expose the page to a network.

The ntfy topic is a shared secret. Alerts can contain plate numbers, so read
[docs/privacy.md](docs/privacy.md) before enabling them.

To report a vulnerability, contact the maintainer privately rather than opening a public issue. Do not include real
plates or recordings in a report.
