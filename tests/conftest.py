"""Test setup: make the analytics/ and evaluation/ modules importable, and stub
the I/O-only dependencies (database, HTTP, CoAP) so the pure functions under test
can be imported without a running InfluxDB, network, or the heavier libraries."""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analytics"))
sys.path.insert(0, str(ROOT / "evaluation"))


def _stub(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)


# db does InfluxDB queries; the pure functions never call it, so a stub is enough.
_stub(
    "db",
    load_telemetry=lambda *a, **k: None,
    load_events=lambda *a, **k: None,
    load_sessions=lambda *a, **k: None,
)
# requests / aiocoap are only used by the live benchmark, not by summarise().
_stub("requests", Session=object, RequestException=Exception)
_stub("aiocoap", Message=object, POST=None, Context=object)
