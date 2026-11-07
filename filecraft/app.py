"""Loopback-only browser interface."""

import secrets
import tempfile
from pathlib import Path
from threading import Lock
from zipfile import ZIP_STORED, ZipFile

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from .convert import FORMATS, ToolError, convert, safe_name
from .media import MAX_OUTPUT, download, parse_link


MAX_UPLOAD = 64 * 1024 * 1024
MAX_UPLOAD_FILES = 20


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
        MAX_CONTENT_LENGTH=MAX_UPLOAD,
        MAX_FORM_PARTS=MAX_UPLOAD_FILES + 4,
    )
    token = secrets.token_urlsafe(32)
    worker = Lock()

    @app.before_request
    def require_local_token():
        if request.method == "POST" and not secrets.compare_digest(
            request.headers.get("X-Filecraft-Token", ""), token
        ):
            return jsonify(error="Reload Filecraft before submitting again."), 403

    @app.after_request
    def protect_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="Uploads must total less than 64 MB and include at most 20 files."), 413

    @app.get("/")
    def index():
        return render_template("index.html", token=token, formats=FORMATS)

    def run_tool(operation):
        if not worker.acquire(blocking=False):
            return jsonify(error="Another job is running. Try again when it finishes."), 409
        temporary = None
        try:
            temporary = tempfile.TemporaryDirectory(prefix="filecraft-", ignore_cleanup_errors=True)
            files, bundle_name = operation(Path(temporary.name))
            if sum(file.stat().st_size for file in files) > MAX_OUTPUT:
                raise ToolError("The result exceeds 200 MB. Choose fewer files or a lower DPI.")
            result = files[0]
            if len(files) > 1:
                result = Path(temporary.name) / f"{safe_name(bundle_name)}.zip"
                with ZipFile(result, "w", compression=ZIP_STORED) as archive:
                    for file in files:
                        archive.write(file, arcname=file.name)
            response = send_file(result, as_attachment=True, conditional=False, etag=False)
            response.direct_passthrough = False
            response.headers["X-Filecraft-Count"] = str(len(files))
            response.call_on_close(temporary.cleanup)
            temporary = None
            return response
        except ToolError as error:
            return jsonify(error=str(error)), 422
        except RequestEntityTooLarge:
            raise
        except Exception as error:
            app.logger.error("Tool failed (%s)", type(error).__name__)
            return jsonify(error="This file or link could not be processed. Check the input and try again."), 500
        finally:
            try:
                if temporary:
                    temporary.cleanup()
            finally:
                worker.release()

    @app.post("/api/convert")
    def convert_file():
        def operation(folder: Path):
            uploads = request.files.getlist("files")
            if not 1 <= len(uploads) <= MAX_UPLOAD_FILES:
                raise ToolError("Choose between 1 and 20 files.")
            target = request.form.get("target", "").lower()
            dpi = request.form.get("dpi", 300, type=int)
            destination = folder / "output"
            destination.mkdir()
            sources = []
            for index, upload in enumerate(uploads):
                name = Path((upload.filename or "").replace("\\", "/")).name
                path = Path(name)
                ext = path.suffix.lower()
                if ext[1:] not in FORMATS:
                    raise ToolError("Choose a PDF, JPG, PNG, WebP, XLSX, CSV, or TSV file.")
                source_dir = folder / str(index)
                source_dir.mkdir()
                source = source_dir / f"{safe_name(path.stem)}{ext}"
                upload.save(source)
                sources.append(source)
            outputs = convert(sources, target, destination, dpi)
            return outputs, f"{sources[0].stem}-{target}"

        return run_tool(operation)

    @app.post("/api/download")
    def download_media():
        def operation(folder: Path):
            if request.form.get("allowed") != "yes":
                raise ToolError("Confirm that you have permission to save this content.")
            link = parse_link(request.form.get("url", ""))
            upload = request.files.get("cookies")
            raw = upload.stream.read(256 * 1024 + 1) if upload and upload.filename else None
            return download(link, folder, raw), f"{link.platform}-media"

        return run_tool(operation)

    return app
