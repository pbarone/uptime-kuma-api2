---
inclusion: always
---

# Product

`uptime-kuma-api2` is a Python wrapper for the [Uptime Kuma](https://github.com/louislam/uptime-kuma)
Socket.IO API. It is a maintained **continuation** of the dormant
[`lucasheld/uptime-kuma-api`](https://github.com/lucasheld/uptime-kuma-api),
carried forward under the original MIT license with the original author's
copyright retained. It is not a hostile fork; credit the original author and
incorporated PR authors.

## Naming (easy to get wrong)

- **PyPI distribution:** `uptime-kuma-api2` (`pip install uptime-kuma-api2`)
- **Import package:** `uptime_kuma_api` (unchanged from upstream, so existing
  user code works as-is — never rename this)
- **GitHub repo:** `pbarone/uptime-kuma-api2` (standalone; renamed from
  `uptime-kuma-api-v2`, old URL redirects)
- The PyPI names `uptime-kuma-api` (original) and `uptime-kuma-api-v2`
  (an unrelated maintainer) are **not** this project.

## The one non-negotiable principle

**On this branch: Uptime Kuma 3.x only.** This is the `v3` branch: the Uptime Kuma 3.x line during the Uptime Kuma 3.0 beta ([#88](https://github.com/pbarone/uptime-kuma-api2/issues/88)). The library supports Uptime Kuma 3.0 and later. Uptime Kuma 1.x and 2.x are supported by the 2.x line on `main` (`uptime-kuma-api2<3`), and the backward-compatibility rule for 1.x lives there. Connecting to a server older than 3.0 raises a clear error that points to `uptime-kuma-api2<3`.

Behavior that differs between 3.x releases is gated behind `parse_version(self.version)`, so older 3.x servers keep working when a later 3.x release adds a field. The public API (method names, arguments, return shapes) is still a contract for callers, including the Ansible collection: keep it stable wherever Uptime Kuma 3.x allows it. See the policy in coding-standards steering before altering any public behavior.

The code paths for 1.x and 2.x servers (the pre-1.22 payload path and the `1.22`, `1.23`, `1.23.1` and `2.0` gates) are removed on this branch. They are kept on `main`.

## Audience

Users automate Uptime Kuma configuration (often via the companion Ansible
collection). They depend on stable public method signatures and return shapes.
Treat the public API as a contract.
