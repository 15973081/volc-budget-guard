import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlparse
import httpx


class VolcOpenAPI:
    def __init__(self, access_key: str, secret_key: str, region: str):
        if not access_key or not secret_key:
            raise RuntimeError("VOLC_ACCESS_KEY and VOLC_SECRET_KEY are required")
        self.access_key, self.secret_key, self.region = access_key, secret_key, region

    def request(
        self, endpoint: str, service: str, action: str, version: str,
        *, body: dict | None = None, query: dict | None = None,
    ) -> dict:
        method = "POST" if body is not None else "GET"
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        params = {"Action": action, "Version": version, **(query or {})}
        canonical_query = urlencode(
            sorted(params.items()), quote_via=quote, safe="-_.~"
        )
        now = datetime.now(timezone.utc)
        x_date, short_date = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        host = urlparse(endpoint).netloc
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        signed_headers = "host;x-content-sha256;x-date" if body is not None else "host;x-date"
        canonical_headers = f"host:{host}\n"
        if body is not None:
            canonical_headers += f"x-content-sha256:{payload_hash}\n"
        canonical_headers += f"x-date:{x_date}\n"
        canonical_request = "\n".join([
            method, "/", canonical_query, canonical_headers, signed_headers, payload_hash
        ])
        scope = f"{short_date}/{self.region}/{service}/request"
        string_to_sign = "\n".join([
            "HMAC-SHA256", x_date, scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])
        key = hmac.new(self.secret_key.encode(), short_date.encode(), hashlib.sha256).digest()
        for value in (self.region, service, "request"):
            key = hmac.new(key, value.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Host": host,
            "X-Date": x_date,
            "Authorization": (
                f"HMAC-SHA256 Credential={self.access_key}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}"
            ),
        }
        if body is not None:
            headers.update({
                "Content-Type": "application/json",
                "X-Content-Sha256": payload_hash,
            })
        response = httpx.request(
            method, endpoint, params=params, headers=headers,
            content=payload or None, timeout=30,
        )
        try:
            data = response.json()
        except ValueError:
            response.raise_for_status()
            raise
        error = data.get("ResponseMetadata", {}).get("Error")
        if error:
            raise RuntimeError(json.dumps(error, ensure_ascii=False))
        response.raise_for_status()
        return data
