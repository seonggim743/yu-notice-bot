import os
import zipfile
from pathlib import Path

from services.polaris_service import PolarisService


def test_extract_zip_images_skips_non_images_and_avoids_input_conflict(tmp_path):
    input_path = tmp_path / "input.hwp"
    input_path.write_bytes(b"original-hwp")

    zip_path = tmp_path / "input_jpg_images.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("input.hwp/", b"")
        zf.writestr("input.hwp", b"duplicate original")
        zf.writestr("../escape.jpg", b"safe escape")
        zf.writestr("pages/page1.jpg", b"page one")

    extracted = PolarisService._extract_zip_images(str(zip_path), str(tmp_path))
    extracted_names = sorted(Path(path).name for path in extracted)

    assert extracted_names == ["escape.jpg", "page1.jpg"]
    assert input_path.read_bytes() == b"original-hwp"
    assert (tmp_path / "escape.jpg").read_bytes() == b"safe escape"
    assert (tmp_path / "page1.jpg").read_bytes() == b"page one"
    assert all(os.path.commonpath([str(tmp_path), path]) == str(tmp_path) for path in extracted)


def test_extract_zip_images_renames_duplicate_page_names(tmp_path):
    zip_path = tmp_path / "input_jpg_images.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("first/page.jpg", b"first")
        zf.writestr("second/page.jpg", b"second")

    extracted = PolarisService._extract_zip_images(str(zip_path), str(tmp_path))
    extracted_names = sorted(Path(path).name for path in extracted)

    assert extracted_names == ["page.jpg", "page_2.jpg"]
    assert (tmp_path / "page.jpg").read_bytes() == b"first"
    assert (tmp_path / "page_2.jpg").read_bytes() == b"second"
