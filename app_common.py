"""Logique commune aux deux applications macOS (MagCard, ChipCard) :
fenêtre native pywebview autour du même serveur local, limitée à un seul mode."""
import os
import threading
from http.server import ThreadingHTTPServer

import webview


def run(mode, app_name):
    os.environ["CARDPRO_APP_MODE"] = mode
    os.environ["CARDPRO_APP_NAME"] = app_name
    import msr  # importé après la définition des variables d'environnement ci-dessus

    app_support = os.path.expanduser(f"~/Library/Application Support/{app_name}")

    def start_server():
        # port fixe (le stockage du navigateur est lié au port) ; on en essaie quelques-uns si occupé
        for port in range(8605, 8615):
            try:
                return ThreadingHTTPServer(("127.0.0.1", port), msr.Handler), port
            except OSError:
                continue
        raise SystemExit("Aucun port libre.")

    srv, port = start_server()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.makedirs(app_support, exist_ok=True)
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window(
        app_name, f"http://127.0.0.1:{port}/",
        width=1240, height=900, min_size=(900, 640), background_color="#0d1020",
    )
    webview.start(private_mode=False, storage_path=app_support)   # garde thème et modèles entre deux lancements
    srv.shutdown()
