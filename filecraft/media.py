"""Download media from individual Instagram and TikTok links."""

import http.cookiejar
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import imageio_ffmpeg

from .convert import ToolError


MAX_OUTPUT = 200 * 1024 * 1024
MAX_ITEMS = 40
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".avif", ".heic", ".gif",
    ".mp4", ".mkv", ".webm", ".mov", ".m4v", ".m4a", ".mp3", ".aac", ".opus",
}
INSTAGRAM_HOSTS = {"www.instagram.com", "instagram.com", "m.instagram.com"}
TIKTOK_HOSTS = {"www.tiktok.com", "tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"


@dataclass(frozen=True)
class Link:
    url: str
    platform: str
    kind: str


def parse_link(value: str) -> Link:
    value = value.strip()
    invalid = "Paste a valid HTTPS link to an Instagram post, Reel or Story, or a TikTok post."
    if len(value) > 4096 or re.search(r"[\x00-\x20\x7f\\]", value):
        raise ToolError(invalid)
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.netloc.lower() != parts.hostname or parts.port is not None:
            raise ToolError(invalid)
    except ValueError as exc:
        raise ToolError(invalid) from exc
    path = parts.path.rstrip("/")
    if parts.hostname in INSTAGRAM_HOSTS:
        base = "https://www.instagram.com"
        post = re.fullmatch(r"/(?:(?:[a-zA-Z0-9_.]+)/)?(p|reels?|tv)/([\w-]+)", path, re.ASCII)
        if post:
            kind = "post" if post[1] == "p" else "reel"
            route = "reel" if post[1] == "reels" else post[1]
            return Link(f"{base}/{route}/{post[2]}/", "instagram", kind)
        if re.fullmatch(r"/stories/[a-zA-Z0-9_.]{1,30}(?:/[0-9]+)?", path):
            if path in {"/stories/me", "/stories/highlights"} or path.startswith("/stories/highlights/"):
                raise ToolError("Paste an individual Story link or a creator's active Stories link.")
            return Link(base + path + "/", "instagram", "story")
        if re.fullmatch(r"/share/(?:(?:p|reel)/)?[a-zA-Z0-9_-]+", path):
            return Link(base + path + "/", "instagram", "share")
    if parts.hostname in TIKTOK_HOSTS:
        if re.fullmatch(r"/@[a-zA-Z0-9_.]+/(?:video|photo)/[0-9]+", path):
            kind = "photo" if "/photo/" in path else "video"
            return Link("https://www.tiktok.com" + path, "tiktok", kind)
        if (parts.hostname in {"vm.tiktok.com", "vt.tiktok.com"} and
                re.fullmatch(r"/[a-zA-Z0-9]+", path)):
            return Link(f"https://{parts.hostname}{path}/", "tiktok", "share")
        if re.fullmatch(r"/t/[a-zA-Z0-9]+", path):
            return Link("https://www.tiktok.com" + path + "/", "tiktok", "share")
    raise ToolError(invalid)


class ShareRedirect(HTTPRedirectHandler):
    max_redirections = 4

    def __init__(self, platform: str):
        self.platform = platform

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = parse_link(urljoin(req.full_url, newurl))
        if target.platform != self.platform:
            raise ToolError("This share link redirected away from its platform.")
        if target.kind != "share":
            # Stop once the permalink is known. The extractor fetches the actual page.
            raise ResolvedLink(target)
        return super().redirect_request(req, fp, code, msg, headers, target.url)


class ResolvedLink(Exception):
    def __init__(self, link: Link):
        self.link = link


def resolve_link(link: Link) -> Link:
    if link.kind != "share":
        return link
    opener = build_opener(ShareRedirect(link.platform))
    try:
        with opener.open(Request(link.url, headers={"User-Agent": USER_AGENT}), timeout=8):
            pass
    except ResolvedLink as result:
        return result.link
    except (HTTPError, URLError, TimeoutError, ToolError) as exc:
        raise ToolError("This share link could not be opened. Copy the full post or Reel link and try again.") from exc
    raise ToolError("This share link did not resolve to a post. Paste its full link instead.")


def prepare_cookies(raw: bytes, platform: str, path: Path) -> Path:
    if len(raw) > 256 * 1024:
        raise ToolError("Choose a platform-only Netscape cookie file under 256 KB.")
    # Some exporters use 0 for a session cookie; MozillaCookieJar expects a blank expiry.
    lines = []
    for line in raw.splitlines():
        fields = line.split(b"\t")
        if len(fields) == 7 and fields[4] == b"0":
            fields[4] = b""
        lines.append(b"\t".join(fields))
    path.write_bytes(b"\n".join(lines) + b"\n")
    source = http.cookiejar.MozillaCookieJar(path)
    try:
        source.load(ignore_discard=True)
        result = http.cookiejar.MozillaCookieJar(path)
        domain = f"{platform}.com"
        for cookie in source:
            host = cookie.domain.lstrip(".").lower()
            if host == domain or host.endswith("." + domain):
                result.set_cookie(cookie)
        if not result:
            raise ToolError("The file contains no current cookies for this platform.")
        result.save(ignore_discard=True)
    except (http.cookiejar.LoadError, UnicodeError) as exc:
        raise ToolError("Choose a valid Netscape-format cookies.txt file.") from exc
    return path


def build_command(engine: str, link: Link, folder: Path, cookies: Path | None) -> list[str]:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    if engine == "yt_dlp":
        args = [
            "--ignore-config", "--no-cache-dir", "--no-playlist", "--quiet", "--no-progress",
            "--socket-timeout", "12", "--retries", "1", "--fragment-retries", "1",
            "--max-filesize", "200M", "--ffmpeg-location", ffmpeg,
            "--format", "bv*+ba/b", "--no-mtime", "--windows-filenames",
            "--output", str(folder / f"{link.platform}-%(id)s.%(ext)s"),
        ]
    else:
        filename = (
            "instagram-{post_shortcode}-{num:>02}-{media_id}.{extension}"
            if link.platform == "instagram" else "tiktok-{id}-{num:>02}-{type}.{extension}"
        )
        args = [
            "--config-ignore", "--quiet", "--directory", str(folder), "--filename", filename,
            "--windows-filenames",
            "--range", f"1-{MAX_ITEMS + 1}",
            "-o", "cache.file=:memory:", "-o", "extractor.timeout=12", "-o", "extractor.retries=1",
            "-o", "extractor.instagram.videos=dash", "-o", "extractor.instagram.static-videos=false",
            "-o", "extractor.tiktok.photos=true", "-o", "extractor.tiktok.audio=true",
            "-o", "downloader.http.filesize-max=200M",
            "-o", "downloader.ytdl.filesize-max=200M",
            "-o", "downloader.ytdl.format=bv*+ba/b",
            "-o", "downloader.ytdl.raw-options.cachedir=false",
            "-o", f"downloader.ytdl.raw-options.ffmpeg_location={ffmpeg}",
        ]
    if cookies:
        args += ["--cookies", str(cookies)]
    return [sys.executable, "-m", engine, *args, "--", link.url]


def current_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except FileNotFoundError:
            # The downloader may rename a partial file while this loop runs.
            continue
    return total


def run_engine(command: list[str], folder: Path, deadline: float) -> tuple[list[Path], str]:
    with (folder.parent / f"{folder.name}.log").open("w+b") as log:
        with subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, cwd=folder,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as job:
            try:
                while job.poll() is None:
                    if time.monotonic() > deadline:
                        raise ToolError("The platform took too long to respond. Try again in a moment.")
                    # Allow temporary streams and their merged copy to coexist.
                    if current_size(folder) > MAX_OUTPUT * 2:
                        raise ToolError("The download exceeds the 200 MB result limit.")
                    time.sleep(0.35)
            finally:
                if job.poll() is None:
                    job.kill()
        log.seek(max(0, log.seek(0, 2) - 16384))
        diagnostic = log.read().decode("utf-8", errors="replace").lower()
    files = sorted(path for path in folder.rglob("*") if path.is_file() and
                   path.suffix.lower() in MEDIA_EXTENSIONS and path.stat().st_size)
    if any(path.suffix == ".part" for path in folder.rglob("*")):
        return [], diagnostic
    return (files if job.returncode == 0 else []), diagnostic


def download(link: Link, folder: Path, cookie_bytes: bytes | None = None) -> list[Path]:
    link = resolve_link(link)
    if link.kind == "story" and not cookie_bytes:
        raise ToolError("Instagram Stories need a cookie file from a signed-in session that can view them.")
    cookies = prepare_cookies(cookie_bytes, link.platform, folder / ".session.txt") if cookie_bytes else None
    try:
        if link.kind in {"photo", "story"}:
            engines = ("gallery_dl",)
        elif link.kind == "post":
            # A post can contain photos or a mixed carousel. Preserve every item.
            engines = ("gallery_dl",)
        else:
            engines = ("yt_dlp", "gallery_dl")
        diagnostics = []
        deadline = time.monotonic() + 150
        for engine in engines:
            destination = folder / engine
            destination.mkdir()
            files, diagnostic = run_engine(build_command(engine, link, destination, cookies), destination, deadline)
            if files:
                if len(files) > MAX_ITEMS or sum(path.stat().st_size for path in files) > MAX_OUTPUT:
                    raise ToolError(f"The result exceeds {MAX_ITEMS} files or 200 MB. Try an individual item.")
                return files
            diagnostics.append(diagnostic)
            if "429" in diagnostic:
                break
    finally:
        if cookies:
            cookies.unlink(missing_ok=True)

    logs = "\n".join(diagnostics)
    if "429" in logs:
        raise ToolError("The platform is rate-limiting this connection. Wait a while and try again.")
    if any(word in logs for word in ("max-filesize", "filesize-max", "file is larger")):
        raise ToolError("The download exceeds the 200 MB result limit.")
    if any(word in logs for word in ("cookie", "login", "log in", "sign in", "403", "private")):
        raise ToolError("The platform denied access. Check that the link opens for you and try a current session cookie file.")
    raise ToolError("No media was returned. The post may have expired, been removed, or been blocked by the platform.")
