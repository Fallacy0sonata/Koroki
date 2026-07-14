"""DAVE E2EE decrypt shim for discord-ext-voice-recv (2026-07-10).

Discord enforced end-to-end voice encryption (DAVE / MLS) around 2026-03.
discord.py speaks it for SENDING (voice_state.dave_session), but voice-recv
0.5.2 predates it and only does the TRANSPORT decrypt — the opus frame inside
is still DAVE-encrypted, so the opus decoder gets garbage and throws OpusError.
That is exactly why Koroki went deaf mid-rehearsal.

The fix, without editing the installed library (survives reinstalls): patch
AudioReader.__init__ once. `decrypt_rtp` is NOT a class method — the decryptor
binds a mode-specific one per instance in its own __init__ (..._rtpsize etc.),
so we wrap that instance attribute after the reader is built, capturing the
reader in a closure (which reaches the voice client -> dave_session + ssrc map).
After the normal transport decrypt, if the client has a READY dave_session, the
frame goes through session.decrypt(user_id, audio, data). Any failure (silence
frames, passthrough packets, not-yet-negotiated) falls back to the transport
bytes, so behaviour on a non-E2EE server is byte-identical to today.

Idempotent: install_dave_shim() is safe to call repeatedly. Returns True if the
shim is active (davey present), False if this build has no DAVE support.
"""

from __future__ import annotations

import logging
import weakref

logger = logging.getLogger("koroki.ears.dave")

_INSTALLED = False


def dave_decrypt(session, voice_client, packet, transport_data: bytes, audio_media) -> bytes:
    """Route one transport-decrypted packet through DAVE, or pass it through.

    Extracted from the wrapper so the decision logic is unit-testable without a
    live voice connection (the full path needs a real DAVE-negotiated VC).
    Returns DAVE-decrypted bytes when the session is ready and the speaker is
    known; otherwise the transport bytes unchanged. Never raises."""
    if session is None or not getattr(session, "ready", False):
        return transport_data  # non-E2EE server or DAVE not negotiated yet
    try:
        uid = voice_client._get_id_from_ssrc(packet.ssrc)
        if not uid:
            return transport_data  # unknown speaker (silence bootstrap)
        return session.decrypt(int(uid), audio_media, bytes(transport_data))
    except Exception:
        # silence/passthrough frames + transient MLS-epoch gaps land here;
        # transport bytes keep the stream flowing (opus tolerates the odd bad
        # frame far better than a hard raise).
        return transport_data


def install_dave_shim() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True

    try:
        import davey
    except ImportError:
        logger.info("davey not installed — DAVE shim skipped (E2EE servers will be deaf)")
        return False

    from discord.ext.voice_recv import reader as _reader_mod

    AudioReader = _reader_mod.AudioReader
    audio_media = davey.MediaType.audio
    _orig_reader_init = AudioReader.__init__

    def _reader_init(self, *args, **kwargs):
        _orig_reader_init(self, *args, **kwargs)
        dec = getattr(self, "decryptor", None)
        if dec is None or getattr(dec, "_koroki_dave_wrapped", False):
            return
        reader_ref = weakref.ref(self)
        transport_decrypt = dec.decrypt_rtp  # instance-bound, mode-specific

        def _wrapped(packet):
            data = transport_decrypt(packet)  # transport layer (unchanged)
            reader = reader_ref()
            if reader is None:
                return data
            vc = reader.voice_client
            session = getattr(getattr(vc, "_connection", None), "dave_session", None)
            return dave_decrypt(session, vc, packet, data, audio_media)

        dec.decrypt_rtp = _wrapped
        dec._koroki_dave_wrapped = True

    AudioReader.__init__ = _reader_init
    _INSTALLED = True
    logger.info("DAVE decrypt shim installed (voice-recv E2EE listening enabled)")
    return True
