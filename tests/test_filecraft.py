import csv
import io
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

import pikepdf
from openpyxl import load_workbook
from PIL import Image
from reportlab.pdfgen.canvas import Canvas

from filecraft.app import create_app
from filecraft.convert import ToolError
from filecraft.media import build_command, parse_link, prepare_cookies


def table_pdf(pages=1):
    stream = io.BytesIO()
    document = Canvas(stream, pagesize=(144, 144))
    for _ in range(pages):
        for x in (10, 70, 130):
            document.line(x, 55, x, 115)
        for y in (55, 85, 115):
            document.line(10, y, 130, y)
        document.drawString(14, 98, "Item")
        document.drawString(74, 98, "Count")
        document.drawString(14, 69, "Apples")
        document.drawString(74, 69, "25")
        document.showPage()
    document.save()
    return stream.getvalue()


def picture(kind, size=(64, 32)):
    buffer = io.BytesIO()
    mode = "RGBA" if kind == "PNG" else "RGB"
    Image.new(mode, size, "orange").save(buffer, kind)
    return buffer.getvalue()


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.client = create_app().test_client()
        html = self.client.get("/").get_data(as_text=True)
        self.headers = {"X-Filecraft-Token": re.search(r'filecraft-token" content="([^"]+)', html)[1]}

    def convert(self, target, *files, **options):
        response = self.client.post("/api/convert", headers=self.headers, data={
            "files": [(io.BytesIO(payload), name) for name, payload in files],
            "target": target,
            **options,
        })
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200, response.get_json(silent=True))
        return response

    def test_pdf_raster_zip_and_dpi(self):
        result = self.convert("jpg", ("sample.pdf", table_pdf(2)), dpi="450")
        with ZipFile(io.BytesIO(result.data)) as archive:
            self.assertEqual(archive.namelist(), ["sample-001.jpg", "sample-002.jpg"])
            with Image.open(io.BytesIO(archive.read("sample-001.jpg"))) as image:
                self.assertEqual((image.format, image.size, image.info["dpi"]), ("JPEG", (900, 900), (450, 450)))
        png = self.convert("png", ("sample.pdf", table_pdf()), dpi="150")
        with Image.open(io.BytesIO(png.data)) as image:
            self.assertEqual((image.format, image.size), ("PNG", (300, 300)))

    def test_image_pdf_keeps_jpeg_stream_and_image_order(self):
        jpeg = picture("JPEG")
        result = self.convert("pdf", ("a.jpg", jpeg), ("b.png", picture("PNG", (40, 80))),
                              ("c.webp", picture("WEBP")))
        with pikepdf.open(io.BytesIO(result.data)) as pdf:
            self.assertEqual(len(pdf.pages), 3)
            original = next(iter(pdf.pages[0].get_images().values()))
            self.assertEqual(original.read_raw_bytes(), jpeg)
            second = next(iter(pdf.pages[1].get_images().values()))
            self.assertEqual((second.Width, second.Height), (40, 80))

    def test_pdf_table_and_text(self):
        payload = table_pdf(2)
        result = self.convert("xlsx", ("sales.pdf", payload))
        workbook = load_workbook(io.BytesIO(result.data), data_only=True)
        self.assertEqual(workbook.sheetnames, ["Page 1 Table 1", "Page 2 Table 1"])
        self.assertEqual(list(workbook.worksheets[1].values), [("Item", "Count"), ("Apples", "25")])
        result = self.convert("txt", ("sales.pdf", payload))
        self.assertEqual(result.data.decode().count("Apples 25"), 2)

    def test_delimited_round_trip_and_formula_text(self):
        source = "ID\tLabel\n0012\t=SUM(1,2)\n0003\tالكويت\n".encode()
        result = self.convert("xlsx", ("data.tsv", source))
        workbook = load_workbook(io.BytesIO(result.data))
        self.assertEqual(workbook.active["A2"].value, "0012")
        self.assertEqual(workbook.active["B2"].data_type, "s")
        out = self.convert("csv", ("data.xlsx", result.data))
        rows = list(csv.reader(io.StringIO(out.data.decode("utf-8-sig"))))
        self.assertEqual(rows, [["ID", "Label"], ["0012", "'=SUM(1,2)"], ["0003", "الكويت"]])

    def test_image_transforms_preserve_size_and_alpha(self):
        result = self.convert("webp", ("sample.png", picture("PNG")))
        png = self.convert("png", ("sample.webp", result.data))
        jpg = self.convert("jpg", ("sample.png", png.data))
        for data, kind in ((png.data, "PNG"), (jpg.data, "JPEG")):
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual((image.format, image.size), (kind, (64, 32)))

    def test_bad_upload_and_cross_origin_requests(self):
        bad = self.client.post("/api/convert", headers=self.headers, data={
            "files": (io.BytesIO(b"bogus"), "bad.pdf"), "target": "png"
        })
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.client.post("/api/convert").status_code, 403)
        self.assertEqual(self.client.get("/", base_url="http://example.org").status_code, 400)

    @patch("filecraft.app.download")
    def test_download_response_keeps_platform_bytes(self, download):
        def fake_download(_link, folder, _cookies):
            files = [folder / "photo.jpg", folder / "video.mp4"]
            for file in files:
                file.write_bytes(file.name.encode())
            return files

        download.side_effect = fake_download
        payload = {"url": "https://www.instagram.com/p/ABC1/"}
        denied = self.client.post("/api/download", headers=self.headers, data=payload)
        self.assertEqual(denied.status_code, 422)
        download.assert_not_called()
        response = self.client.post("/api/download", headers=self.headers, data={**payload, "allowed": "yes"})
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200, response.get_json(silent=True))
        with ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(archive.read("video.mp4"), b"video.mp4")
            self.assertEqual(archive.read("photo.jpg"), b"photo.jpg")


class MediaTests(unittest.TestCase):
    def test_link_allowlist_and_canonicalization(self):
        cases = {
            "https://www.instagram.com/reels/ABC/?igsh=tracker": "https://www.instagram.com/reel/ABC/",
            "https://www.instagram.com/stories/creator/123456/": "https://www.instagram.com/stories/creator/123456/",
            "https://m.tiktok.com/@creator/photo/123?_t=tracker": "https://www.tiktok.com/@creator/photo/123",
            "https://vm.tiktok.com/ZMabc/": "https://vm.tiktok.com/ZMabc/",
        }
        for value, canonical in cases.items():
            with self.subTest(value=value):
                self.assertEqual(parse_link(value).url, canonical)
        for value in ("http://www.tiktok.com/@x/video/1", "https://www.tiktok.com.evil.test/@x/video/1",
                      "https://www.tiktok.com:443/@x/video/1", "https://www.instagram.com@evil.test/p/1",
                      "https://www.instagram.com/example", "https://www.tiktok.com/@creator"):
            with self.subTest(value=value), self.assertRaises(ToolError):
                parse_link(value)

    def test_scoped_session_cookies_and_native_format_command(self):
        cookie_bytes = (
            b"# Netscape HTTP Cookie File\n"
            b".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tplaceholder\n"
            b".example.org\tTRUE\t/\tTRUE\t0\tother\tplaceholder\n"
        )
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            cookies = prepare_cookies(cookie_bytes, "instagram", folder / "cookies.txt")
            self.assertIn("sessionid", cookies.read_text())
            self.assertNotIn("example.org", cookies.read_text())
            command = build_command("yt_dlp", parse_link("https://www.instagram.com/reel/ABC/"), folder, cookies)
            self.assertIn("bv*+ba/b", command)
            self.assertNotIn("--recode-video", command)
            self.assertIn(str(cookies), command)


if __name__ == "__main__":
    unittest.main()
