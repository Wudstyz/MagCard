#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Wudstyz
"""Lecture / écriture de cartes à piste magnétique avec un MSR605X (USB HID) sur macOS.

    msr.py gui                              interface web (http://127.0.0.1:8605)
    msr.py info                             firmware et coercivité du lecteur
    msr.py read                             lit une carte
    msr.py write --t1 .. --t2 .. --t3 ..    écrit une carte
    msr.py erase [--tracks 123]             efface des pistes
    msr.py hico | loco                      règle la coercivité (noire = hico, marron = loco)
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Permet de livrer deux applications distinctes (bande magnétique seule / puce seule)
# à partir du même code : fixés par le lanceur (app_mag.py / app_chip.py), "both" en développement.
# Chaque application n'installe/importe que la bibliothèque dont elle a réellement besoin.
APP_MODE = os.environ.get("CARDPRO_APP_MODE", "both")
APP_NAME = os.environ.get("CARDPRO_APP_NAME", "CardPro Studio")

hid = None
if APP_MODE in ("mag", "both"):
    import hid

chipmod = None
if APP_MODE in ("chip", "both"):
    import chip as chipmod

VID, PID = 0x0801, 0x0003
ESC = b"\x1b"
HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # _MEIPASS : application empaquetée
DEBUG = bool(os.environ.get("MSR_DEBUG"))  # journal des octets bruts (contient les données des cartes)
APP_SUBTITLE = {
    "mag": "Lecteur de cartes magnétiques",
    "chip": "Lecteur de cartes à puce",
}.get(APP_MODE, "Lecteur de cartes magnétiques et à puce")

STATUS = {
    0x31: "Erreur de lecture/écriture : repasse la carte plus lentement et bien à plat.",
    0x32: "Erreur de format de commande.",
    0x34: "Commande invalide.",
    0x39: "Erreur de passage : repasse la carte d'un geste régulier.",
}
TRACK1_OK = re.compile(r"^[\x20-\x5f]*$")      # ASCII 6 bits : majuscules, chiffres, ponctuation
TRACK23_OK = re.compile(r"^[0-9:;<=>?]*$")      # numérique


class MSRError(Exception):
    pass


class Cancelled(MSRError):
    pass


class LostResponse(Exception):
    """Le lecteur coupe parfois la liaison HID juste après une écriture ou un effacement."""


def _log(tag, b):
    if DEBUG:
        with open(os.path.join(os.path.expanduser("~/Library/Logs") if hasattr(sys, "_MEIPASS") else HERE, "msr_debug.log"), "a") as f:
            f.write(f"{tag} {bytes(b).rstrip(bytes(1)).hex(' ')}\n")


def connected() -> bool:
    return bool(hid and hid.enumerate(VID, PID))


class MSR605X:
    def __init__(self, cancel: threading.Event | None = None):
        self.cancel = cancel or threading.Event()
        self.tolerate_loss = False
        self.dev = hid.device()
        try:
            self.dev.open(VID, PID)
        except OSError:
            raise MSRError("Lecteur introuvable. Est-il bien branché en USB ?")
        self.reset()
        while self.dev.read(64, 200):  # vide d'éventuelles réponses en attente
            pass

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass

    # --- transport : trames de 64 octets, octet 0 = 0x80 (début) | 0x40 (fin) | longueur
    def _send(self, payload: bytes):
        chunks = [payload[i:i + 63] for i in range(0, len(payload), 63)] or [b""]
        for i, c in enumerate(chunks):
            flag = (0x80 if i == 0 else 0) | (0x40 if i == len(chunks) - 1 else 0) | len(c)
            frame = (bytes([flag]) + c).ljust(64, b"\x00")
            _log("TX", frame)
            self.dev.send_feature_report(b"\x00" + frame)

    def _recv(self, timeout_s: float) -> bytes:
        out, waited = b"", 0.0
        while True:
            if self.cancel.is_set():
                self.reset()
                raise Cancelled("Annulé.")
            try:
                data = self.dev.read(64, 250)
            except OSError:
                if self.tolerate_loss:
                    raise LostResponse
                time.sleep(0.2)
                waited += 0.2
                if waited >= timeout_s:
                    raise MSRError("Le lecteur ne répond plus (débranché ?).")
                continue
            if not data:
                waited += 0.25
                if waited >= timeout_s:
                    self.reset()
                    raise MSRError("Temps écoulé : aucune carte détectée.")
                continue
            _log("RX", data)
            out += bytes(data[1:1 + (data[0] & 0x3F)])
            if data[0] & 0x40:
                return out

    def _cmd(self, payload: bytes, timeout_s: float = 10) -> bytes:
        self._send(payload)
        return self._recv(timeout_s)

    @staticmethod
    def _status(resp: bytes):
        """Code statut final (ESC <code>) ou None."""
        return resp[-1] if len(resp) >= 2 and resp[-2:-1] == ESC else None

    # --- commandes simples
    def reset(self):
        self._send(ESC + b"a")

    def firmware(self) -> str:
        return self._cmd(ESC + b"v")[1:].decode(errors="replace").strip("\x00 ")

    def coercivity(self) -> str:
        return {b"h": "hico", b"l": "loco"}.get(self._cmd(ESC + b"d")[1:2], "?")

    def set_coercivity(self, kind: str):
        resp = self._cmd(ESC + (b"x" if kind == "hico" else b"y"))
        if self._status(resp) not in (0x30, None):
            raise MSRError("Impossible de changer la coercivité.")

    # --- lecture
    @staticmethod
    def parse_tracks(resp: bytes):
        """ESC s ESC 01 <t1> ESC 02 <t2> ESC 03 <t3> ? FS ESC <statut>
        -> ({1: texte, 2: ..., 3: ...}, [pistes en erreur])"""
        if not resp.startswith(ESC + b"s"):
            raise MSRError("Réponse inattendue du lecteur.")
        body = resp[2:].split(b"?\x1c")[0]
        parts = re.split(rb"\x1b([\x01\x02\x03])", body)
        tracks, errors = {1: "", 2: "", 3: ""}, []
        for i in range(1, len(parts) - 1, 2):
            n, d = parts[i][0], parts[i + 1]
            if d.startswith(ESC):                      # ESC + = piste vide, ESC * = erreur
                if d[1:2] == b"*":
                    errors.append(n)
                continue
            d = d.decode("latin-1")
            tracks[n] = d[1:-1] if d.endswith("?") else d   # retire sentinelles début/fin
        return tracks, errors

    def read(self):
        self.reset()
        resp = self._cmd(ESC + b"r", timeout_s=60)
        tracks, errors = self.parse_tracks(resp)
        code = self._status(resp)
        if code not in (0x30, None) and not any(tracks.values()):
            if errors == [1, 2, 3]:
                raise MSRError("Rien de lisible sur la carte (bande vierge, ou passage de travers).")
            raise MSRError(STATUS.get(code, f"Erreur du lecteur (0x{code:02x})."))
        return tracks, errors

    # --- écriture / effacement
    def _confirmed(self, payload: bytes) -> bool:
        """True si le lecteur a confirmé, False s'il a coupé la liaison (voyant vert = OK)."""
        self.tolerate_loss = True
        try:
            resp = self._cmd(payload, timeout_s=60)
            code = self._status(resp)
            if code not in (0x30, None):
                raise MSRError(STATUS.get(code, f"Erreur du lecteur (0x{code:02x})."))
            return True
        except LostResponse:
            return False
        finally:
            self.tolerate_loss = False

    def write(self, t1="", t2="", t3="") -> bool:
        validate(t1, t2, t3)
        self.reset()
        payload = (ESC + b"w" + ESC + b"s"
                   + ESC + b"\x01" + t1.encode("latin-1")
                   + ESC + b"\x02" + t2.encode("latin-1")
                   + ESC + b"\x03" + t3.encode("latin-1")
                   + b"?\x1c")
        return self._confirmed(payload)

    def erase(self, tracks="123") -> bool:
        mask = sum(1 << (int(t) - 1) for t in tracks)
        self.reset()
        return self._confirmed(ESC + b"c" + bytes([mask]))


def validate(t1, t2, t3):
    if len(t1) > 79 or not TRACK1_OK.match(t1):
        raise MSRError("Piste 1 : 79 caractères max, majuscules/chiffres/ponctuation (pas de minuscules ni d'accents).")
    if len(t2) > 40 or not TRACK23_OK.match(t2):
        raise MSRError("Piste 2 : 40 chiffres max (0-9).")
    if len(t3) > 107 or not TRACK23_OK.match(t3):
        raise MSRError("Piste 3 : 107 chiffres max (0-9).")


# --- serveur web ---------------------------------------------------------------
_busy = threading.Lock()
_cancel = threading.Event()


def run_action(action: str, b: dict) -> dict:
    """Exécute une action sur le lecteur (un seul à la fois)."""
    if not _busy.acquire(blocking=False):
        raise MSRError("Le lecteur est déjà en cours d'utilisation.")
    _cancel.clear()
    m = None
    try:
        m = MSR605X(_cancel)
        if action == "info":
            return {"msg": f"Lecteur connecté · firmware {m.firmware()}", "coercivity": m.coercivity()}
        if action == "leds":
            codes = {"off": 0x81, "all": 0x82, "green": 0x83, "yellow": 0x84, "red": 0x85}
            m._send(ESC + bytes([codes.get(b.get("color"), 0x81)]))
            time.sleep(float(b.get("seconds", 1.5)))
            m.reset()
            return {"msg": "Test des voyants terminé."}
        if action == "coercivity":
            m.set_coercivity("loco" if b.get("coercivity") == "loco" else "hico")
            return {"msg": "Coercivité réglée.", "coercivity": m.coercivity()}
        if action == "read":
            tracks, errors = m.read()
            msg = "Carte lue." if any(tracks.values()) else "Carte lue : les pistes sont vides."
            if errors:
                msg += f" Piste(s) illisible(s) : {', '.join(map(str, errors))}."
            return {"tracks": tracks, "msg": msg}
        if action in ("write", "erase"):
            m.set_coercivity("loco" if b.get("coercivity") == "loco" else "hico")
            if action == "write":
                t1 = b.get("t1", "").upper()
                ok = m.write(t1, b.get("t2", ""), b.get("t3", ""))
                return {"msg": "Carte écrite." if ok else "Écriture envoyée : voyant vert = réussi. Clique sur Lire pour vérifier."}
            ok = m.erase()
            return {"msg": "Carte effacée." if ok else "Effacement envoyé : voyant vert = réussi."}
        raise MSRError("Action inconnue.")
    finally:
        if m:
            m.close()
        _busy.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/chip/status":
            if not chipmod:
                return self._json({"error": "Fonctions puce non disponibles dans cette application."}, 404)
            return self._json(chipmod.chip.status())
        if self.path == "/api/status":
            return self._json({"connected": connected(), "busy": _busy.locked()})
        if self.path.split("?")[0] in ("/", "/index.html"):
            with open(os.path.join(HERE, "web", "index.html"), "rb") as f:
                page = f.read()
            page = (page.replace(b"__APP_NAME__", APP_NAME.encode())
                        .replace(b"__APP_SUBTITLE__", APP_SUBTITLE.encode())
                        .replace(b"__APP_MODE__", APP_MODE.encode()))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(page)
        self.send_error(404)

    def do_POST(self):
        action = self.path.rsplit("/", 1)[-1]
        if self.path.startswith("/api/chip/"):
            if not chipmod:
                return self._json({"error": "Fonctions puce non disponibles dans cette application."}, 404)
            try:
                n = int(self.headers.get("Content-Length", 0))
                b = json.loads(self.rfile.read(n) or b"{}")
                c = chipmod.chip
                if action == "apdu":
                    return self._json(c.transmit(chipmod.parse_hex(b.get("apdu", ""))))
                if action == "reset":
                    return self._json(c.reset())
                if action == "explore":
                    return self._json(c.explore())
                if action == "dump":
                    return self._json(c.dump(int(b.get("start", 0)), int(b.get("length", 256))))
                if action == "write_memory":
                    psc = chipmod.parse_hex(b["psc"]) if b.get("psc") else None
                    return self._json(c.write_memory(int(b.get("start", 0)), chipmod.parse_hex(b.get("data", "")), psc))
                if action == "update_binary":
                    return self._json(c.update_binary(int(b.get("sfi", 0)), int(b.get("offset", 0)), chipmod.parse_hex(b.get("data", ""))))
                if action == "pin":
                    return self._json(c.presented_pin(chipmod.parse_hex(b.get("pin", ""))))
                return self._json({"error": "Action inconnue."})
            except (chipmod.ChipError, ValueError) as e:
                return self._json({"error": str(e)})
            except Exception as e:
                return self._json({"error": f"Erreur inattendue : {e}"})
        if action == "cancel":
            _cancel.set()
            return self._json({"msg": "Annulation…"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            self._json(run_action(action, body))
        except MSRError as e:
            self._json({"error": str(e)})
        except Exception as e:
            self._json({"error": f"Erreur inattendue : {e}"})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gui")
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=8605)
    g.add_argument("--no-browser", action="store_true")
    sub.add_parser("read")
    w = sub.add_parser("write")
    for t in ("t1", "t2", "t3"):
        w.add_argument(f"--{t}", default="")
    e = sub.add_parser("erase")
    e.add_argument("--tracks", default="123")
    for c in ("info", "hico", "loco"):
        sub.add_parser(c)
    a = p.parse_args()

    if a.cmd == "gui":
        try:
            srv = ThreadingHTTPServer((a.host, a.port), Handler)
        except OSError:
            sys.exit(f"Le port {a.port} est déjà utilisé (l'interface tourne peut-être déjà).")
        url = f"http://{a.host}:{a.port}"
        print(f"Interface : {url}   (Ctrl+C pour quitter)")
        if not a.no_browser:
            webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return

    try:
        m = MSR605X()
        try:
            if a.cmd == "info":
                print("Firmware :", m.firmware(), "| Coercivité :", m.coercivity())
            elif a.cmd == "read":
                print("Passe la carte…")
                tracks, errors = m.read()
                for n, d in tracks.items():
                    print(f"Piste {n}: {d}" + ("  (illisible)" if n in errors else ""))
            elif a.cmd == "write":
                print("Passe la carte…")
                ok = m.write(a.t1.upper(), a.t2, a.t3)
                print("Écrit." if ok else "Envoyé (voyant vert = OK). Vérifie avec « read ».")
            elif a.cmd == "erase":
                print("Passe la carte…")
                ok = m.erase(a.tracks)
                print("Effacé." if ok else "Envoyé (voyant vert = OK).")
            else:
                m.set_coercivity(a.cmd)
                print("Coercivité :", m.coercivity())
        finally:
            m.close()
    except MSRError as e:
        sys.exit(f"Erreur : {e}")


if __name__ == "__main__":
    main()
