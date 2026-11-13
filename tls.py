"""TLS context with a CA bundle that works on python.org Python builds.

Those builds ship without CA certs (CERTIFICATE_VERIFY_FAILED). Fall back to
certifi if installed, else macOS's /etc/ssl/cert.pem. Never disables verification.
"""
import ssl
from pathlib import Path


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and Path("/etc/ssl/cert.pem").exists():
        ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return ctx
