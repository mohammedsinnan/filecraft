"""File conversion routes and limits."""

import csv
import math
import re
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import img2pdf
import pdfplumber
import pypdfium2 as pdfium
from openpyxl import Workbook, load_workbook
from openpyxl.cell import Cell
from openpyxl.utils.exceptions import InvalidFileException
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from PIL import Image, ImageOps, UnidentifiedImageError


class ToolError(Exception):
    """An actionable message safe to display in the app."""


IMAGE_TYPES = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
FORMATS = {
    "pdf": ["jpg", "png", "xlsx", "txt"],
    "jpg": ["pdf", "png", "webp"],
    "jpeg": ["pdf", "png", "webp"],
    "png": ["pdf", "jpg", "webp"],
    "webp": ["pdf", "jpg", "png"],
    "xlsx": ["csv", "tsv"],
    "csv": ["xlsx"],
    "tsv": ["xlsx"],
}
MAX_PAGES = 100
MAX_PIXELS = 36_000_000
MAX_TOTAL_PIXELS = 240_000_000
MAX_CELLS = 300_000
MAX_COLUMNS = 512
MAX_SHEETS = 40
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[^\w.() -]', "_", name, flags=re.UNICODE)
    return cleaned[:100].strip(" .") or "file"


def convert(sources: list[Path], target: str, folder: Path, dpi: int = 300) -> list[Path]:
    extensions = [path.suffix.lower() for path in sources]
    if len(sources) > 1:
        if target != "pdf" or not all(ext in IMAGE_TYPES for ext in extensions):
            raise ToolError("Choose one file, or combine multiple images into one PDF.")
    elif not sources or target not in FORMATS.get(extensions[0][1:], []):
        raise ToolError("Choose a supported input and output format.")

    source = sources[0]
    stem = safe_name(source.stem)
    try:
        if extensions[0] in IMAGE_TYPES:
            return convert_images(sources, target, folder / f"{stem}.{target}")
        if extensions[0] == ".pdf":
            if target in ("jpg", "png"):
                return render_pdf(source, target, folder, stem, dpi)
            return [extract_pdf(source, folder / f"{stem}.{target}", target)]
        if extensions[0] == ".xlsx":
            return export_workbook(source, folder, stem, target)
        return [import_delimited(source, folder / f"{stem}.xlsx")]
    except PDFPasswordIncorrect as exc:
        raise ToolError("Unlock the password-protected PDF and try again.") from exc
    except (pdfium.PdfiumError, PDFSyntaxError) as exc:
        raise ToolError("This PDF is damaged or password-protected.") from exc
    except (UnidentifiedImageError, Image.DecompressionBombError, img2pdf.ImageOpenError) as exc:
        raise ToolError("This image is damaged, unsupported, or too large.") from exc
    except (BadZipFile, InvalidFileException) as exc:
        raise ToolError("This file is not a readable XLSX workbook.") from exc
    except (UnicodeError, csv.Error) as exc:
        raise ToolError("Export the table as UTF-8 CSV or TSV and try again.") from exc


def convert_images(sources: list[Path], target: str, output: Path) -> list[Path]:
    total_pixels = 0
    prepared = []
    for index, source in enumerate(sources):
        with Image.open(source) as image:
            if image.format != IMAGE_TYPES[source.suffix.lower()]:
                raise ToolError("The image format does not match its file extension.")
            if getattr(image, "n_frames", 1) > 1:
                raise ToolError("Choose still images. Animated files have multiple frames.")
            pixels = image.width * image.height
            total_pixels += pixels
            if pixels > MAX_PIXELS or total_pixels > MAX_TOTAL_PIXELS:
                raise ToolError("The selected images exceed the pixel limit. Use smaller images or fewer files.")
            if target == "pdf" and image.format in ("JPEG", "PNG"):
                prepared.append(str(source))
                continue
            oriented = ImageOps.exif_transpose(image)
            if target == "pdf":
                # WebP must be decoded for PDF. Preserve its decoded pixels in PNG.
                image_path = output.parent / f".embedded-{index}.png"
                oriented.save(image_path, "PNG")
                prepared.append(str(image_path))
            elif target == "jpg":
                if "A" in oriented.getbands() or "transparency" in oriented.info:
                    rgba = oriented.convert("RGBA")
                    rgb = Image.new("RGB", rgba.size, "white")
                    rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = oriented.convert("RGB")
                rgb.save(output, quality=100, subsampling=0)
            else:
                if oriented.mode not in ("1", "L", "LA", "P", "RGB", "RGBA", "I;16"):
                    oriented = oriented.convert("RGB")
                options = {"lossless": True, "exact": True} if target == "webp" else {}
                if oriented.info.get("icc_profile") and image.mode == oriented.mode:
                    options["icc_profile"] = oriented.info["icc_profile"]
                oriented.save(output, **options)

    if target == "pdf":
        with output.open("wb") as stream:
            img2pdf.convert(*prepared, outputstream=stream, rotation=img2pdf.Rotation.ifvalid)
    return [output]


def render_pdf(source: Path, target: str, folder: Path, stem: str, dpi: int) -> list[Path]:
    if dpi not in (150, 300, 450):
        raise ToolError("Choose 150, 300, or 450 DPI.")
    scale = dpi / 72
    outputs = []
    total_pixels = 0
    with pdfium.PdfDocument(source) as document:
        if not 0 < len(document) <= MAX_PAGES:
            raise ToolError(f"Choose a PDF with 1 to {MAX_PAGES} pages.")
        for index in range(len(document)):
            page = document[index]
            try:
                width, height = page.get_size()
                pixels = math.ceil(width * scale) * math.ceil(height * scale)
                total_pixels += pixels
                if not 0 < pixels <= MAX_PIXELS or total_pixels > MAX_TOTAL_PIXELS:
                    raise ToolError("These pages exceed the pixel limit. Lower the DPI or split the PDF.")
                bitmap = page.render(scale=scale)
                try:
                    name = f"{stem}.{target}" if len(document) == 1 else f"{stem}-{index + 1:03}.{target}"
                    destination = folder / name
                    options = {"quality": 100, "subsampling": 0} if target == "jpg" else {}
                    with bitmap.to_pil() as image:
                        image.convert("RGB").save(destination, dpi=(dpi, dpi), **options)
                    outputs.append(destination)
                finally:
                    bitmap.close()
            finally:
                page.close()
    return outputs


def write_rows(sheet, rows, used_cells: int = 0) -> int:
    for row in rows:
        used_cells += max(1, len(row))
        if len(row) > MAX_COLUMNS or used_cells > MAX_CELLS:
            raise ToolError("This table exceeds the spreadsheet size limit.")
        cells = []
        for value in row:
            if value is not None:
                value = CONTROL_CHARS.sub("", str(value))
                if len(value) > 32767:
                    raise ToolError("A cell contains more text than Excel can store.")
            cell = Cell(sheet, value=value)
            if value is not None:
                cell.data_type = "s"
            cells.append(cell)
        sheet.append(cells)
    return used_cells


def extract_pdf(source: Path, output: Path, target: str) -> Path:
    with pdfplumber.open(source) as document:
        if not 0 < len(document.pages) <= MAX_PAGES:
            raise ToolError(f"Choose a PDF with 1 to {MAX_PAGES} pages.")
        if target == "txt":
            pages = []
            for page in document.pages:
                pages.append(page.extract_text() or "")
                page.close()
            if not any(pages):
                raise ToolError("No selectable text was found. Scanned pages require OCR.")
            output.write_text("\n\n".join(pages), encoding="utf-8")
            return output

        workbook = Workbook()
        workbook.remove(workbook.active)
        used_cells = 0
        for page in document.pages:
            for index, rows in enumerate(page.extract_tables(), start=1):
                if len(workbook.worksheets) >= MAX_SHEETS:
                    raise ToolError(f"The PDF contains more than {MAX_SHEETS} tables. Split it into smaller PDFs.")
                sheet = workbook.create_sheet(f"Page {page.page_number} Table {index}")
                used_cells = write_rows(sheet, rows, used_cells)
            page.close()
        if not workbook.worksheets:
            raise ToolError("No ruled tables were found. Use a text-based PDF with visible table borders.")
        workbook.save(output)
    return output


def import_delimited(source: Path, output: Path) -> Path:
    workbook = Workbook()
    workbook.active.title = "Data"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        delimiter = "\t" if source.suffix.lower() == ".tsv" else ","
        if not write_rows(workbook.active, csv.reader(stream, delimiter=delimiter, strict=True)):
            raise ToolError("This table is empty.")
    workbook.save(output)
    return output


def csv_value(value):
    # Keep spreadsheet programs from interpreting text values as formulas on import.
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or
                                   value.lstrip().startswith(("=", "+", "-", "@", "＝", "＋", "－", "＠"))):
        return "'" + value
    return value


def export_workbook(source: Path, folder: Path, stem: str, target: str) -> list[Path]:
    with ZipFile(source) as archive:
        if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
            raise ToolError("The workbook is too large when expanded.")
        if "xl/workbook.xml" not in archive.namelist():
            raise ToolError("This file is not a readable XLSX workbook.")
    workbook = load_workbook(source, read_only=True, data_only=True, keep_links=False)
    outputs = []
    used_cells = 0
    try:
        if not 0 < len(workbook.worksheets) <= MAX_SHEETS:
            raise ToolError(f"Choose a workbook with 1 to {MAX_SHEETS} sheets.")
        for index, sheet in enumerate(workbook.worksheets, start=1):
            label = stem if len(workbook.worksheets) == 1 else f"{index:02}-{safe_name(sheet.title)}"
            output = folder / f"{label}.{target}"
            if (sheet.max_column or 0) > MAX_COLUMNS:
                raise ToolError(f"Worksheets can contain up to {MAX_COLUMNS} columns.")
            with output.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream, delimiter="\t" if target == "tsv" else ",")
                for row in sheet.values:
                    used_cells += max(1, len(row))
                    if len(row) > MAX_COLUMNS or used_cells > MAX_CELLS:
                        raise ToolError("This workbook exceeds the spreadsheet size limit.")
                    writer.writerow(csv_value(value) for value in row)
            outputs.append(output)
    finally:
        workbook.close()
    return outputs
