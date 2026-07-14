"""Contract tests for the DAVE decrypt routing (ears_dave_shim.dave_decrypt).

Proves the decision logic without a live voice connection: ready session ->
decrypt; not-ready / missing -> passthrough; unknown speaker -> passthrough;
decrypt raising -> fall back to transport bytes (never deafen on one bad frame).
"""

from ears_dave_shim import dave_decrypt

_AUDIO = object()  # stand-in for davey.MediaType.audio


class _Packet:
    def __init__(self, ssrc=111):
        self.ssrc = ssrc


class _VC:
    def __init__(self, uid):
        self._uid = uid

    def _get_id_from_ssrc(self, ssrc):
        return self._uid


class _Session:
    def __init__(self, ready=True, raises=False):
        self.ready = ready
        self._raises = raises
        self.calls = []

    def decrypt(self, uid, media, data):
        if self._raises:
            raise RuntimeError("bad MLS epoch")
        self.calls.append((uid, media, data))
        return b"DECRYPTED:" + data


def test_ready_session_decrypts():
    s = _Session(ready=True)
    out = dave_decrypt(s, _VC(42), _Packet(), b"opusframe", _AUDIO)
    assert out == b"DECRYPTED:opusframe"
    assert s.calls == [(42, _AUDIO, b"opusframe")]


def test_no_session_passthrough():
    assert dave_decrypt(None, _VC(42), _Packet(), b"raw", _AUDIO) == b"raw"


def test_not_ready_session_passthrough():
    s = _Session(ready=False)
    assert dave_decrypt(s, _VC(42), _Packet(), b"raw", _AUDIO) == b"raw"
    assert s.calls == []  # never touched decrypt before negotiation


def test_unknown_speaker_passthrough():
    # ssrc not yet mapped to a user (silence bootstrap) -> leave bytes alone
    assert dave_decrypt(_Session(ready=True), _VC(None), _Packet(), b"raw", _AUDIO) == b"raw"


def test_decrypt_exception_falls_back_to_transport():
    # a silence/passthrough frame or transient epoch gap must NOT deafen her
    s = _Session(ready=True, raises=True)
    assert dave_decrypt(s, _VC(42), _Packet(), b"raw", _AUDIO) == b"raw"


def test_install_is_idempotent_and_safe():
    from ears_dave_shim import install_dave_shim

    # davey is installed in this env -> True; second call must not re-patch
    first = install_dave_shim()
    second = install_dave_shim()
    assert first == second
