import requests
import os
import json
import hashlib
import hmac
import datetime
import urllib.parse

VOLC_ACCESS_KEY = os.getenv("VOLC_ACCESS_KEY", "")
VOLC_SECRET_KEY = os.getenv("VOLC_SECRET_KEY", "")

VOLC_HOST = "visual.volcengineapi.com"
VOLC_REGION = "cn-north-1"
VOLC_SERVICE = "cv"
VOLC_VERSION = "2022-08-31"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def test_jimeng():
    action = "CVSync2AsyncSubmitTask"
    body = json.dumps({
        "req_key": "jimeng_t2i_v40",
        "prompt": "a cute cat",
        "force_single": True,
        "width": 512,
        "height": 512,
    }).encode("utf-8")

    now = datetime.datetime.utcnow()
    date_str = now.strftime("%Y%m%d")
    datetime_str = now.strftime("%Y%m%dT%H%M%SZ")

    query_string = f"Action={action}&Version={VOLC_VERSION}"
    canonical_headers = (
        f"content-type:application/json\n"
        f"host:{VOLC_HOST}\n"
        f"x-date:{datetime_str}\n"
    )
    signed_headers = "content-type;host;x-date"
    payload_hash = hashlib.sha256(body).hexdigest()

    canonical_request = "\n".join([
        "POST", "/", query_string,
        canonical_headers, signed_headers, payload_hash,
    ])

    credential_scope = f"{date_str}/{VOLC_REGION}/{VOLC_SERVICE}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256", datetime_str, credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])

    signing_key = _hmac_sha256(
        _hmac_sha256(
            _hmac_sha256(
                _hmac_sha256(VOLC_SECRET_KEY.encode(), date_str),
                VOLC_REGION,
            ),
            VOLC_SERVICE,
        ),
        "request",
    )
    signature = hmac.new(
        signing_key,
        string_to_sign.encode(),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Authorization": (
            f"HMAC-SHA256 Credential={VOLC_ACCESS_KEY}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        ),
        "X-Date": datetime_str,
        "Content-Type": "application/json",
        "Host": VOLC_HOST,
    }

    url = f"https://{VOLC_HOST}?Action={action}&Version={VOLC_VERSION}"
    print(f"请求URL: {url}")
    print(f"X-Date: {datetime_str}")

    resp = requests.post(url, data=body, headers=headers, timeout=30)
    print(f"状态码: {resp.status_code}")
    print(f"返回结果: {json.dumps(resp.json(), ensure_ascii=False, indent=2)}")


if __name__ == "__main__":
    test_jimeng()
