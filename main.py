from fastapi import FastAPI, Request, Response, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse
import uvicorn
import asyncio
import os
import json
import threading
import queue
import uuid
import hashlib
import secrets
from drive_utils import DriveCopyWorker
from database import UsageDatabase
from dotenv import load_dotenv
import requests

app = FastAPI()

# Mount Static & Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Global State (Single User Desktop App Mode)
msg_queue = queue.Queue()
is_running = False

# Database
db = UsageDatabase()

# Load environment variables
load_dotenv()

# SePay Webhook configuration
# KMA uses environment variable if available, otherwise generates a random one
SEPAY_WEBHOOK_KEY = os.environ.get("SEPAY_WEBHOOK_KEY")
if not SEPAY_WEBHOOK_KEY:
    # Generate a random key if not set in env (fallback)
    import secrets
    SEPAY_WEBHOOK_KEY = f"SEPAY_{secrets.token_urlsafe(32)}"
    print(f"\n============================================================")
    print(f"🔑 GENERATED SEPAY WEBHOOK API KEY: {SEPAY_WEBHOOK_KEY}")
    print(f"⚠️  WARNING: Store this key in Vercel Environment Variables!")
    print(f"============================================================\n")
else:
    print(f"\n============================================================")
    print(f"✅ USING CONFIGURED SEPAY WEBHOOK API KEY (from ENV)")
    print(f"============================================================\n")

# SePay API Token for Manual Checks
SEPAY_API_TOKEN = os.environ.get("SEPAY_API_TOKEN")


def status_callback(message, progress=None, is_error=False):
    """Callback bridge for DriveCopyWorker to put messages into Queue."""
    data = {
        "message": message,
        "progress": progress,
        "error": is_error,
        "done": False
    }
    msg_queue.put(data)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/check_auth")
def check_auth(request: Request):
    """Check if session is valid in DB (Persistent)."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        # Fallback to file check (legacy/local)
        exists = os.path.exists('token.json') or os.path.exists('/tmp/token.json')
        return {"authenticated": exists}
    
    token_json = db.get_session(session_id)
    return {"authenticated": token_json is not None}

# Startup: Handle Vercel Environment Variables
AUTH_FILE_PATH = 'client_secret.json'

@app.on_event("startup")
async def startup_event():
    global AUTH_FILE_PATH
    # Check if running on Vercel (or local with Env Var)
    secret_json = os.environ.get("GOOGLE_CLIENT_SECRET_JSON")
    if secret_json:
        # Write to /tmp for Vercel (Read-only file system)
        AUTH_FILE_PATH = "/tmp/client_secret.json"
        with open(AUTH_FILE_PATH, "w") as f:
            f.write(secret_json)
        print(f"Created client_secret.json from Env Var at {AUTH_FILE_PATH}")
    elif os.path.exists('client_secret.json'):
         AUTH_FILE_PATH = 'client_secret.json'

# Global Worker for Auth State
auth_worker = None

@app.post("/api/login")
async def login_google(request: Request):
    """Redirects to Google Auth with Vercel URL."""
    global auth_worker
    try:
        if os.path.exists('token.json') or os.path.exists('/tmp/token.json'):
             return {"status": "already_logged_in"}

        if not os.path.exists(AUTH_FILE_PATH):
             return {"status": "error", "message": "Missing client_secret.json"}

        # Determine Redirect URI based on environment or Request
        # If on Vercel, it's https://tool-coppy-drive.vercel.app/api/callback
        # Ideally we read this from Env, but for quick fix we use hardcode or request.base_url
        
        # Determine Redirect URI dynamically based on the current request URL
        # This allows it to work on both Localhost and Vercel automaticallly
        base_url = str(request.base_url).rstrip("/")
        REDIRECT_URI = f"{base_url}/api/callback"
        
        # Init worker for Auth with Redirect URI
        # IMPORTANT: You must add this EXACT URL to Google Cloud Console "Authorized redirect URIs"
        # Local: http://localhost:8000/api/callback
        # Vercel: https://tool-coppy-drive.vercel.app/api/callback
        auth_worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user')
        auth_url = auth_worker.get_auth_url(redirect_uri=REDIRECT_URI)
        
        return {"status": "redirect_required", "auth_url": auth_url}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/callback")
async def auth_callback(code: str, request: Request):
    """Handles the OAuth Callback from Google."""
    global auth_worker
    try:
        # Reconstruct base_url and Redirect URI
        base_url = str(request.base_url).rstrip("/")
        if "vercel.app" in base_url and "http://" in base_url:
            base_url = base_url.replace("http://", "https://")
        
        REDIRECT_URI = f"{base_url}/api/callback"
        
        if not auth_worker:
             auth_worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user')
             auth_worker.get_auth_url(redirect_uri=REDIRECT_URI)

        creds = auth_worker.exchange_code(code)
        
        if creds:
            # SUCCESS: Convert creds to JSON
            token_json = creds.to_json()
            
            # Generate Session ID
            session_id = str(uuid.uuid4())
            
            # Save to Database (Persistent!)
            db.save_session(session_id, token_json)
            print(f"✅ Saved session {session_id} to database")
            
            # Set Cookie and Redirect
            response = HTMLResponse("<script>window.location.href='/';</script>")
            response.set_cookie(
                key="session_id", 
                value=session_id,
                max_age=30*24*60*60, # 30 days
                httponly=True,
                samesite='lax'
            )
            return response
        else:
            return HTMLResponse("<h1>Lỗi xác thực!</h1><p>Không thể trao đổi mã token.</p>")
            
    except Exception as e:
        return HTMLResponse(f"<h1>Lỗi Callback: {e}</h1>")



@app.post("/api/start_copy")
async def start_copy(request: Request):
    global is_running
    
    if is_running:
        return JSONResponse({"status": "error", "message": "Tiến trình khác đang chạy!"})
    
    try:
        # 1. Try to get token from Session (DB)
        session_id = request.cookies.get("session_id")
        token_path = None
        
        if session_id:
            token_json = db.get_session(session_id)
            if token_json:
                # Write to temp file for worker to use
                token_path = f"/tmp/token_{session_id}.json"
                with open(token_path, "w") as f:
                    f.write(token_json)
                print(f"✅ Loaded token from DB for session {session_id}")
        
        # 2. Fallback to global file (Legacy/Local)
        if not token_path:
            if os.path.exists("/tmp/token.json"):
                token_path = "/tmp/token.json"
            elif os.path.exists("token.json"):
                token_path = "token.json"

        # Verify user is logged in
        if not token_path or not os.path.exists(token_path):
            return JSONResponse({
                "status": "error", 
                "message": "Bạn chưa đăng nhập Google Drive. Vui lòng đăng nhập lại!"
            })
        
        # Get user email from Google token (this is the unique identifier)
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(token_path)
        
        # Get user info from Google
        from googleapiclient.discovery import build
        oauth_service = build('oauth2', 'v2', credentials=creds)
        user_info = oauth_service.userinfo().get().execute()
        user_email = user_info.get('email')
        
        print(f"🔍 User email retrieved: {user_email}")
        
        if not user_email:
            return JSONResponse({
                "status": "error",
                "message": "Không thể lấy thông tin tài khoản Google"
            })
        
        # Use email as client_id (cannot be bypassed by clearing cookies)
        client_id = user_email
        print(f"📧 Using client_id: {client_id}")
        
        # Check usage
        user = db.get_or_create_user(client_id)
        print(f"📊 Current usage for {client_id}: {user['usage_count']} / 2, Paid: {user['is_paid']}")
        
        # Payment gate logic:
        # - If paid: unlimited access
        # - If not paid and usage_count >= 2: require payment
        # - If not paid and usage_count < 2: allow copy (will increment after)
        
        if not user['is_paid'] and user['usage_count'] >= 2:
            # User has used 2 free copies and hasn't paid yet
            # Generate payment QR code
            payment_code = f"DH{hashlib.md5(client_id.encode()).hexdigest()[:8].upper()}"
            amount = 50000
            
            # SePay QR format with user's bank info
            bank_name = "MBBank"
            account_number = "0348880746"
            account_name = "TRAN VAN PHU"
            
            qr_url = f"https://qr.sepay.vn/img?bank={bank_name}&acc={account_number}&amount={amount}&des={payment_code}"
            
            print(f"💳 Payment required for {client_id}. Payment code: {payment_code}")
            
            return JSONResponse({
                "status": "payment_required",
                "message": "Bạn đã sử dụng hết 2 lần miễn phí. Vui lòng thanh toán để tiếp tục.",
                "qr_url": qr_url,
                "amount": amount,
                "payment_code": payment_code,
                "account_name": account_name
            })
    
        # User is either:
        # 1. Paid user (unlimited access)
        # 2. Free user with usage_count < 2
        
        # Params
        dest = request.query_params.get("dest_url")
        src = request.query_params.get("source_url")
        limit = int(request.query_params.get("limit_size", 500))
        exclude = request.query_params.get("exclude_str", "")
        from_p = int(request.query_params.get("from_page", 0))
        to_p = int(request.query_params.get("to_page", 0))
        
        excluded_list = [x.strip() for x in exclude.split(",") if x.strip()]
        
        # Worker Thread
        def run_worker():
            global is_running
            try:
                is_running = True
                if not os.path.exists(AUTH_FILE_PATH):
                     msg_queue.put({"message": "Thiếu file client_secret", "error": True, "done": True})
                     return

                # Increment usage BEFORE copy starts to prevent bypass on error
                if not user['is_paid']:
                    db.increment_usage(client_id)
                    print(f"✅ Incremented usage for {client_id} (Attempt started). New count: {user['usage_count'] + 1}")
                else:
                    print(f"✅ Paid user {client_id} - no usage increment")
                
                # Start Copy
                worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user', status_callback=status_callback)
                worker.run_copy(src, dest, limit, excluded_list, from_p, to_p)
                
                msg_queue.put({"message": "✅ Đã hoàn tất sao chép!", "progress": 1.0, "done": True})
            except Exception as e:
                msg_queue.put({"message": f"Lỗi nghiêm trọng: {str(e)}", "error": True, "done": True})
            finally:
                is_running = False
                
        threading.Thread(target=run_worker, daemon=True).start()
        
        return JSONResponse({"status": "started"})
    
    except Exception as e:
        print(f"❌ Error in start_copy: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse({
            "status": "error",
            "message": f"Lỗi hệ thống: {str(e)}"
        })

@app.post("/api/scan")
async def scan_folder(request: Request):
    """
    Phase 1: Scans the source folder and returns a flat list of files.
    """
    try:
        # 1. Auth & Payment Check
        session_id = request.cookies.get("session_id")
        token_path = None
        if session_id:
            token_json = db.get_session(session_id)
            if token_json:
                token_path = f"/tmp/token_{session_id}.json"
                with open(token_path, "w") as f: f.write(token_json)
        
        if not token_path or not os.path.exists(token_path):
             # Fallback
             if os.path.exists('token.json'): token_path = 'token.json'
             else: return JSONResponse({"status": "error", "message": "Chưa đăng nhập!"})

        # Get User Email for Payment Check
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        creds = Credentials.from_authorized_user_file(token_path)
        service = build('oauth2', 'v2', credentials=creds)
        user_info = service.userinfo().get().execute()
        user_email = user_info.get('email')
        
        # Check Payment
        user = db.get_or_create_user(user_email)
        if not user['is_paid'] and user['usage_count'] >= 2:
             # Payment Logic
             amount = 50000
             payment_code = f"DH{hashlib.md5(user_email.encode()).hexdigest()[:8].upper()}"
             qr_url = f"https://qr.sepay.vn/img?bank=MBBank&acc=0348880746&amount={amount}&des={payment_code}"
             return JSONResponse({
                "status": "payment_required",
                "message": "Hết lượt miễn phí. Vui lòng thanh toán.",
                "qr_url": qr_url,
                "amount": amount,
                "payment_code": payment_code
             })

        # 2. Prepare Worker
        data = await request.json()
        src = data.get("source_url")
        exclude = data.get("exclude_str", "")
        excluded_list = [x.strip() for x in exclude.split(",") if x.strip()]
        
        worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user')
        # Manually load creds to worker (hacky but works since we have file)
        # Actually worker init does it if token.json exists.
        # We need to make sure worker uses OUR token_path
        # DriveCopyWorker logic prefers 'token.json' or '/tmp/token.json'.
        # We might need to copy our specific token there.
        import shutil
        shutil.copy(token_path, '/tmp/token.json')
        
        # 3. Scan
        src_id = worker.extract_folder_id(src)
        if not src_id: return JSONResponse({"status": "error", "message": "Link Drive không hợp lệ"})
        
        # Check access & Get Root Name
        try:
            worker._get_service() # Init service
            file_meta = worker.service.files().get(fileId=src_id, supportsAllDrives=True).execute()
            root_name = file_meta.get('name', 'Copied_Folder')
        except Exception as e:
            return JSONResponse({"status": "error", "message": f"Không thể truy cập folder nguồn: {e}"})

        items = worker.scan_structure(src_id)
        
        # Increment usage if not paid (Optimistic: mark used once they start scanning/copying)
        # Or we can increment in copy-batch? Better here to prevent spamming scan.
        if not user['is_paid']:
            db.increment_usage(user_email)

        return JSONResponse({
            "status": "success",
            "root_name": root_name,
            "items": items,
            "total_size": sum(i['size'] for i in items if i['type'] == 'file')
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": str(e)})

@app.post("/api/copy-batch")
async def copy_batch(request: Request):
    """
    Phase 2: Copies a batch of files.
    """
    try:
        # 1. Auth (Simplified - assume valid from Scan phase, but still check existence)
        # For Vercel, we need to re-verify or trust session.
        session_id = request.cookies.get("session_id")
        token_path = "/tmp/token.json" 
        # Ideally we re-validate session every time but for speed we rely on token file presence or re-extract
        if session_id:
             token_json = db.get_session(session_id)
             if token_json:
                 with open(token_path, "w") as f: f.write(token_json)
        
        if not os.path.exists(token_path):
            return JSONResponse({"status": "error", "message": "Auth missed"})
            
        # 2. Parse Body
        data = await request.json()
        items = data.get("items", [])
        dest_url = data.get("dest_url")
        root_folder_name = data.get("root_folder_name", "Copied_Folder")
        
        if not items or not dest_url:
            return JSONResponse({"status": "error", "message": "Missing info"})
            
        worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user')
        worker._get_service()
        
        dest_parent_id = worker.extract_folder_id(dest_url)
        
        # Ensure Root Folder Exists (Idempotent)
        # We assume the First batch might create it, or we check every time.
        # Better: check check_exists
        root_dest_id = worker.create_folder(dest_parent_id, root_folder_name)
        if not root_dest_id:
             return JSONResponse({"status": "error", "message": "Cannot create root folder"})
             
        # 3. Process Batch
        results = []
        for item in items:
            if item['type'] == 'folder':
                # Just ensure it exists
                # worker.ensure_path_exists(root_dest_id, item['path'] + [item['name']])
                # Actually, scan_structure returns folders too. 
                # If we process folders here, we just create them.
                # But 'copy_file_with_path' handles path creation.
                # So we can Ignore folder items if we use 'copy_file_with_path' for files.
                # BUT empty folders won't be created if we ignore them.
                # Let's verify:
                full_path = item['path'] + [item['name']]
                worker.ensure_path_exists(root_dest_id, full_path)
                results.append({"id": item['id'], "status": "created"})
            else:
                # File
                res = worker.copy_file_with_path(item, root_dest_id)
                results.append({"id": item['id'], "status": res['status']})
                
        return JSONResponse({"status": "success", "results": results})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"status": "error", "message": str(e)})

@app.get("/api/payment-status")
async def check_payment_status(request: Request):
    """Check if user has paid using Google email."""
    try:
        # Get token from Session or File
        session_id = request.cookies.get("session_id")
        token_path = 'token.json' # Default
        
        if session_id:
            token_json = db.get_session(session_id)
            if token_json:
                token_path = f"/tmp/token_{session_id}.json"
                with open(token_path, "w") as f:
                    f.write(token_json)
        elif os.path.exists("/tmp/token.json"):
            token_path = "/tmp/token.json"
        
        if not os.path.exists(token_path):
            return {"paid": False, "message": "Not logged in"}
        
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        creds = Credentials.from_authorized_user_file(token_path)
        oauth_service = build('oauth2', 'v2', credentials=creds)
        user_info = oauth_service.userinfo().get().execute()
        user_email = user_info.get('email')
        
        if not user_email:
            return {"paid": False, "message": "Cannot get email"}
        
        user = db.get_user(user_email)
        if not user:
            return {"paid": False, "message": "User not found"}
        
        return {
            "paid": user['is_paid'],
            "usage_count": user['usage_count']
        }
    except Exception as e:
        print(f"❌ Payment status check error: {e}")
        return {"paid": False, "message": str(e)}

@app.post("/api/mark-paid")
async def mark_paid(request: Request):
    """Endpoint to manually mark user as paid after payment verification."""
    try:
        # Get token from Session or File
        session_id = request.cookies.get("session_id")
        token_path = 'token.json' # Default
        
        if session_id:
            token_json = db.get_session(session_id)
            if token_json:
                token_path = f"/tmp/token_{session_id}.json"
                with open(token_path, "w") as f:
                    f.write(token_json)
        elif os.path.exists("/tmp/token.json"):
            token_path = "/tmp/token.json"
        
        if not os.path.exists(token_path):
            return {"status": "error", "message": "Not logged in"}
        
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        creds = Credentials.from_authorized_user_file(token_path)
        oauth_service = build('oauth2', 'v2', credentials=creds)
        user_info = oauth_service.userinfo().get().execute()
        user_email = user_info.get('email')
        
        if not user_email:
            return {"status": "error", "message": "Cannot get email"}
        
        db.mark_as_paid(user_email)
        print(f"✅ Manually marked {user_email} as paid")
        return {"status": "success", "message": "User marked as paid"}
    except Exception as e:
        print(f"❌ Mark paid error: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/debug/database")
async def debug_database(request: Request):
    """Debug endpoint to view database contents."""
    try:
        # Get all users from database
        if hasattr(db, 'database_url') and db.database_url:
            # PostgreSQL
            import psycopg2
            conn = psycopg2.connect(db.database_url)
            cursor = conn.cursor()
            cursor.execute('SELECT client_id, usage_count, is_paid, created_at, updated_at FROM user_usage ORDER BY created_at DESC LIMIT 20')
            users = cursor.fetchall()
            conn.close()
            
            return {
                "database_type": "PostgreSQL (Neon)",
                "total_users": len(users),
                "users": [
                    {
                        "email": row[0],
                        "usage_count": row[1],
                        "is_paid": row[2],
                        "created_at": str(row[3]),
                        "updated_at": str(row[4])
                    }
                    for row in users
                ]
            }
        else:
            # SQLite
            import sqlite3
            conn = sqlite3.connect(db.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT client_id, usage_count, is_paid, created_at, updated_at FROM user_usage ORDER BY created_at DESC LIMIT 20')
            users = cursor.fetchall()
            conn.close()
            
            return {
                "database_type": "SQLite",
                "database_path": db.db_path,
                "total_users": len(users),
                "users": [
                    {
                        "email": row[0],
                        "usage_count": row[1],
                        "is_paid": bool(row[2]),
                        "created_at": row[3],
                        "updated_at": row[4]
                    }
                    for row in users
                ]
            }
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/sepay-webhook")
async def sepay_webhook(request: Request, authorization: str = Header(None)):
    """Webhook endpoint to receive payment notifications from SePay."""
    try:
        # Verify API key
        if not authorization:
            print("❌ Webhook: Missing authorization")
            return {"status": "error", "message": "Unauthorized"}
        
        # Extract API key (format: "apikey YOUR_KEY" or "Bearer YOUR_KEY" or just "YOUR_KEY")
        api_key = authorization.replace('apikey ', '').replace('Bearer ', '').strip()
        
        if api_key != WEBHOOK_API_KEY:
            print(f"❌ Webhook: Invalid API key")
            return {"status": "error", "message": "Unauthorized"}
        
        # Parse webhook data
        data = await request.json()
        print(f"📥 Webhook received: {json.dumps(data, indent=2)}")
        
        # Extract payment info
        transfer_content = data.get('transferContent', '') or data.get('content', '')
        transfer_amount = int(data.get('transferAmount', 0) or data.get('amount', 0))
        
        print(f"💰 Amount: {transfer_amount}, Content: {transfer_content}")
        
        # Extract payment code (format: DH{hash})
        import re
        match = re.search(r'DH([A-F0-9]{8})', transfer_content.upper())
        if not match:
            print(f"❌ Invalid payment code format")
            return {"status": "error", "message": "Invalid payment code"}
        
        payment_hash = match.group(1).lower()
        print(f"🔑 Payment hash: {payment_hash}")
        
        # Verify amount
        if transfer_amount < 50000:
            print(f"❌ Insufficient amount: {transfer_amount}")
            return {"status": "error", "message": "Insufficient amount"}
        
        # Find user by matching email hash
        # Search all users and match hash
        if hasattr(db, 'database_url') and db.database_url:
            # PostgreSQL
            import psycopg2
            conn = psycopg2.connect(db.database_url)
            cursor = conn.cursor()
            cursor.execute('SELECT client_id FROM user_usage WHERE is_paid = FALSE')
            users = cursor.fetchall()
            conn.close()
        else:
            # SQLite
            import sqlite3
            conn = sqlite3.connect(db.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT client_id FROM user_usage WHERE is_paid = 0')
            users = cursor.fetchall()
            conn.close()
        
        # Find matching user
        for (client_id,) in users:
            user_hash = hashlib.md5(client_id.encode()).hexdigest()[:8].lower()
            if user_hash == payment_hash:
                db.mark_as_paid(client_id)
                print(f"✅ Payment confirmed for: {client_id}")
                return {"status": "success", "message": "Payment confirmed", "client_id": client_id}
        
        return {"status": "ignored", "message": "No matching payment found"}
        
    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

def check_sepay_payment(payment_code_hash):
    """
    Manually check SePay API for a transaction with specific content.
    Returns: True if found (and valid), False otherwise.
    """
    if not SEPAY_API_TOKEN:
        print("❌ Missing SEPAY_API_TOKEN. Cannot check manually.")
        return False

    try:
        url = "https://my.sepay.vn/userapi/transactions/list"
        headers = {
            "Authorization": f"Bearer {SEPAY_API_TOKEN}",
            "Content-Type": "application/json"
        }
        # Iterate pages if needed, but for now check recent 50
        params = {"limit": 50} 
        
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        
        if not data.get("status") == 200:
             print(f"❌ SePay API Error: {data.get('message')}")
             return False

        transactions = data.get("transactions", [])
        print(f"🔍 Checking {len(transactions)} recent transactions for code hash: {payment_code_hash}")
        
        for trans in transactions:
            content = trans.get("transaction_content", "")
            amount = float(trans.get("amount_in", 0))
            
            # Check content for "DH{hash}"
            import re
            match = re.search(r'DH([A-F0-9]{8})', content.upper())
            if match:
                found_hash = match.group(1).lower()
                if found_hash == payment_code_hash.lower() and amount >= 50000:
                    print(f"✅ Found matching transaction in API! Content: {content}")
                    return True
                    
        return False

    except Exception as e:
        print(f"❌ Error checking SePay API: {e}")
        return False

@app.post("/api/force-check-payment")
async def force_check_payment(request: Request):
    """Endpoint triggered by user 'Check Payment' button."""
    try:
        # Get Token/User
        session_id = request.cookies.get("session_id")
        token_path = 'token.json'
        if session_id:
            token_json = db.get_session(session_id)
            if token_json:
                token_path = f"/tmp/token_{session_id}.json"
                with open(token_path, "w") as f: f.write(token_json)
        
        if not os.path.exists(token_path):
             return {"status": "error", "message": "Not logged in"}

        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        creds = Credentials.from_authorized_user_file(token_path)
        oauth_service = build('oauth2', 'v2', credentials=creds)
        user_info = oauth_service.userinfo().get().execute()
        user_email = user_info.get('email')
        
        if not user_email: return {"status": "error", "message": "No email"}
        
        user = db.get_user(user_email)
        if user and user['is_paid']:
            return {"status": "success", "message": "Already paid!", "paid": True}

        # Calculate expected hash
        payment_code_hash = hashlib.md5(user_email.encode()).hexdigest()[:8].lower()
        
        # Check API
        is_paid = check_sepay_payment(payment_code_hash)
        
        if is_paid:
            db.mark_as_paid(user_email)
            return {"status": "success", "message": "Payment verified!", "paid": True}
        else:
            return {
                "status": "not_found", 
                "message": "Payment not found yet. Please wait a few minutes or check your transfer content.",
                "paid": False
            }

    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/stream_logs")
async def stream_logs(request: Request):
    """Streams logs from queue using SSE."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
                
            try:
                # Non-blocking get
                data = msg_queue.get(timeout=0.5)
                yield json.dumps(data)
                if data.get("done"):
                    break
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue
                
    return EventSourceResponse(event_generator())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
