import base64
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _default_base_url(env: str) -> str:
    env = (env or "demo").lower()
    if env == "prod":
        # OpenAPI server example includes api.elections.kalshi.com; Kalshi has multiple domains.
        # Allow override via KALSHI_BASE_URL.
        return "https://api.elections.kalshi.com/trade-api/v2"
    return "https://demo-api.kalshi.co/trade-api/v2"


@dataclass
class KalshiAuth:
    key_id: str
    private_key_pem_path: str

    def _load_private_key(self):
        with open(self.private_key_pem_path, "rb") as f:
            data = f.read()
        return serialization.load_pem_private_key(data, password=None)

    def sign(self, method: str, path: str, timestamp: str) -> str:
        """Create Kalshi signature header.

        Format per Kalshi docs:
          message = timestamp + HTTP_METHOD + path_without_query
        Signed with RSA-PSS + SHA256, then base64 encoded.

        `path` must include the `/trade-api/v2/...` prefix.
        """
        path_wo_query = path.split("?", 1)[0]
        msg = (f"{timestamp}{method.upper()}{path_wo_query}").encode("utf-8")
        key = self._load_private_key()
        sig = key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("ascii")


class KalshiClient:
    def __init__(self, base_url: str, auth: Optional[KalshiAuth] = None, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "KalshiClient":
        # Ensure repo config/.env is loaded even when kalshi_client is used directly.
        base_dir = os.path.dirname(os.path.abspath(__file__))
        repo_dir = os.path.abspath(os.path.join(base_dir, ".."))
        # Force-load repo config to avoid stale exported env vars causing auth failures.
        load_dotenv(os.path.join(repo_dir, "config", ".env"), override=True)

        env = os.getenv("KALSHI_ENV", "demo")
        base_url = os.getenv("KALSHI_BASE_URL", "") or _default_base_url(env)
        key_id = os.getenv("KALSHI_KEY_ID", "")
        key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")

        auth = None
        if key_id and key_path:
            auth = KalshiAuth(key_id=key_id, private_key_pem_path=key_path)

        return cls(base_url=base_url, auth=auth)

    def _headers(self, method: str, sign_path: str) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if not self.auth:
            return headers

        ts = str(int(time.time() * 1000))
        sig = self.auth.sign(method=method, path=sign_path, timestamp=ts)

        headers.update(
            {
                "KALSHI-ACCESS-KEY": self.auth.key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": sig,
            }
        )
        return headers

    def _sign_path(self, path: str) -> str:
        """Return the full path (including /trade-api/v2 prefix) used for signing."""
        # base_url is like https://demo-api.kalshi.co/trade-api/v2
        # so its path is /trade-api/v2
        from urllib.parse import urlparse

        base_path = urlparse(self.base_url).path.rstrip("/")
        return base_path + path

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self.base_url + path
        sign_path = self._sign_path(path)
        r = requests.get(url, params=params, headers=self._headers("GET", sign_path), timeout=self.timeout)
        if r.status_code >= 400:
            # include response body for debugging (may include reason)
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {r.url} :: {r.text[:400]}", response=r)
        return r.json()

    def post(self, path: str, json: Optional[Dict[str, Any]] = None) -> Any:
        url = self.base_url + path
        sign_path = self._sign_path(path)
        r = requests.post(url, json=json, headers=self._headers("POST", sign_path), timeout=self.timeout)
        if r.status_code >= 400:
            raise requests.HTTPError(f"{r.status_code} {r.reason} for url: {r.url} :: {r.text[:400]}", response=r)
        return r.json() if r.content else {}
