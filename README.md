# uptime-kuma-api2

A wrapper for the Uptime Kuma Socket.IO API (`v3` branch: Uptime Kuma 3.x support, beta)
---

> **About this project:** uptime-kuma-api2 is an independent continuation of
> [lucasheld/uptime-kuma-api](https://github.com/lucasheld/uptime-kuma-api),
> the original library created by [Lucas Held](https://github.com/lucasheld).
> That project appears to be unmaintained — its last release was in 2023, and
> open issues and pull requests (including several adding Uptime Kuma 2.x
> support) have sat unreviewed and unmerged. Rather than leave 2.x users
> without a working library, this project carries the code forward with full
> v2.x support while keeping backward compatibility with v1.x.
>
> It began as a fork and is published under the original MIT license. Lucas
> Held's copyright is retained in [LICENSE](LICENSE) alongside the later
> changes — full credit to him for building the foundation. This is a
> continuation, not a replacement: if the original project becomes active
> again, the changes here are available to contribute back upstream.

> **Naming:** the PyPI distribution is `uptime-kuma-api2`, while the import package remains `uptime_kuma_api` so existing code works unchanged. The similarly named PyPI projects `uptime-kuma-api` (the original) and `uptime-kuma-api-v2` (a different maintainer) are separate from this one. The repository was renamed from `uptime-kuma-api-v2` to `uptime-kuma-api2` to match the PyPI name; the old URL redirects automatically.

uptime-kuma-api2 is a Python wrapper for the [Uptime Kuma](https://github.com/louislam/uptime-kuma) Socket.IO API.

This package was originally developed to configure Uptime Kuma with Ansible. The original Ansible collection can be found at https://github.com/lucasheld/ansible-uptime-kuma.

Python version 3.10+ is required. Tested on 3.10 through 3.14.

Supported Uptime Kuma versions:

> **This is the `v3` branch, the beta of the 3.x line.** It is the Uptime Kuma 3.x line during the Uptime Kuma 3.0 beta, and it supports **Uptime Kuma 3.x only**. It is published as a pre-release, which pip installs only when asked: `pip install --pre "uptime-kuma-api2>=3.0.0b1"`. For Uptime Kuma 1.x and 2.x, use the 2.x line: `pip install "uptime-kuma-api2<3"` (the `main` branch). Progress is tracked in [#88](https://github.com/pbarone/uptime-kuma-api2/issues/88).

| Uptime Kuma          | uptime-kuma-api2           |
|----------------------|----------------------------|
| 3.0.0-beta.2 onward  | 3.0.0b1 (pre-release)      |
| 1.21.3 - 2.5.12      | 2.11.0 - 2.11.2, 2.x line  |

The floor on this branch is Uptime Kuma 3.0, and it is live-verified against 3.0.0-beta.2, over plain HTTP and over HTTPS behind a reverse proxy. Login uses Uptime Kuma 3.x's HTTP auth (including two-factor login and `login_by_token`), and API keys, `uptime`, `avg_ping`, the compact `get_monitors` and heartbeat history work against the 3.x server. To log in with an API key, pass it as a header, `UptimeKumaApi(url, headers={"Authorization": "Bearer <key>"})`, with the server's "API and MCP" setting turned on. API keys carried over from Uptime Kuma 2.x only work on `/metrics`. The CHANGELOG lists every breaking change from the 2.x API.

The 3.x line and the 2.x line can't be installed in the same Python environment. If you manage both Uptime Kuma 2.x and 3.x servers, use one environment per line. The 2.x line (2.11.1 and later) raises a clear error when you log in to an Uptime Kuma 3.x server, and the 3.x line refuses to connect to a server older than Uptime Kuma 3.0.

Earlier release lines, kept as a record of what each published version was documented to support:

| Uptime Kuma     | uptime-kuma-api2 |
|-----------------|------------------|
| 1.21.3 - 2.5.0  | 2.3.0 - 2.10.1   |
| 2.0.0 - 2.4.0   | 2.0.0 - 2.2.1    |
| 1.21.3 - 1.23.2 | 1.0.0 - 1.2.1    |
| 1.17.0 - 1.21.2 | 0.1.0 - 0.13.0   |

Releases 1.2.1 and earlier were published under the upstream `uptime-kuma-api` name; 2.0.0 onward are published as `uptime-kuma-api2`.

Installation
---
uptime-kuma-api2 is available on the [Python Package Index (PyPI)](https://pypi.org/project/uptime-kuma-api2/).

You can install it using pip:

```
pip install uptime-kuma-api2
```

Documentation
---
The API reference is published on [Read the Docs](https://uptime-kuma-api2.readthedocs.io).

You can also build it locally with Sphinx:

```
pip install -r dev-requirements.txt
cd docs && make html
```

Note: [uptime-kuma-api.readthedocs.io](https://uptime-kuma-api.readthedocs.io) (without the `2`) is the *original* project's site and does not cover this project's v2 features.

Example
---
Once you have installed the python package, you can use it to communicate with an Uptime Kuma instance.

To do so, import `UptimeKumaApi` from the library and specify the Uptime Kuma server url (e.g. 'http://127.0.0.1:3001'), username and password to initialize the connection.

```python
>>> from uptime_kuma_api import UptimeKumaApi, MonitorType
>>> api = UptimeKumaApi('INSERT_URL')
>>> api.login('INSERT_USERNAME', 'INSERT_PASSWORD')
```

**Note on the Uptime Kuma "API key":** on Uptime Kuma 3.x, an API key created in the web UI can authenticate this library when you pass it as `UptimeKumaApi(url, headers={"Authorization": "Bearer <key>"})` and the server's "API and MCP" setting is on. Keys carried over from Uptime Kuma 2.x only grant access to the Prometheus `/metrics` endpoint. You can also authenticate with a username and password, or with a login token via `login_by_token()`.

Now you can call one of the existing methods of the instance. For example create a new monitor:

```python
>>> result = api.add_monitor(type=MonitorType.HTTP, name="Google", url="https://google.com")
>>> print(result)
{'msg': 'successAdded', 'msgi18n': True, 'monitorID': 1}
```

At the end, the connection to the API must be disconnected so that the program does not block.

```python
>>> api.disconnect()
```

With a context manager, the disconnect method is called automatically:

```python
from uptime_kuma_api import UptimeKumaApi, MonitorType

with UptimeKumaApi('INSERT_URL') as api:
    api.login('INSERT_USERNAME', 'INSERT_PASSWORD')
    api.add_monitor(
        type=MonitorType.HTTP,
        name="Google",
        url="https://google.com"
    )
```

MonitorBuilder
---
For complex monitor configurations, use the fluent `MonitorBuilder`:

```python
from uptime_kuma_api import UptimeKumaApi, MonitorType, MonitorBuilder

with UptimeKumaApi('INSERT_URL') as api:
    api.login('INSERT_USERNAME', 'INSERT_PASSWORD')
    
    config = (
        MonitorBuilder()
        .type(MonitorType.HTTP)
        .name("My Monitor")
        .url("https://example.com")
        .interval(60)
        .conditions([
            {"type": "expression", "variable": "response_status", "operator": "==", "value": "200", "andOr": ""}
        ])
        .build()
    )
    result = api.add_monitor(**config)
    print(result)
```

What this adds over the original library
---
- **New monitor types**: RabbitMQ, SNMP, SMTP, System Service
- **New notification providers**: Nextcloud Talk, Brevo, Evolution API
- **MonitorBuilder**: Fluent builder pattern for monitor configuration
- **Logger support**: Pass a custom logger for Socket.IO debugging
- **Version gating**: a field or monitor type that a later 3.x release introduces can be gated behind a per-field floor, so older 3.x servers keep working (no gate is needed today, because every field ships in Uptime Kuma 3.0)
- **Fixes carried forward from the upstream tracker**, plus defects found by this project's own live verification against real servers

See [CHANGELOG.md](CHANGELOG.md) for the release-by-release detail — this list is deliberately not versioned, so it does not go stale one release at a time.

Testing
---
The unit tests need no live server, and are what a bare `pytest` runs. This is also exactly what CI runs:

```
pip install pytest
pytest -v
```

The remaining test files are integration tests inherited from upstream. They expect a live Uptime Kuma instance at `UPTIME_KUMA_TEST_URL` (default `http://127.0.0.1:3001`; any host other than `127.0.0.1` or `localhost` is refused) and will **delete all monitors, notifications, proxies, tags, status pages, docker hosts, maintenances and API keys** on that instance, so never point them at a production server. They are excluded from a plain `pytest` run for that reason — opting in is explicit:

```
pytest -m integration     # DESTRUCTIVE: disposable instances only
```

The exclusion is not a list of filenames anyone maintains. `tests/conftest.py` marks a test `integration` when its class extends `UptimeKumaTestCase`, the base class whose `setUp` performs those deletions, and `pytest.ini` deselects that marker by default. So "needs a live server" is read off the code rather than restated, and adding a test file requires no change to CI, to the workflows, or to any command in this README.

The table below is coverage documentation, not a list anything executes — letting it fall behind degrades the docs, it cannot silently un-run a test.

Test files:

| File | Coverage |
|------|----------|
| `tests/test_monitor_types_v2.py` | New monitor types (RABBITMQ, SNMP, SMTP, SYSTEM_SERVICE) |
| `tests/test_monitor_params_v2.py` | Monitor parameters, payload shape, validation, monitor-list cache writes, unparseable server versions |
| `tests/test_version_gates.py` | Version gating on 3.x: every former 1.x/2.x gate is unconditional, and the gating mechanism (withhold and warn, per-type floors, pre-release handling) against a synthetic 3.1 floor |
| `tests/test_status_page_v2.py` | Status page analytics keys, optional fields, SSL verify, the withhold-and-warn mechanism |
| `tests/test_notification_v2.py` | Nextcloud Talk, Brevo, Evolution API providers |
| `tests/test_logger.py` | Logger parameter type validation |
| `tests/test_monitor_builder.py` | MonitorBuilder fluent API |
| `tests/test_status_page_incidents.py` | `get_status_page()` returns the `incidents` list and no singular `incident` key |
| `tests/test_incident_management_v3.py` | `edit_incident()`, `resolve_incident()`, `delete_incident()`, `get_incident_history()`, the lazy `iter_incident_history()` and `post_incident()` without the extra save |
| `tests/test_delete_id_coercion_v2.py` | String vs integer id in the existence guard of all seven `delete_*` methods (#91) |
| `tests/test_monitor_cache_v2.py` | The `updateMonitorIntoList` / `deleteMonitorFromList` delta handlers and the refreshed `delete_monitor` / `delete_monitor_tag` guards |
| `tests/test_notification_ntfy_v2.py` | ntfy notification payload building and argument checks |
| `tests/test_important_heartbeats_v2.py` | `get_important_heartbeats()` requests history per monitor (#63) |
| `tests/test_heartbeat_list_regression.py` | Heartbeat list events append without nesting |
| `tests/test_uptime_kuma_3_detection.py` | Servers older than Uptime Kuma 3.0 are refused at connect time (#88) |
| `tests/test_auth_http_v3.py` | The HTTP auth helper and the cookie reconnect |
| `tests/test_login_v3.py` | `login()` over the HTTP auth flow: success, failure and the 429 rate limit |
| `tests/test_session_v3.py` | `login_by_token()` and `logout()` |
| `tests/test_twofa_v3.py` | Two-factor methods and two-factor login |
| `tests/test_account_v3.py` | `need_setup()`, `setup()` and `change_password()` |
| `tests/test_api_key_v3.py`, `tests/test_api_key_properties_v3.py`, `tests/test_api_key_lookup_properties_v3.py` | API-key methods, `get_api_keys()` normalization and `get_api_key()` lookup |
| `tests/test_event_state_v3.py` | The `session`, `stats` and `heartbeatLists` events and the connection cache reset |
| `tests/test_stats_v3.py`, `tests/test_stats_uptime_properties_v3.py`, `tests/test_stats_avg_ping_properties_v3.py`, `tests/test_stats_merge_properties_v3.py` | `uptime()` and `avg_ping()` read from the `stats` event, including property tests |
| `tests/test_monitor_cache_v3.py`, `tests/test_monitor_cache_properties_v3.py` | The compact monitor list and `get_monitors()` |
| `tests/test_heartbeat_lists_v3.py`, `tests/test_heartbeat_properties_v3.py` | Heartbeat history from `heartbeatLists`, including the merge property test |
| `tests/test_live_auth_helpers_v3.py` | Offline checks of the helpers in `tests/live_test_auth_v3.py` |
| `tests/test_dev_dependency_v3.py` | The dev-only `hypothesis` dependency wiring |
