.. _api:


Main Interface
--------------

.. module:: uptime_kuma_api

.. autoclass:: UptimeKumaApi
    :inherited-members:


.. _auth-v3:

Authentication on Uptime Kuma 3.x
---------------------------------

**Password login.** :meth:`~uptime_kuma_api.UptimeKumaApi.login` signs in through Better Auth over HTTP and then reconnects the Socket with the session cookie. Because the Socket is replaced, do not rely on event data received before ``login``: read it again after logging in. Statistics (``uptime``, ``avgPing``) for a monitor created after login only arrive after the next login or the server's periodic refresh, which runs every 15 minutes (``server/monitor-stats.ts``).

**Rate limit.** Better Auth allows 10 requests per 60 seconds. Repeated ``login`` calls, for example in a loop or a retry storm, can exhaust that budget, and the server then rejects further sign-in attempts until the window passes. Log in once and reuse the connection.

**Tokens.** :meth:`~uptime_kuma_api.UptimeKumaApi.login_by_token` is supported on 3.x. Pass the token that ``login`` returned, which is the value of the ``better-auth.session_token`` cookie. Better Auth rotates that cookie when two-factor settings change, and the library keeps the rotated one. ``login`` has no ``api_key`` parameter. Over HTTPS the cookie keeps the same name; the server only marks it ``Secure``. Behind a reverse proxy, enable WebSocket support, because the Socket uses a WebSocket.

**API keys.** A key created on Uptime Kuma 3.x authenticates the Socket when you send it as a bearer header on the connection. The server only accepts it when the "API and MCP" setting (``apiEnabled``, Settings -> General) is on::

    from uptime_kuma_api import UptimeKumaApi

    with UptimeKumaApi(
        "http://127.0.0.1:3001",
        headers={"Authorization": "Bearer INSERT_API_KEY"},
    ) as api:
        print(api.get_monitors())

No call to ``login`` is needed in that case.

**Legacy keys.** Keys carried over from Uptime Kuma 2.x are listed as Legacy API Keys. They work only on the Prometheus ``/metrics`` endpoint, not on the Socket (see louislam/uptime-kuma#8258).


.. _v2-only-fields:

Version-gated fields
--------------------

This version of the library supports Uptime Kuma 3.0 and newer. Every monitor
field, monitor type and status-page field it knows is available on Uptime Kuma
3.0, so nothing is gated today. The mechanism stays in place for the first field
or type that a later 3.x release introduces. When a field is gated and you supply
it to a server that does not implement it, there is one rule:

    **The field is left out of the payload, and you are told once per call.**

That rule applies to every version-gated monitor field on
:meth:`~uptime_kuma_api.UptimeKumaApi.add_monitor` and
:meth:`~uptime_kuma_api.UptimeKumaApi.edit_monitor`, so the behaviour of a new
field is inherited rather than decided again.

**How you are told.** A single :class:`UnsupportedFieldWarning` is emitted per
call, naming every field that was withheld, the server version each one requires,
and the version your server reports. One warning per call, not one per field.

**Which fields, and from which version.** The set is internal, because it tracks
what the server supports rather than anything you configure. It is empty today.
Supply the fields you want and the warning names any your server cannot take.

**If you would rather it failed loudly.** Escalate the category, and a request for
an unsupported field raises instead of being dropped, before anything is sent::

    import warnings
    from uptime_kuma_api import UnsupportedFieldWarning

    warnings.simplefilter("error", UnsupportedFieldWarning)

Or silence it, if you deploy against mixed server versions deliberately and the
warning is noise::

    warnings.simplefilter("ignore", UnsupportedFieldWarning)

**What this is not.** Argument validation is unaffected and version-independent:
an out-of-range or wrongly-typed value still raises ``ValueError`` or
``TypeError`` on every server version, because a bad value is a bad value
regardless of what the server would have accepted.

Monitor *types* are governed separately. A type the server has no implementation
of raises rather than being degraded, since a type is the thing being requested
rather than a parameter whose loss can be absorbed. The table of type floors is
empty today.

**Status-page fields.** The same withhold-and-warn rule applies to
:meth:`~uptime_kuma_api.UptimeKumaApi.save_status_page`, with its own internal
table of floors, also empty today. The analytics trio (``analyticsType``,
``analyticsId``, ``analyticsScriptUrl``) will not be gated even then: the server
requires ``analyticsType`` to be present in the payload and rejects the entire
save when it is absent, so all three are always sent, including when ``None``.

``password`` and ``googleAnalyticsId`` are no longer parameters of
:meth:`~uptime_kuma_api.UptimeKumaApi.save_status_page`: the 3.x server does not read them, so they were removed rather than accepted and ignored.

MonitorBuilder
--------------

.. autoclass:: MonitorBuilder
    :members:


Enums
-----

.. autoclass:: AuthMethod
    :members:

.. autoclass:: MonitorType
    :members:

.. autoclass:: MonitorStatus
    :members:

.. autoclass:: NotificationType
    :members:

.. autoclass:: ProxyProtocol
    :members:

.. autoclass:: IncidentStyle
    :members:

.. autoclass:: DockerType
    :members:

.. autoclass:: MaintenanceStrategy
    :members:

.. autoclass:: Event
    :members:
    :undoc-members:


Notification provider metadata
------------------------------

Two module-level tables describe the arguments each notification provider
accepts. They are generated from Uptime Kuma's own frontend, so their contents
track the providers the server supports.

Between them they drive every argument check on the notification methods:
rejecting unknown keyword arguments, enforcing the required ones for the chosen
provider, range-checking the numeric ones, and clearing a previous provider's
options when :meth:`~uptime_kuma_api.UptimeKumaApi.edit_notification` changes a
notification's type. They are exported so that callers can inspect the same
tables the library validates against.

.. py:data:: notification_provider_options

    Keyed by :class:`NotificationType`, with one entry per member. Each value
    maps an option's keyword-argument name to a description of it::

        notification_provider_options[NotificationType.ALERTA]
        # {'alertaApiEndpoint': {'type': 'str', 'required': True},
        #  'alertaApiKey': {'type': 'str', 'required': True},
        #  ... }

    ``type`` is a Python type name as a string (``'str'``, ``'int'``, ``'bool'``,
    ``'list'``, ``'dict'``) and ``required`` marks the options that must be
    supplied for that provider. There is an entry for every member of
    :class:`NotificationType`, so the table is too large to reproduce here; read
    it from the attribute directly.

.. py:data:: notification_provider_conditions

    Numeric bounds for the few options that have them, keyed by the option name
    rather than by provider, since an option name is unique across providers.
    Each value carries ``min`` and ``max``, and a value outside that range is
    rejected before any request is sent::

        {'gotifyPriority': {'min': 0, 'max': 10},
         'ntfyPriority': {'min': 1, 'max': 5},
         ... }


Exceptions and warnings
-----------------------

.. autoexception:: UptimeKumaException

.. autoexception:: Timeout

.. autoexception:: UnsupportedFieldWarning
