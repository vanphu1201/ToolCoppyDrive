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

app = FastAPI()

# Mount Static & Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Global State (Single User Desktop App Mode)
msg_queue = queue.Queue()
is_running = False

# Database
db = UsageDatabase()

# Webhook API Key - Tạo key ngẫu nhiên hoặc dùng key cố định
# Bạn sẽ điền key này vào form SePay
WEBHOOK_API_KEY = os.environ.get('SEPAY_WEBHOOK_KEY', 'SEPAY_' + secrets.token_urlsafe(32))
print(f"\n{'='*60}")
print(f"🔑 SEPAY WEBHOOK API KEY: {WEBHOOK_API_KEY}")
print(f"📋 Copy key này và điền vào form SePay (trường API Key)")
print(f"{'='*60}\n")

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
def check_auth():
    """Check if token.json exists (local or tmp)."""
    exists = os.path.exists('token.json') or os.path.exists('/tmp/token.json')
    return {"authenticated": exists}

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
async def auth_callback(code: str):
    """Handles the OAuth Callback from Google."""
    global auth_worker
    try:
        if not auth_worker:
            # Re-init if lost (stateless)
             auth_worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user')
             
             # Re-inject flow with SAME Redirect URI to exchange code
             # We need to reconstruct the callback URL from the current request to match
             base_url = str(request.base_url).rstrip("/")
             # Note: request.base_url in FastAPI/Starlette might be http even if behind https proxy on Vercel unless trusted hosts are set.
             # However, for exchange_code, the redirect_uri string just needs to match what was sent.
             # Ideally check 'x-forwarded-proto' if needed, but for now try request.base_url
             
             # Force HTTPS if we are on Vercel (heuristic)
             if "vercel.app" in base_url and "http://" in base_url:
                 base_url = base_url.replace("http://", "https://")

             REDIRECT_URI = f"{base_url}/api/callback"
             auth_worker.get_auth_url(redirect_uri=REDIRECT_URI)
             
        creds = auth_worker.exchange_code(code)
        
        if creds:
            # Redirect back to Home
            return HTMLResponse("<script>window.location.href='/';</script>")
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
        # Check Auth first
        token_path = 'token.json' 
        if os.path.exists("/tmp/token.json"): 
            token_path = "/tmp/token.json"
        
        # Verify user is logged in
        if not os.path.exists(token_path):
            return JSONResponse({
                "status": "error", 
                "message": "Bạn chưa đăng nhập Google Drive. Vui lòng đăng nhập trước!"
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
        
        # Payment gate: if usage >= 2 and not paid, require payment
        if user['usage_count'] >= 2 and not user['is_paid']:
            # Generate payment QR code
            payment_code = f"DH{hashlib.md5(client_id.encode()).hexdigest()[:8].upper()}"
            amount = 50000
            
            # SePay QR format with user's bank info
            bank_name = "MBBank"
            account_number = "0348880746"
            account_name = "TRAN VAN PHU"
            
            qr_url = f"https://qr.sepay.vn/img?bank={bank_name}&acc={account_number}&amount={amount}&des={payment_code}"
            
            return JSONResponse({
                "status": "payment_required",
                "message": "Bạn đã sử dụng hết 2 lần miễn phí. Vui lòng thanh toán để tiếp tục.",
                "qr_url": qr_url,
                "amount": amount,
                "payment_code": payment_code,
                "account_name": account_name
            })
    
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

                worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user', status_callback=status_callback)
                worker.run_copy(src, dest, limit, excluded_list, from_p, to_p)
                
                # Increment usage count after successful copy
                db.increment_usage(client_id)
                
                msg_queue.put({"message": "✅ Đã hoàn tất sao chép!", "progress": 1.0, "done": True})
            except Exception as e:
                msg_queue.put({"message": f"Lỗi nghiêm trọng: {str(e)}", "error": True, "done": True})
            finally:
                is_running = False
                
        threading.Thread(target=run_worker, daemon=True).start()
        
        # Return response
        return JSONResponse({"status": "started"})
    
    except Exception as e:
        print(f"❌ Error in start_copy: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse({
            "status": "error",
            "message": f"Lỗi hệ thống: {str(e)}"
        })

@app.get("/api/payment-status")
async def check_payment_status(request: Request):
    """Check if user has paid using Google email."""
    try:
        # Get email from token
        token_path = 'token.json'
        if os.path.exists("/tmp/token.json"):
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
    """Debug endpoint to manually mark user as paid."""
    client_id = request.cookies.get('client_id')
    if not client_id:
        return {"status": "error", "message": "No client ID"}
    
    db.mark_as_paid(client_id)
    return {"status": "success", "message": "User marked as paid"}

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
        
        print(f"❌ No matching user found for hash: {payment_hash}")
        return {"status": "ignored", "message": "No matching payment found"}
        
    except Exception as e:
        print(f"❌ Webhook error: {str(e)}")
        import traceback
        traceback.print_exc()
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
