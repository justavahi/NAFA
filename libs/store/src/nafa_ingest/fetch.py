import time
import requests

def fetch_url(url, *, headers=None, timeout=20, retries=4, backoff=1.0):
    """GET with bounded exponential retries. Example: body=fetch_url(url, headers=ua)."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.content, response.url, response.headers.get("Content-Type", "")
        except requests.RequestException as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(backoff * (2 ** attempt))
    raise last_error
