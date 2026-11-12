import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "leadgen/0.2 (+lead research tool)"
RETRY_STATUS = {429, 500, 502, 503, 504}
_sleep = time.sleep  # patched in tests


def _open(req, timeout, retries=3):
    """urlopen with backoff on rate limits / transient server errors."""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_STATUS or attempt == retries:
                detail = e.read(300).decode("utf-8", "replace").strip()
                raise OSError(f"HTTP {e.code} from {urllib.parse.urlparse(req.full_url).netloc}: {detail}") from e
            _sleep(2 ** attempt * 2)
        except urllib.error.URLError as e:
            if attempt == retries or "403" in str(e.reason):  # proxy/network policy block: retrying won't help
                raise
            _sleep(2 ** attempt * 2)


def request_json(url, method="GET", headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    if data:
        h["Content-Type"] = "application/json"
    return _open(urllib.request.Request(url, data=data, headers=h, method=method), timeout)


def request_form_json(url, fields, timeout=90):
    """POST application/x-www-form-urlencoded, parse JSON response (used by Overpass)."""
    data = urllib.parse.urlencode(fields).encode()
    return _open(urllib.request.Request(url, data=data, headers={"User-Agent": UA}, method="POST"), timeout)
