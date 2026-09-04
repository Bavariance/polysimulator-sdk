# Changelog

All notable changes to the PolySimulator Python SDK.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `examples/` — five runnable scripts, from a first trade to porting a
  `py-clob-client` bot by changing the host.
- `CONTRIBUTING.md`, including what kinds of issue are most useful (fidelity bug
  reports, and anywhere the compatibility surface is not actually drop-in).
- This changelog. Earlier releases are reconstructed below from git history and
  are less detailed than they would have been written at the time.

## [0.4.3] — 2026-07-06

Current release on PyPI.

## [0.4.x and earlier] — 2026-06

Three import surfaces in one package:
- `polysim_sdk` — the native client, sync and async, with WebSocket streaming.
- `polysim_clob_client` — drop-in for Polymarket's `py-clob-client` v1; 63 of 63
  `ClobClient` methods present.
- `polysim_polymarket` — paper-mode mirror of the newer unified
  `polymarket-client` (py-sdk).

Paper mode throughout: no private key, no `chain_id`, no `funder`, no
`signature_type`, no EIP-712, no USDC approvals, no RPC. Runtime dependencies are
`httpx` and `websockets` only.

[Unreleased]: https://github.com/Bavariance/polysimulator-sdk/compare/v0.4.3...HEAD
[0.4.3]: https://github.com/Bavariance/polysimulator-sdk/releases/tag/v0.4.3
