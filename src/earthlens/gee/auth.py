"""Service-account authentication for the Google Earth Engine backend.

Hosts :class:`EarthEngineAuth`, a thin wrapper over the
`earthengine-api` (`ee`) authentication entry points. The Earth Engine
backend authenticates with a Google Cloud *service account* plus a JSON
key file (no interactive browser login on the machine that runs the
download); :class:`EarthEngineAuth.initialize` performs the one-time
`ee.Initialize` against a *registered* Cloud project.

The Cloud project the calls are scoped/billed to is mandatory on
current `earthengine-api` releases: it is taken from the explicit
`project` argument when given, else from the key file's `project_id`
field. A project that has never been registered for Earth Engine, or
that the service account lacks permission on, surfaces as an
:class:`AuthenticationError` with a pointer at the registration /
permissions docs rather than a raw `ee` exception.

See:
    - Service accounts: <https://developers.google.com/earth-engine/guides/service_account>
    - Registering a project: <https://code.earthengine.google.com/register>
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import ee

_REGISTER_URL = "https://code.earthengine.google.com/register"
_SERVICE_ACCOUNT_DOCS = (
    "https://developers.google.com/earth-engine/guides/service_account"
)


class AuthenticationError(Exception):
    """Raised when the Earth Engine connection cannot be established.

    Wraps the underlying `ee` / Google credential errors with an
    actionable message — most commonly a missing or malformed service
    key, an unregistered Cloud project, or a service account that lacks
    an Earth Engine IAM role on the target project.
    """


def _load_key_dict(service_key: str) -> dict[str, Any] | None:
    """Return the parsed service-account JSON, or `None` if not parseable.

    Accepts either a filesystem path to the key file or the raw JSON
    string itself; returns `None` when `service_key` is neither (so the
    caller can still proceed with whatever `ee` accepts and only error
    if `ee` itself rejects it).

    Args:
        service_key: Path to the service-account JSON file, or the JSON
            content as a string.

    Returns:
        The decoded key mapping, or `None` if it could not be read or
        parsed.
    """
    try:
        path = Path(service_key)
        if path.is_file():
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    try:
        return json.loads(service_key)
    except (ValueError, TypeError):
        return None


class EarthEngineAuth:
    """Authenticate and initialise a connection to Google Earth Engine.

    Construct this with a service-account email and key (file path or
    raw JSON); construction performs the one-time `ee.Initialize`. The
    Cloud project is read from the `project` argument or, failing that,
    from the key file's `project_id`.

    Args:
        service_account: The service-account email, e.g.
            `my-sa@my-project.iam.gserviceaccount.com`.
        service_key: Path to the service-account JSON key file, or the
            JSON content as a string.
        project: Cloud project id to scope the Earth Engine calls to.
            If omitted, the key file's `project_id` is used.

    Raises:
        AuthenticationError: If the credentials are missing/invalid, no
            project can be determined, or the project is not registered
            for Earth Engine / not accessible to the service account.

    Examples:
        - Authenticate with a key file:

            ```python
            >>> auth = EarthEngineAuth(  # doctest: +SKIP
            ...     "my-sa@my-project.iam.gserviceaccount.com",
            ...     "/path/to/key.json",
            ... )
            ```
    """

    def __init__(
        self,
        service_account: str,
        service_key: str,
        project: str | None = None,
    ):
        """Authenticate and call `ee.Initialize`; see the class docstring.

        Args:
            service_account: The service-account email.
            service_key: Path to the service-account JSON key file, or
                the JSON content as a string.
            project: Cloud project id; if omitted, read from the key
                file's `project_id`.

        Raises:
            AuthenticationError: As described on :class:`EarthEngineAuth`.
        """
        self.service_account = service_account
        self.project = self.initialize(service_account, service_key, project)

    @staticmethod
    def initialize(
        service_account: str,
        service_key: str,
        project: str | None = None,
    ) -> str:
        """Authenticate the service account and call `ee.Initialize`.

        Args:
            service_account: The service-account email.
            service_key: Path to the service-account JSON key file, or
                the JSON content as a string.
            project: Cloud project id to scope the calls to. If omitted,
                the key file's `project_id` is used.

        Returns:
            The Cloud project id the connection was initialised with.

        Raises:
            AuthenticationError: If the key cannot be loaded, no project
                can be resolved, the project is not registered for Earth
                Engine, or the service account lacks permission on it.

        Examples:
            - Initialise from a key file (requires network + a registered project):
                ```python
                >>> EarthEngineAuth.initialize(  # doctest: +SKIP
                ...     "my-sa@my-project.iam.gserviceaccount.com",
                ...     "/path/to/key.json",
                ... )
                'my-project'

                ```
            - A key with no `project_id` and no explicit `project` fails fast:
                ```python
                >>> import json
                >>> bad_key = json.dumps({"type": "service_account"})
                >>> EarthEngineAuth.initialize("sa@x.iam", bad_key)  # doctest: +IGNORE_EXCEPTION_DETAIL
                Traceback (most recent call last):
                    ...
                earthlens.gee.auth.AuthenticationError: no Earth Engine Cloud project

                ```
        """
        key_dict = _load_key_dict(service_key)
        resolved_project = project or (key_dict or {}).get("project_id")
        if not resolved_project:
            raise AuthenticationError(
                "no Earth Engine Cloud project: pass project=, or use a "
                "service-account key file that includes a 'project_id' "
                f"field. See {_SERVICE_ACCOUNT_DOCS}."
            )

        try:
            credentials = ee.ServiceAccountCredentials(service_account, service_key)
        except ValueError:
            try:
                credentials = ee.ServiceAccountCredentials(
                    service_account, key_data=service_key
                )
            except Exception as exc:  # noqa: BLE001 - re-raised as AuthenticationError
                raise AuthenticationError(
                    "could not build service-account credentials from the "
                    f"supplied key (account={service_account!r}). Check that "
                    f"the key file/JSON is valid. See {_SERVICE_ACCOUNT_DOCS}."
                ) from exc

        try:
            ee.Initialize(credentials=credentials, project=resolved_project)
        except ee.EEException as exc:
            message = str(exc)
            if "not registered to use Earth Engine" in message:
                raise AuthenticationError(
                    f"Cloud project {resolved_project!r} is not registered "
                    f"to use Earth Engine. Register it at {_REGISTER_URL} "
                    "(pick the noncommercial track if eligible), then retry."
                ) from exc
            if (
                "does not have required permission" in message
                or "serviceUsageConsumer" in message
                or "PERMISSION_DENIED" in message
            ):
                raise AuthenticationError(
                    f"service account {service_account!r} cannot use project "
                    f"{resolved_project!r}: grant it the "
                    "'roles/serviceusage.serviceUsageConsumer' and "
                    "'roles/earthengine.viewer' IAM roles on that project."
                ) from exc
            raise AuthenticationError(
                f"Earth Engine initialisation failed for project "
                f"{resolved_project!r}: {message}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - re-raised as AuthenticationError
            raise AuthenticationError(
                f"Earth Engine initialisation failed for project "
                f"{resolved_project!r}: {exc}"
            ) from exc

        return resolved_project

    @staticmethod
    def encode_service_account(service_key_path: str) -> bytes:
        """Base64-encode a service-account JSON key file.

        Useful for shipping a key through an environment variable or CI
        secret without newlines.

        Args:
            service_key_path: Path to the service-account JSON key file.

        Returns:
            The base64-encoded JSON content as a byte string.

        Examples:
            - Encode a tiny key file and inspect the result:
                ```python
                >>> import json, os, tempfile
                >>> p = os.path.join(tempfile.mkdtemp(), "key.json")
                >>> _ = open(p, "w").write(json.dumps({"type": "service_account", "project_id": "demo"}))
                >>> blob = EarthEngineAuth.encode_service_account(p)
                >>> EarthEngineAuth.decode_service_account(blob)
                {'type': 'service_account', 'project_id': 'demo'}

                ```

        See Also:
            decode_service_account: The inverse operation.
        """
        content = json.loads(Path(service_key_path).read_text())
        return base64.b64encode(json.dumps(content).encode())

    @staticmethod
    def decode_service_account(service_key_bytes: bytes) -> dict[str, Any]:
        """Decode a base64-encoded service-account key back to a mapping.

        Inverse of :meth:`encode_service_account`.

        Args:
            service_key_bytes: The base64-encoded JSON content.

        Returns:
            The decoded service-account key as a dictionary.

        Examples:
            - Round-trip a key dict through encode then decode:
                ```python
                >>> import base64, json
                >>> blob = base64.b64encode(json.dumps({"client_email": "sa@p.iam", "project_id": "p"}).encode())
                >>> decoded = EarthEngineAuth.decode_service_account(blob)
                >>> decoded["client_email"]
                'sa@p.iam'
                >>> decoded["project_id"]
                'p'

                ```

        See Also:
            encode_service_account: The inverse operation.
        """
        return json.loads(base64.b64decode(service_key_bytes).decode())
