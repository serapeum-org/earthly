"""Google earth engine main script."""
from __future__ import annotations

import base64
import json

import ee

# import os


class GEE:
    """GEE."""

    def __init__(self, service_account: str, service_key_path: str):
        """Initialize.

        Parameters
        ----------
        service_account: [str]
                service account name
        service_key_path: [str]
                path to the service account json file
        Returns
        -------
        None
        """
        self.initialize(service_account, service_key_path)

        pass

    @staticmethod
    def initialize(service_account: str, service_key: str):
        """Initialize.

            Initialize authenticate and initializes the connection to google earth engine with a service accont file
            content or path

        Parameters
        ----------
        service_account: [str]
                service account name
        service_key: [str]
                path to the service account json file or the content of the service account

        Returns
        -------
        None
        """
        try:
            credentials = ee.ServiceAccountCredentials(service_account, service_key)
        except ValueError:
            credentials = ee.ServiceAccountCredentials(
                service_account, key_data=service_key
            )
        ee.Initialize(credentials=credentials)

    @staticmethod
    def encodeServiceAccount(service_key_dir: str) -> bytes:
        """encodeServiceAccount.

            decodeServiceAccount decode the service account

        Parameters
        ----------
        service_key_dir: [str]

        Returns
        -------
        byte string
        """
        content = json.load(open(service_key_dir))
        dumped_service_account = json.dumps(content)
        encoded_service_account = base64.b64encode(dumped_service_account.encode())
        return encoded_service_account

    @staticmethod
    def decodeServiceAccount(service_key_bytes: bytes) -> str:
        """decodeServiceAccount.

            decodeServiceAccount

        Parameters
        ----------
        service_key_bytes: [bytes]
            the content of the service account encoded with base64

        Returns
        -------
        str:
            google cloud service account content
        """
        service_key = json.loads(base64.b64decode(service_key_bytes).decode())
        return service_key
