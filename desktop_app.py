import os
import sys
import time
import socket
import threading
import webbrowser
from server import app

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def start_server():
    port = 5050
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)

def main():
    port = 5050
    url = f"http://127.0.0.1:{port}"

    if not is_port_in_use(port):
        server_thread = threading.Thread(target=start_server, daemon=True)
        server_thread.start()

        # Wait for server to come up
        for _ in range(30):
            if is_port_in_use(port):
                break
            time.sleep(0.2)

    # Try launching native pywebview window
    try:
        import webview
        webview.create_window(
            title="Local Video Studio Pro | AI Automation Suite",
            url=url,
            width=1420,
            height=940,
            min_size=(1080, 700),
            background_color="#0b0d11",
            text_select=True
        )
        webview.start()
    except Exception as e:
        print(f"pywebview GUI error: {e}. Opening in default system browser instead.")
        webbrowser.open(url)
        # Keep process alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
