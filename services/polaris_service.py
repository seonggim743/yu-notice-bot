import os
import time
import zipfile
import logging
import subprocess
import sys
from typing import List, Optional

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
import zipfile
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

            print(f"[SCRIPT] Navigating to {{url}}...")
            page.goto(url)

            # Debug: Dump HTML
            try:
                with open(os.path.join(debug_dir, "page_dump.html"), "w", encoding="utf-8") as f:
                    f.write(page.content())
                print(f"[SCRIPT] Dumped HTML to page_dump.html")
                page.screenshot(path=os.path.join(debug_dir, "initial_page.png"))
            except Exception as e:
                print(f"[SCRIPT] Failed to dump debug info: {{e}}")

            # Try to close popup (specific)
            try:
                close_btn = page.locator(".Vue-Toastification__close-button").first
                if close_btn.is_visible(timeout=5000):
                    print("[SCRIPT] Closing popup...")
                    close_btn.click()
                    time.sleep(1)
            except:
                pass

            # 1. Upload File
            print(f"[SCRIPT] Attempting upload...")
            uploaded = False
            
            # Method A: File Chooser (User-like)
            try:
                # The button is .addFileBtn
                upload_trigger = page.locator(".addFileBtn").first
                
                # Check if it's visible
                if upload_trigger.is_visible(timeout=5000):
                    print(f"[SCRIPT] Found upload trigger (.addFileBtn), clicking...")
                    
                    # Ensure it's not disabled (though class might be misleading)
                    # We'll just try clicking it.
                    
                    with page.expect_file_chooser(timeout=10000) as fc_info:
                        upload_trigger.click()
                    
                    file_chooser = fc_info.value
                    file_chooser.set_files(file_path)
                    uploaded = True
                    print("[SCRIPT] Uploaded via File Chooser")
            except Exception as e:
                print(f"[SCRIPT] File chooser method failed: {{e}}")

            # Method B: Direct Input (Fallback)
            if not uploaded:
                print("[SCRIPT] Falling back to direct input manipulation...")
                # The input is inside .addFileBtn
                file_input = page.locator(".addFileBtn input[type='file']")
                
                # It might be hidden, so we use set_input_files which handles this
                file_input.set_input_files(file_path)
                
                # Dispatch events
                print(f"[SCRIPT] Dispatching change event...")
                page.evaluate("document.querySelector('.addFileBtn input[type=file]').dispatchEvent(new Event('change', {{'bubbles': true}}))")
                page.evaluate("document.querySelector('.addFileBtn input[type=file]').dispatchEvent(new Event('input', {{'bubbles': true}}))")
            
            # Take screenshot after upload
            time.sleep(3)
            page.screenshot(path=os.path.join(debug_dir, "after_upload.png"))
            print(f"[SCRIPT] Saved after_upload.png")

            # 2. Wait for Convert Button
            print(f"[SCRIPT] Waiting for conversion button...")
            # Try multiple selectors
            convert_btn = page.locator("#btn_convert, .funcBtn").first
            try:
                convert_btn.wait_for(state="visible", timeout=30000)
            except Exception as e:
                print(f"[SCRIPT] Timeout waiting for convert button. Taking screenshot...")
                page.screenshot(path=os.path.join(debug_dir, "timeout_convert_btn.png"))
                raise e
            
            # Check if button is disabled
            if "btnDisabled" in convert_btn.get_attribute("class") or convert_btn.is_disabled():
                print(f"[SCRIPT] Button disabled, waiting for enablement...")
                time.sleep(2)
            
            print(f"[SCRIPT] Clicking convert button...")
            convert_btn.click()

            # 3. Wait for Download Button
            print(f"[SCRIPT] Waiting for conversion completion...")
            # Wait for the download button to appear
            download_btn = page.locator(".file_down_btn, .downloadBtn").first
            try:
                download_btn.wait_for(state="visible", timeout=60000)
            except Exception as e:
                print(f"[SCRIPT] Timeout waiting for download button. Taking screenshot...")
                page.screenshot(path=os.path.join(debug_dir, "timeout_download_btn.png"))
                raise e

            print(f"[SCRIPT] Download button found. Clicking...")
            
            # 4. Handle Download
            # It might be a popup OR a modal OR a direct download
            
            # We'll try to detect if a popup opens
            target_page = page
            try:
                with context.expect_page(timeout=3000) as popup_info:
                    download_btn.click()
                
                popup = popup_info.value
                print(f"[SCRIPT] Popup opened. Waiting for 'Download All'...")
                popup.wait_for_load_state("networkidle")
                target_page = popup
            except Exception as e:
                print(f"[SCRIPT] No popup detected (timeout), assuming modal or direct download on main page... (Error: {{e}})")
                # The click happened, so we just proceed to check the main page
            
            # Now look for the actual download trigger (e.g. "Download All" inside the popup/modal)
            # Or maybe the previous click already triggered it?
            
            # Try to find the "Download All" button
            download_all_btn = target_page.locator("a.btn_download, button.btn_download, .download_all, .allDownload, .download_zip").first
            
            if download_all_btn.is_visible(timeout=5000):
                print(f"[SCRIPT] Found 'Download All' button, clicking...")
                with target_page.expect_download(timeout=30000) as download_info:
                    download_all_btn.click()
            else:
                print(f"[SCRIPT] 'Download All' button not found. Checking if download already started...")
                # Maybe the first click was enough? (Direct download)
                # But we missed the expect_download context.
                # This is tricky. If direct download happened, we might have missed the event.
                # But usually there's a confirmation or it's a separate button.
                raise Exception("Could not find download trigger")
            
            download = download_info.value
            download_path = os.path.join(output_dir, download.suggested_filename)
            download.save_as(download_path)
            
            print(f"[SCRIPT] Downloaded to: {{download_path}}")
            browser.close()
            
            # Return the downloaded file path
            print(f"OUTPUT_FILE:{{download_path}}")

    except Exception as e:
        print(f"[SCRIPT] Error: {{e}}", file=sys.stderr)
        try:
            # Take screenshot on error
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
                timeout=120 # 2 minutes timeout
            )
            
            # Log output
            if result.stdout:
                logger.info(f"[POLARIS] Worker STDOUT: {result.stdout}")
            if result.stderr:
                logger.warning(f"[POLARIS] Worker STDERR: {result.stderr}")
                
            if result.returncode != 0:
                logger.error(f"[POLARIS] Worker failed with exit code {result.returncode}")
                return []

            # Parse output for downloaded file
            downloaded_file = None
            for line in result.stdout.splitlines():
                if line.startswith("OUTPUT_FILE:"):
                    downloaded_file = line.split(":", 1)[1].strip()
                    break
            
            if not downloaded_file or not os.path.exists(downloaded_file):
                logger.error("[POLARIS] No output file found from worker")
                return []

            # Process the downloaded file (ZIP or JPG)
            extracted_files = []
            if downloaded_file.lower().endswith(".zip"):
                logger.info(f"[POLARIS] Extracting ZIP: {downloaded_file}")
                with zipfile.ZipFile(downloaded_file, 'r') as zip_ref:
                    zip_ref.extractall(output_dir)
                    for name in zip_ref.namelist():
                        if name.lower().endswith(('.jpg', '.jpeg')):
                            extracted_files.append(os.path.join(output_dir, name))
            elif downloaded_file.lower().endswith(('.jpg', '.jpeg')):
                extracted_files.append(downloaded_file)
            
            logger.info(f"[POLARIS] Extracted {len(extracted_files)} images")
            return extracted_files

        except Exception as e:
            logger.error(f"[POLARIS] Conversion error: {e}")
            return []
