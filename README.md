# Filecraft

Filecraft is a local app for converting files and saving media from Instagram or TikTok links. It opens in your browser and runs on your computer.

## Start

On Windows, install [Python 3.11 or later](https://www.python.org/downloads/windows/) and double-click **`launch.cmd`**. The first launch installs the pinned dependencies. Keep the launcher window open and visit [http://127.0.0.1:8770](http://127.0.0.1:8770).

To run it from a terminal on macOS, Linux or Windows:

```sh
python -m venv .venv
```

Activate the environment with `.venv\Scripts\activate` in Windows Command Prompt or `source .venv/bin/activate` in a Unix shell. Then run:

```sh
python -m pip install -r requirements.txt
python -m filecraft
```

Run `python -m filecraft --port 8771 --no-browser` to choose a different port and open the browser manually. The server binds to the local computer. GitHub stores the project source, while the launcher runs its Python backend.

## Convert files

| Input | Output | Details |
| --- | --- | --- |
| PDF | JPG, PNG | 150, 300 or 450 DPI. Multi-page results come as a ZIP. JPG uses quality 100 with no chroma subsampling. PNG stores the rendered pixels losslessly. |
| PDF | XLSX, TXT | XLSX extracts each detected table with visible borders to a separate worksheet. TXT extracts selectable text. Scanned PDFs require OCR before text or table extraction. |
| JPG, PNG, WebP | PDF | One image per page, in the displayed order. Original JPEG streams are embedded without re-encoding. PNG and decoded WebP pixels are embedded losslessly. |
| JPG, PNG, WebP | JPG, PNG, WebP | Convert between different image formats at the original pixel dimensions. WebP uses lossless encoding. JPG uses a white background for transparency. Animated inputs require a still frame. |
| Excel (.xlsx) | CSV, TSV | One UTF-8 file per sheet, bundled when there are multiple sheets. Exports stored values, including cached formula results when available. Text that spreadsheet programs could execute as a formula is prefixed with an apostrophe. |
| CSV, TSV | Excel (.xlsx) | Reads UTF-8 data as text so identifiers retain leading zeros and formula-like content stays inert. |

Uploads total up to 64 MB and image-to-PDF batches can include up to 20 files. PDF work is limited to 100 pages, with additional render limits that depend on page size and DPI. Generated files are sent back to the browser for saving.

## Save media

1. Open **Download media** and paste an individual link to an Instagram post, Reel, active Story, TikTok video or TikTok photo post. Full TikTok links and standard share links are accepted.
2. For an Instagram Story, choose a Netscape-format cookie file from a signed-in session that can open it. For public links, start without cookies. The session file is sensitive. Filecraft filters it to the linked platform, uses a temporary copy and removes it after processing.
3. Confirm you have permission to save the content. Click **Get media**, then save the result. A carousel or multi-item result comes as a ZIP.

The downloader uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [gallery-dl](https://github.com/mikf/gallery-dl). It selects the best formats available to its extractor and keeps the media encoding, using FFmpeg stream copy when it needs to join audio and video. The platform may already have compressed or resized the media. The actual resolution depends on the variants the platform serves. Expired Stories, restricted links, rate limits, and extractor changes can prevent a download. Media results are limited to 200 MB and 40 items.

## Run the checks

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The checks create their own sample files and exercise the HTTP handlers. Media response tests use a fixture, so the suite can run offline and does not need any accounts.
