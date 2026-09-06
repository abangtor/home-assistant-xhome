# Documentation

Architecture notes, reverse-engineering references, and Home Assistant design
documents live here.

Keep secrets, tokens, full device UIDs, and private media out of this directory.

HACS runtime packaging uses the vendored API client under
`custom_components/xhome/api`. Keep that copy in sync with `src/xhome` until the
API client is published as a separate package.

## Pairing and local-only research

- [Door pairing protocol](PAIRING_PROTOCOL.md): AP/UDP, BLE, QR, account binding,
  region mapping, and source evidence for XHome 1.6.4.8.
- [Local-only feasibility](LOCAL_ONLY_FEASIBILITY.md): established capabilities,
  firmware-side unknowns, and a staged self-hosted proof-of-concept plan.
