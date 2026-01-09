from fastapi import FastAPI, Request
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
from drive_utils import DriveCopyWorker

app = FastAPI()

# Mount Static & Templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Global State (Single User Desktop App Mode)
msg_queue = queue.Queue()
is_running = False

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
        return {"status": "error", "message": "Tiến trình khác đang chạy!"}
    
    # Check Auth
    token_path = 'token.json' 
    if os.path.exists("/tmp/token.json"): token_path = "/tmp/token.json"
    
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
            
            msg_queue.put({"message": "✅ Đã hoàn tất sao chép!", "progress": 1.0, "done": True})
        except Exception as e:
            msg_queue.put({"message": f"Lỗi nghiêm trọng: {str(e)}", "error": True, "done": True})
        finally:
            is_running = False
            
    threading.Thread(target=run_worker, daemon=True).start()
    
    return {"status": "started"}

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
