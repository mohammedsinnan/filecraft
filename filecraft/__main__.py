import argparse
import webbrowser

from waitress import create_server

from .app import MAX_UPLOAD, create_app


def main():
    parser = argparse.ArgumentParser(description="Run Filecraft on your computer.")
    parser.add_argument("--port", default=8770, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        server = create_server(create_app(), host="127.0.0.1", port=args.port, threads=4,
                               max_request_body_size=MAX_UPLOAD)
    except OSError as error:
        parser.exit(1, f"Could not start Filecraft on port {args.port}. Choose another port with --port. ({error})\n")
    url = f"http://127.0.0.1:{args.port}"
    print(f"Filecraft is ready at {url}\nKeep this window open while you use the app.", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.run()
    except KeyboardInterrupt:
        server.close()


if __name__ == "__main__":
    main()
