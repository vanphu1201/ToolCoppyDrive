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
async def login_google():
    """Returns Auth URL for manual Vercel flow."""
    global auth_worker
    try:
        # Check if already logged in
        if os.path.exists('token.json') or os.path.exists('/tmp/token.json'):
             return {"status": "already_logged_in"}

        if not os.path.exists(AUTH_FILE_PATH):
             return {"status": "error", "message": "Missing client_secret.json"}

        # Init worker for Auth
        auth_worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user')
        auth_url = auth_worker.get_auth_url()
        
        return {"status": "manual_auth_required", "auth_url": auth_url}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/submit_code")
async def submit_code(request: Request):
    global auth_worker
    try:
        data = await request.json()
        code = data.get("code")
        
        if not auth_worker:
             # Re-init if lost (stateless serverless might break this, but usually okay for warm containers)
             # If completely stateless, we need to rebuild flow from session, but let's try simple global first
             auth_worker = DriveCopyWorker(AUTH_FILE_PATH, auth_mode='user')
             auth_worker.get_auth_url() # Re-init flow
             
        success = auth_worker.submit_code(code)
        if success:
            # Copy token to tmp if needed
            if os.path.exists('token.json'):
                with open('token.json', 'r') as f:
                    token_data = f.read()
                with open('/tmp/token.json', 'w') as f:
                    f.write(token_data)
                    
            return {"status": "success"}
        return {"status": "error", "message": "Code verification failed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
