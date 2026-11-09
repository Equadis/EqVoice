"""Session identity: who this client is to the signalling server, for one game login only.

The game tells us the character and realm (never typed by hand) and a random login id that changes every time
the game starts a session. From those we make:

    sid    22 random base64url characters, chosen here: the ephemeral id other clients see in the roster
    key    an Ed25519 key that signs the registration, so only this client can take the name back
    token  issued by the server at registration; lets a restarted client resume within the lease

All three are kept on disk only so a restart of THIS program inside the same game login keeps its name and
connections' identity. A different character, realm or login id gets all-new values, and the file is ignored
after MAX_KEY_AGE. Nothing links one play session to the next.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

REG_CONTEXT = b"OV2-REG\x00"      # must equal eqvoice_signalling.hub.REG_CONTEXT
MAX_KEY_AGE = 15 * 60


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_sid() -> str:
    return b64u(secrets.token_bytes(16))


def registration_message(nonce: bytes, sid: str, realm: str, character: str) -> bytes:
    return (REG_CONTEXT + nonce + b"\x00" + sid.encode("ascii") + b"\x00" + realm.encode("utf-8")
            + b"\x00" + character.encode("utf-8"))


@dataclass
class Identity:
    realm: str
    character: str
    login_id: int
    sid: str
    key: Ed25519PrivateKey
    token: str = ""
    created: float = field(default_factory=time.time)

    @property
    def game(self) -> tuple:
        return (self.realm, self.character, self.login_id)

    @property
    def public_b64(self) -> str:
        return b64u(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def registration(self, nonce_b64: str) -> tuple:
        """(pub, sig) to put in a ``register`` message answering the server's nonce."""
        sig = self.key.sign(registration_message(unb64u(nonce_b64), self.sid, self.realm, self.character))
        return self.public_b64, b64u(sig)


def _path(home: Path) -> Path:
    return Path(home) / "session_key.json"


def load_or_create(home: Path, realm: str, character: str, login_id: int, now=time.time) -> Identity:
    """The identity for this game login: the saved one if it is the same login and recent, else a fresh one."""
    path = _path(home)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if (d.get("realm") == realm and d.get("character") == character and d.get("login_id") == login_id
                and now() - float(d.get("created", 0)) < MAX_KEY_AGE):
            key = Ed25519PrivateKey.from_private_bytes(unb64u(d["key"]))
            return Identity(realm, character, login_id, str(d["sid"]), key, str(d.get("token", "")), float(d["created"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    ident = Identity(realm, character, login_id, new_sid(), Ed25519PrivateKey.generate(), "", now())
    save(home, ident)
    return ident


def save(home: Path, ident: Identity) -> None:
    raw = ident.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    path = _path(home)
    tmp = path.with_suffix(".tmp")
    try:
        Path(home).mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"realm": ident.realm, "character": ident.character, "login_id": ident.login_id,
                                   "sid": ident.sid, "key": b64u(raw), "token": ident.token,
                                   "created": ident.created}), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except OSError:
        pass                      # an identity that cannot be saved still works; it just cannot be resumed


def forget(home: Path) -> None:
    """Called on a clean logout: nothing about this login should outlive it."""
    try:
        _path(home).unlink()
    except OSError:
        pass


def peek(home: Path) -> Optional[dict]:
    try:
        return json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
