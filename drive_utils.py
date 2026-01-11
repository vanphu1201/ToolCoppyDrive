import os
import time
import re
import sys
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.errors import HttpError

class DriveCopyWorker:
    """
    Worker handles the Google Drive Copy logic using Service Account or User OAuth.
    Communicates progress back to the UI via status_callback.
    """
    def __init__(self, auth_file, auth_mode='service', status_callback=None):
        self.auth_file = auth_file
        self.auth_mode = auth_mode # 'service' or 'user'
        self.status_callback = status_callback  # func(message, progress_percent, is_error)
        self.service = self._get_service()
        
        # State tracking
        self.total_size = 0
        self.limit_size = 0
        self.excluded_strings = []
        self.total_files = 0
        self.processed_files = 0
        self.stop_signal = False

    def _get_service(self):
        """Authenticates using Service Account or User Credentials."""
        SCOPES = [
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/userinfo.email',
            'openid'
        ]
        creds = None
        
        try:
            if self.auth_mode == 'service':
                creds = service_account.Credentials.from_service_account_file(
                    self.auth_file, scopes=SCOPES)
            
            elif self.auth_mode == 'user':
                from google.oauth2.credentials import Credentials
                from google_auth_oauthlib.flow import InstalledAppFlow
                from google.auth.transport.requests import Request
                
                # Check existing token
                if os.path.exists('token.json'):
                    try:
                        self.creds = Credentials.from_authorized_user_file('token.json', SCOPES)
                    except Exception:
                        self.creds = None
                elif os.path.exists('/tmp/token.json'):
                     # Check tmp for Vercel
                    try:
                        self.creds = Credentials.from_authorized_user_file('/tmp/token.json', SCOPES)
                    except Exception:
                        self.creds = None

                if not self.creds or not self.creds.valid:
                    if self.creds and self.creds.expired and self.creds.refresh_token:
                        self.creds.refresh(Request())
                    else:
                        # DESKTOP MODE (Localhost)
                        try:
                            flow = InstalledAppFlow.from_client_secrets_file(self.auth_file, SCOPES)
                            self.creds = flow.run_local_server(port=0)
                        except Exception as e:
                            # Fallback or Error (Caller should handle manual flow if this fails)
                            raise Exception(f"Cannot auto-login (likely on Cloud). Use Manual Flow. Error: {e}")
                    
                    # Save token
                    with open('token.json', 'w') as token:
                        token.write(self.creds.to_json())
                        
            self.service = build('drive', 'v3', credentials=self.creds)
            return self.service
        except Exception as e:
            self._log(f"Lỗi xác thực ({self.auth_mode}): {str(e)}", is_error=True)
            return None

    def get_auth_url(self, redirect_uri=None):
        """Generates Auth URL for Web Flow."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        SCOPES = [
            'https://www.googleapis.com/auth/drive',
            'https://www.googleapis.com/auth/userinfo.email',
            'openid'
        ]
        
        # If no redirect_uri, default to OOB (which is now blocked for new apps, but kept for legacy)
        r_uri = redirect_uri if redirect_uri else 'urn:ietf:wg:oauth:2.0:oob'
        
        self.flow = InstalledAppFlow.from_client_secrets_file(
            self.auth_file, SCOPES, redirect_uri=r_uri)
        
        # Access type offline to get refresh token
        auth_url, _ = self.flow.authorization_url(prompt='consent', access_type='offline')
        return auth_url

    def exchange_code(self, code):
        """Exchanges code for token (Web Flow) and returns credentials."""
        if not hasattr(self, 'flow') or not self.flow:
             return None

        self.flow.fetch_token(code=code)
        self.creds = self.flow.credentials
        
        # Save token to file (Best effort for simple cache)
        try:
            with open('token.json', 'w') as token:
                token.write(self.creds.to_json())
        except Exception:
            with open('/tmp/token.json', 'w') as token:
                token.write(self.creds.to_json())
                
        # Re-init service
        self.service = build('drive', 'v3', credentials=self.creds)
        return self.creds

    def _log(self, message, progress=None, is_error=False):
        """Helper to send updates to UI."""
        if self.status_callback:
            self.status_callback(message, progress, is_error)

    def extract_folder_id(self, url):
        """Extracts Folder ID from URL."""
        if not url: return None
        match = re.search(r'[-\w]{25,}', url)
        return match.group(0) if match else url # Fallback to returning raw string if no match (maybe it is an ID)

    def check_exists(self, parent_id, name):
        """Checks if a file/folder exists in the parent directory."""
        try:
            safe_name = name.replace("'", "\\'")
            query = f"'{parent_id}' in parents and name = '{safe_name}' and trashed=false"
            # Fix: Add supportsAllDrives for Shared Drives support
            result = self.service.files().list(
                q=query, 
                fields='files(id)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            files = result.get('files', [])
            return files[0]['id'] if files else None
        except Exception as e:
            self._log(f"Lỗi kiểm tra tệp {name}: {e}", is_error=True)
            return None

    def verify_permissions(self):
        """Debug function: List first 5 files accessible by Service Account."""
        try:
            results = self.service.files().list(
                pageSize=5,
                fields="files(id, name, webViewLink)",
                q="trashed=false",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            return results.get('files', [])
        except Exception as e:
            return str(e)

    def create_folder(self, parent_id, folder_name):
        """Creates a folder if it doesn't exist."""
        existing_id = self.check_exists(parent_id, folder_name)
        if existing_id:
            return existing_id
        
        try:
            body = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_id]
            }
            folder = self.service.files().create(body=body, fields='id').execute()
            return folder['id']
        except Exception as e:
            self._log(f"Không thể tạo thư mục {folder_name}: {e}", is_error=True)
            return None

    def get_children(self, folder_id, from_page=0, to_page=0):
        """Lists all children files in a folder with pagination logic."""
        files = []
        token = None
        current_page = 0
        
        # Build query
        query = f"'{folder_id}' in parents and trashed = false"
        if self.excluded_strings:
            excludes = " and ".join([f"not name contains '{s}'" for s in self.excluded_strings])
            query += f" and ({excludes})"

        while True:
            if self.stop_signal: break
            try:
                current_page += 1
                results = self.service.files().list(
                    q=query,
                    fields='files(id, name, mimeType, size), nextPageToken',
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000 # Maximize page size for fewer requests
                ).execute()
                
                # Logic pagination
                if (from_page < current_page <= to_page) or to_page == 0:
                    files.extend(results.get('files', []))
                
                token = results.get('nextPageToken')
                
                # Break conditions
                if not token:
                    break
                if to_page > 0 and current_page >= to_page:
                    break
                    
            except Exception as e:
                self._log(f"Lỗi khi quét thư mục: {e}", is_error=True)
                break
                
        return files


    def scan_folder_recursive(self, folder_id):
        """Recursively counts files in a folder for progress bar."""
        total = 0
        token = None
        query = f"'{folder_id}' in parents and trashed = false"
        
        # Apply strict excludes if simple enough
        if self.excluded_strings:
             excludes = " and ".join([f"not name contains '{s}'" for s in self.excluded_strings])
             query += f" and ({excludes})"

        while True:
            if self.stop_signal: break
            try:
                results = self.service.files().list(
                    q=query,
                    fields='nextPageToken, files(id, mimeType)',
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000
                ).execute()
                
                files = results.get('files', [])
                for f in files:
                    if f['mimeType'] == 'application/vnd.google-apps.folder':
                         total += self.scan_folder_recursive(f['id'])
                    else:
                         total += 1
                
                token = results.get('nextPageToken')
                if not token: break
            except Exception as e:
                break 
        return total

    def copy_file(self, dest_parent_id, file_info):
        """Copies a single file."""
        if self.stop_signal: return

        try:
            # Check limits
            if self.limit_size > 0 and self.total_size >= (self.limit_size * 1024 * 1024 * 1024):
                self.stop_signal = True
                self._log("⚠️ Đã đạt giới hạn dung lượng tải xuống!", is_error=True)
                return

            # Check if exists
            if not self.check_exists(dest_parent_id, file_info['name']):
                body = {
                    'parents': [dest_parent_id],
                    'name': file_info['name']
                }
                # Copy request
                self.service.files().copy(
                    fileId=file_info['id'],
                    body=body,
                    supportsAllDrives=True
                ).execute()
                
                # Update stats
                size = int(file_info.get('size', 0))
                self.total_size += size
                self.processed_files += 1
                
                # Calculate progress
                progress = 0
                if self.total_files > 0:
                    progress = min(1.0, self.processed_files / self.total_files)
                
                msg = f"Đã copy: {file_info['name']} ({size/1024/1024:.2f} MB)"
                self._log(msg, progress=progress)
            else:
                self.processed_files += 1
                self._log(f"Bỏ qua (đã tồn tại): {file_info['name']}")

        except HttpError as e:
            reason = e.error_details[0].get('reason') if e.error_details else str(e)
            if reason == 'userRateLimitExceeded' or reason == 'rateLimitExceeded':
                 self._log(f"Rate Limit! Đang chờ 5s...", is_error=True)
                 time.sleep(5) # Simple backoff
            else:
                self._log(f"Lỗi copy {file_info['name']}: {e}", is_error=True)
        except Exception as e:
             self._log(f"Lỗi không xác định {file_info['name']}: {e}", is_error=True)

    def process_folder(self, source_id, dest_id, from_page=0, to_page=0, recursive=True):
        """Recursive function to process folder tree."""
        if self.stop_signal: return

        # 1. Get files in current source folder
        items = self.get_children(source_id, from_page, to_page)
        

        if source_id == self.root_source_id:
             # Logic is now handled in run_copy with pre-scan
             pass

        for item in items:
            if self.stop_signal: break
            
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                if recursive:
                    # Create subfolder in dest
                    sub_dest_id = self.create_folder(dest_id, item['name'])
                    if sub_dest_id:
                        self.process_folder(item['id'], sub_dest_id, 0, 0, recursive=True)
            else:
                self.copy_file(dest_id, item)

    def run_copy(self, source_url, dest_url, size_limit_gb=0, excluded_list=None, from_page=0, to_page=0):
        """Main entry point."""
        if not self.service:
            self._log("Chưa kết nối được Google Drive API (kiểm tra service_account.json)", is_error=True)
            return

        self.limit_size = size_limit_gb
        self.excluded_strings = excluded_list if excluded_list else []
        
        src_id = self.extract_folder_id(source_url)
        dst_id = self.extract_folder_id(dest_url)
        
        if not src_id or not dst_id:
            self._log("URL thư mục nguồn hoặc đích không hợp lệ.", is_error=True)
            return

        self._log("Đang khởi tạo...")
        
        # Get Source Folder Name to create root folder in Dest
        try:
            src_meta = self.service.files().get(fileId=src_id, supportsAllDrives=True).execute()
            root_folder_name = src_meta.get('name', 'Copied_Folder')
        except HttpError as e:
            error_reason = e.error_details[0].get('reason') if e.error_details else ""
            if e.resp.status == 404 or error_reason == 'notFound':
                 self._log(f"⛔ LỖI QUYỀN TRUY CẬP: Service Account không tìm thấy folder nguồn (ID: {src_id}).\n👉 Nguyên nhân: Bạn CHƯA chia sẻ quyền truy cập cho email Service Account.\n👉 Khắc phục: Copy email bên trái, vào Google Drive -> Share folder -> Dán email vào.", is_error=True)
            else:
                 self._log(f"Không truy cập được folder nguồn (ID: {src_id}). Lỗi API: {e}", is_error=True)
            return
        except Exception as e:
             self._log(f"Không truy cập được folder nguồn (ID: {src_id}). Lỗi hệ thống: {str(e)}", is_error=True)
             return

        # Create root folder in destination
        new_root_id = self.create_folder(dst_id, root_folder_name)
        if not new_root_id:
            self._log("Không thể tạo thư mục gốc tại Drive đích. Kiểm tra quyền ghi.", is_error=True)
            return

        self.root_source_id = src_id # Track root
        
        # New: Scan files first
        self._log("⏳ Đang quét toàn bộ cấu trúc folder để tính toán tiến độ (vui lòng đợi)...")
        self.total_files = self.scan_folder_recursive(src_id)
        if self.total_files == 0: self.total_files = 1 # Avoid division by zero
        self._log(f"Đã tìm thấy tổng cộng {self.total_files} tệp. Bắt đầu sao chép...")
        self._log(f"Bắt đầu sao chép '{root_folder_name}'...")
        
        self.process_folder(src_id, new_root_id, from_page, to_page)
        
        if not self.stop_signal:
            self._log("✅ Hoàn tất quá trình sao chép!", progress=1.0)

