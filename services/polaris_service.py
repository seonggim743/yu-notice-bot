import os
import zipfile
import logging
import subprocess
import sys
from typing import List

logger = logging.getLogger(__name__)

class PolarisService:
    def __init__(self):
        self.url = "https://www.polarisofficetools.com/hwpx/convert/image"

    def convert_to_jpg(self, file_path: str, output_dir: str) -> List[str]:
        """
        Converts HWP/HWPX file to JPG using Polaris Office Tools via a separate subprocess.
        Returns a list of paths to the downloaded JPG files.
        """
        # Create debug directory
        debug_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "debug_screenshots"))
        if not os.path.exists(debug_dir):
            os.makedirs(debug_dir)
            
        try:
            logger.info(f"[POLARIS] Starting conversion for {file_path}")
            
            # Create a standalone script to run Playwright
            # This avoids "Sync API inside asyncio loop" errors
            script_content = f'''
import os
import time
import sys
from playwright.sync_api import sync_playwright

def run_conversion():
    file_path = r"{os.path.abspath(file_path).replace(os.sep, '/')}"
    output_dir = r"{os.path.abspath(output_dir).replace(os.sep, '/')}"
    debug_dir = r"{debug_dir.replace(os.sep, '/')}"
    url = "{self.url}"

    print(f"[SCRIPT] Processing {{file_path}} -> {{output_dir}}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            # Phase 1: Navigate and wait for page load
            print(f"[SCRIPT] Navigating to {{url}}...")
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Wait for desktop loading indicator to disappear
            try:
                loading = page.get_by_text("데스크탑 레이아웃 준비 중")
                loading.wait_for(state="hidden", timeout=30000)
                print("[SCRIPT] Desktop loading completed")
            except:
                print("[SCRIPT] No loading indicator found or already loaded")

            # Debug: Dump HTML + screenshot
            try:
                with open(os.path.join(debug_dir, "page_dump.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
                page.screenshot(path=os.path.join(debug_dir, "01_after_load.png"))
                print("[SCRIPT] Saved 01_after_load.png")
            except Exception as e:
                print(f"[SCRIPT] Failed to dump debug info: {{e}}")

            # Phase 2: Dismiss any dialog (e.g. LanguageDetectionDialog)
            try:
                dialog = page.locator("[role='dialog']")
                if dialog.first.is_visible(timeout=3000):
                    print("[SCRIPT] Dialog detected, attempting to close...")
                    close_btns = [
                        dialog.locator("button").filter(has_text="OK"),
                        dialog.locator("button").filter(has_text="확인"),
                        dialog.locator("button").filter(has_text="닫기"),
                        dialog.locator("button").filter(has_text="Close"),
                    ]
                    for btn in close_btns:
                        try:
                            if btn.first.is_visible(timeout=1000):
                                btn.first.click()
                                page.wait_for_timeout(500)
                                print("[SCRIPT] Dialog closed")
                                break
                        except:
                            continue
            except:
                pass

            # Phase 3: Upload file
            print("[SCRIPT] Attempting file upload...")
            uploaded = False

            # Method A: Direct input[type='file'] (hidden input, most reliable)
            try:
                file_input = page.locator("input[type='file']").first
                file_input.set_input_files(file_path)
                uploaded = True
                print("[SCRIPT] Uploaded via direct input[type='file']")
            except Exception as e:
                print(f"[SCRIPT] Direct input method failed: {{e}}")

            # Method B: File chooser via upload button click
            if not uploaded:
                try:
                    print("[SCRIPT] Falling back to file chooser method...")
                    upload_btn = page.get_by_text("파일 업로드").first
                    with page.expect_file_chooser(timeout=10000) as fc_info:
                        upload_btn.click()
                    file_chooser = fc_info.value
                    file_chooser.set_files(file_path)
                    uploaded = True
                    print("[SCRIPT] Uploaded via file chooser")
                except Exception as e:
                    print(f"[SCRIPT] File chooser method failed: {{e}}")

            if not uploaded:
                raise Exception("All upload methods failed")

            page.wait_for_timeout(5000)
            page.screenshot(path=os.path.join(debug_dir, "02_after_upload.png"))
            print("[SCRIPT] Saved 02_after_upload.png")

            # Phase 4: Find and click convert button via JavaScript
            # Use page.evaluate to atomically find + click (avoids locator race conditions)
            print("[SCRIPT] Waiting for convert button...")
            convert_clicked = False
            for attempt in range(30):  # 30 * 2s = 60s max
                result = page.evaluate("""() => {{
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {{
                        const text = btn.textContent || '';
                        if (text.includes('JPG로') && !btn.disabled) {{
                            btn.click();
                            return 'clicked';
                        }} else if (text.includes('JPG로') && btn.disabled) {{
                            return 'disabled';
                        }}
                    }}
                    return 'not_found';
                }}""")

                if result == 'clicked':
                    convert_clicked = True
                    print("[SCRIPT] Convert button clicked via JS")
                    break
                elif result == 'disabled':
                    if attempt % 5 == 0:
                        print(f"[SCRIPT] Convert button found but disabled ({{attempt * 2}}s)")
                else:
                    if attempt == 0:
                        print("[SCRIPT] Convert button not found yet, waiting...")

                page.wait_for_timeout(2000)
                if attempt == 2:
                    page.screenshot(path=os.path.join(debug_dir, "03_waiting_for_convert.png"))

            if not convert_clicked:
                page.screenshot(path=os.path.join(debug_dir, "timeout_convert_btn.png"))
                try:
                    with open(os.path.join(debug_dir, "page_dump_no_convert.html"), "w", encoding="utf-8") as f:
                        f.write(page.content())
                except:
                    pass
                raise Exception("Convert button never appeared or remained disabled")

            page.wait_for_timeout(1000)
            page.screenshot(path=os.path.join(debug_dir, "03_after_convert_click.png"))

            # Phase 5: Wait for download buttons to appear (poll with progress screenshots)
            print("[SCRIPT] Waiting for conversion to complete...")
            download_area = page.locator("button").filter(has_text="다운로드").first
            conversion_done = False
            for wait_round in range(12):  # 12 * 5s = 60s max
                try:
                    download_area.wait_for(state="visible", timeout=5000)
                    conversion_done = True
                    break
                except:
                    elapsed = (wait_round + 1) * 5
                    print(f"[SCRIPT] Still waiting for conversion... ({{elapsed}}s)")
                    if wait_round in (2, 5, 8):  # Screenshots at 15s, 30s, 45s
                        page.screenshot(path=os.path.join(debug_dir, f"conversion_wait_{{elapsed}}s.png"))

            if not conversion_done:
                print("[SCRIPT] Timeout waiting for download button after 60s")
                page.screenshot(path=os.path.join(debug_dir, "timeout_download_btn.png"))
                try:
                    with open(os.path.join(debug_dir, "page_dump_timeout.html"), "w", encoding="utf-8") as f:
                        f.write(page.content())
                except:
                    pass
                raise Exception("Conversion timed out - download button never appeared")

            page.screenshot(path=os.path.join(debug_dir, "04_conversion_done.png"))
            print("[SCRIPT] Conversion complete, download buttons visible")

            # Phase 6: Download converted files
            # "ZIP 파일 다운로드" opens a download dialog (not a direct download).
            # Inside the dialog, "모든 파일 다운로드 (ZIP)" triggers actual download.
            print("[SCRIPT] Initiating download...")
            download_path = None

            # Step 1: Click "ZIP 파일 다운로드" to open download dialog
            zip_btn_clicked = page.evaluate("""() => {{
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {{
                    const text = btn.textContent || '';
                    if (text.includes('ZIP 파일 다운로드')) {{
                        btn.click();
                        return true;
                    }}
                }}
                return false;
            }}""")

            if zip_btn_clicked:
                print("[SCRIPT] Opened download dialog")
                page.wait_for_timeout(2000)
                page.screenshot(path=os.path.join(debug_dir, "05_download_dialog.png"))

                # Step 2: Click "모든 파일 다운로드 (ZIP)" in the dialog
                try:
                    with page.expect_download(timeout=10000) as download_info:
                        page.evaluate("""() => {{
                            const buttons = document.querySelectorAll('button');
                            for (const btn of buttons) {{
                                const text = btn.textContent || '';
                                if (text.includes('모든 파일 다운로드')) {{
                                    btn.click();
                                    return true;
                                }}
                            }}
                            return false;
                        }}""")
                    download = download_info.value
                    download_path = os.path.join(output_dir, download.suggested_filename)
                    download.save_as(download_path)
                    print(f"[SCRIPT] Downloaded ZIP: {{download_path}}")
                    print(f"OUTPUT_FILE:{{download_path}}")
                except Exception as dl_err:
                    print(f"[SCRIPT] ZIP download failed: {{dl_err}}")
            else:
                print("[SCRIPT] ZIP download button not found")

            # Step 3: Fallback - extract images directly from page
            if not download_path:
                print("[SCRIPT] Extracting converted images from page...")
                page.screenshot(path=os.path.join(debug_dir, "06_image_extraction.png"))

                import base64 as b64mod
                img_data_list = page.evaluate("""async () => {{
                    const results = [];
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {{
                        const src = img.src || '';
                        if (src.startsWith('blob:') || (src.startsWith('data:') && src.length > 1000)) {{
                            try {{
                                let dataUrl;
                                if (src.startsWith('blob:')) {{
                                    const resp = await fetch(src);
                                    const blob = await resp.blob();
                                    dataUrl = await new Promise(resolve => {{
                                        const reader = new FileReader();
                                        reader.onload = () => resolve(reader.result);
                                        reader.readAsDataURL(blob);
                                    }});
                                }} else {{
                                    dataUrl = src;
                                }}
                                results.push(dataUrl);
                            }} catch(e) {{}}
                        }}
                    }}
                    return results;
                }}""")

                if img_data_list:
                    for idx, data_url in enumerate(img_data_list):
                        b64_str = data_url.split(",", 1)[1] if "," in data_url else data_url
                        img_path = os.path.join(output_dir, f"page_{{idx+1}}.jpg")
                        with open(img_path, "wb") as img_f:
                            img_f.write(b64mod.b64decode(b64_str))
                        print(f"[SCRIPT] Extracted image: {{img_path}}")
                        print(f"OUTPUT_FILE:{{img_path}}")
                else:
                    raise Exception("No converted images found on page")

            browser.close()

    except Exception as e:
        print(f"[SCRIPT] Error: {{e}}", file=sys.stderr)
        try:
            error_shot = os.path.join(debug_dir, "polaris_worker_error.png")
            page.screenshot(path=error_shot)
            print(f"[SCRIPT] Saved error screenshot to {{error_shot}}", file=sys.stderr)
        except:
            pass
        sys.exit(1)

if __name__ == "__main__":
    run_conversion()
'''
            # Write script to temp file
            script_path = os.path.join(output_dir, "polaris_worker.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script_content)
            
            # Execute script
            result = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=120  # 120 seconds timeout (WASM conversion + possible download fallback)
            )
            
            # Log output
            if result.stdout:
                logger.info(f"[POLARIS] Worker STDOUT: {result.stdout}")
            if result.stderr:
                logger.warning(f"[POLARIS] Worker STDERR: {result.stderr}")
                
            if result.returncode != 0:
                logger.error(f"[POLARIS] Worker failed with exit code {result.returncode}")
                return []

            # Parse output for downloaded files (supports multiple OUTPUT_FILE lines from fallback)
            downloaded_files = []
            for line in result.stdout.splitlines():
                if line.startswith("OUTPUT_FILE:"):
                    f = line.split(":", 1)[1].strip()
                    if os.path.exists(f):
                        downloaded_files.append(f)

            if not downloaded_files:
                logger.error("[POLARIS] No output file found from worker")
                return []

            # Process downloaded files (ZIP or individual images)
            extracted_files = []
            for downloaded_file in downloaded_files:
                if downloaded_file.lower().endswith(".zip"):
                    logger.info(f"[POLARIS] Extracting ZIP: {downloaded_file}")
                    with zipfile.ZipFile(downloaded_file, 'r') as zip_ref:
                        zip_ref.extractall(output_dir)
                        for name in zip_ref.namelist():
                            if name.lower().endswith(('.jpg', '.jpeg', '.png')):
                                extracted_files.append(os.path.join(output_dir, name))
                elif downloaded_file.lower().endswith(('.jpg', '.jpeg', '.png')):
                    extracted_files.append(downloaded_file)
            
            logger.info(f"[POLARIS] Extracted {len(extracted_files)} images")
            return extracted_files

        except Exception as e:
            logger.error(f"[POLARIS] Conversion error: {e}")
            return []
