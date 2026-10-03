import base64
import hashlib
import hmac

from xrpbot.exchange.auth import NonceGenerator, endpoint_path_for_signing, sign_challenge, sign_rest

SECRET = base64.b64encode(b"super-secret-key-for-tests").decode()


def reference_sign(secret, path, data, nonce):
    sha = hashlib.sha256((data + nonce + path).encode()).digest()
    return base64.b64encode(hmac.new(base64.b64decode(secret), sha, hashlib.sha512).digest()).decode()


def test_strips_derivatives_prefix():
    assert endpoint_path_for_signing("/derivatives/api/v3/sendorder") == "/api/v3/sendorder"
    assert endpoint_path_for_signing("/api/v3/sendorder") == "/api/v3/sendorder"


def test_sign_rest_matches_documented_algorithm():
    data = "orderType=lmt&symbol=PF_XRPUSD&side=buy&size=10&limitPrice=0.5"
    got = sign_rest(SECRET, "/derivatives/api/v3/sendorder", data, "1700000000000")
    assert got == reference_sign(SECRET, "/api/v3/sendorder", data, "1700000000000")


def test_signature_changes_with_any_input():
    base = sign_rest(SECRET, "/derivatives/api/v3/sendorder", "a=1", "1")
    assert base != sign_rest(SECRET, "/derivatives/api/v3/sendorder", "a=2", "1")
    assert base != sign_rest(SECRET, "/derivatives/api/v3/sendorder", "a=1", "2")
    assert base != sign_rest(SECRET, "/derivatives/api/v3/cancelorder", "a=1", "1")


def test_sign_challenge():
    ch = "c100b894-1729-464d-ace1-52dbce11db42"
    sha = hashlib.sha256(ch.encode()).digest()
    exp = base64.b64encode(hmac.new(base64.b64decode(SECRET), sha, hashlib.sha512).digest()).decode()
    assert sign_challenge(SECRET, ch) == exp


def test_nonce_strictly_increasing():
    g = NonceGenerator()
    vals = [int(g.next()) for _ in range(1000)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
