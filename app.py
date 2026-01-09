import streamlit as st
import os
import time
import json
from drive_utils import DriveCopyWorker

# === CONFIGURATION ===
PAGE_TITLE = "Drive Copy Pro"
PAGE_ICON = "📂"

st.set_page_config(page_title=PAGE_TITLE, page_icon=PAGE_ICON, layout="wide")

# === CUSTOM CSS (PREMIUM DARK GOLD THEME) ===
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif;
        color: #e2e8f0;
        background-color: #0f172a;
    }

    /* Main Background */
    .stApp {
        background: radial-gradient(circle at top left, #1e293b, #0f172a);
    }

    /* Hide default header */
    header {visibility: hidden;}

    /* Top Navigation Bar */
    .top-nav {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 1rem 2rem;
        background: transparent;
        margin-bottom: 2rem;
    }
    .brand {
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 1.5rem;
        font-weight: 700;
        color: #F59E0B; /* Gold */
    }
    .brand span {
        font-size: 0.8rem;
        color: #94a3b8;
        font-weight: 400;
        margin-left: 5px;
    }
    .vip-badge {
        background: linear-gradient(90deg, #F59E0B, #D97706);
        color: white;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 600;
        box-shadow: 0 2px 10px rgba(245, 158, 11, 0.3);
    }

    /* Main Card */
    .main-card {
        background-color: #1e293b;
        border: 1px solid #334155;
        border-radius: 16px;
        padding: 40px;
        max-width: 800px;
        margin: 0 auto;
        box-shadow: 0 10px 40px rgba(0,0,0,0.5);
    }
    
    /* Card Header */
    .card-title {
        display: flex;
        align-items: center;
        gap: 15px;
        margin-bottom: 30px;
        border-bottom: 1px solid #334155;
        padding-bottom: 20px;
    }
    .card-icon {
        background: #F59E0B;
        width: 40px;
        height: 40px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1.5rem;
        color: white;
    }
    .status-badge {
        background-color: rgba(16, 185, 129, 0.2);
        color: #34d399;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.8rem;
        margin-left: auto;
        border: 1px solid rgba(16, 185, 129, 0.3);
    }

    /* Input Labels */
    .input-label {
        color: #F59E0B;
        font-size: 0.9rem;
        font-weight: 600;
        margin-bottom: 8px;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    /* Inputs Overrides */
    .stTextInput > div > div > input, .stNumberInput > div > div > input {
        background-color: #0f172a !important;
        border: 1px solid #334155 !important;
        color: #e2e8f0 !important;
        border-radius: 8px !important;
        padding: 10px 12px !important;
    }
    .stTextInput > div > div > input:focus, .stNumberInput > div > div > input:focus {
        border-color: #F59E0B !important;
        box-shadow: 0 0 0 1px #F59E0B !important;
    }

    /* Instruction Box */
    .instruction-box {
        background-color: #2D2820; /* Dark Gold/Brown tint */
        border: 1px solid #F59E0B;
        border-radius: 12px;
        padding: 20px;
        margin-top: 30px;
        color: #cbd5e1;
        font-size: 0.9rem;
    }
    .instruction-list {
        margin: 0;
        padding-left: 20px;
        line-height: 1.6;
    }
    
    /* Login Button Area */
    .login-area {
        background: rgba(255, 255, 255, 0.05);
        padding: 15px;
        border-radius: 12px;
        border: 1px solid #334155;
        margin-bottom: 20px;
        text-align: center;
    }

    /* Button */
    .stButton > button {
        background: linear-gradient(90deg, #F59E0B, #ea580c) !important;
        border: none !important;
        color: white !important;
        font-weight: 600 !important;
        padding: 12px 24px !important;
        border-radius: 8px !important;
        width: 100%;
        margin-top: 20px;
        box-shadow: 0 4px 15px rgba(234, 88, 12, 0.3);
        transition: all 0.2s;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(234, 88, 12, 0.4);
    }

    /* Sidebar adjustments */
    section[data-testid="stSidebar"] {
        background-color: #0f172a;
        border-right: 1px solid #334155;
    }
    
    </style>
""", unsafe_allow_html=True)

# === TOP NAV ===
st.markdown("""
    <div class="top-nav">
        <div class="brand">
            📂 Drive Copy Pro
            <span>Premium Edition</span>
        </div>
        <div class="vip-badge">👑 VIP ACCESS</div>
    </div>
""", unsafe_allow_html=True)

# === SIDEBAR CONFIGURATION ===
with st.sidebar:
    st.title("👤 Tài khoản Google")
    
    auth_file = 'client_secret.json'
    
    if os.path.exists('token.json'):
         st.success("✅ Đã đăng nhập")
         st.markdown("Bạn đang sử dụng quyền truy cập Cá nhân.")
         if st.button("Đăng xuất"):
            os.remove('token.json')
            st.rerun()
    else:
        st.warning("⚠️ Chưa đăng nhập")
        st.markdown("Vui lòng đăng nhập để sử dụng tính năng sao chép.")
        st.markdown("---")
        if not os.path.exists(auth_file):
            st.error("❌ Thiếu `client_secret.json`")
            st.caption("Tải file từ Google Cloud Console và bỏ vào thư mục.")
        else:
             st.info("Sẵn sàng kết nối")
    
    st.divider()
    with st.expander("🛠️ Debug"):
        if st.checkbox("Hiển thị log chi tiết"):
            st.session_state['show_debug'] = True
        else:
             st.session_state['show_debug'] = False


# === MAIN LOGIC ===
_, center, _ = st.columns([1, 6, 1])

with center:
    st.markdown('<div class="main-card">', unsafe_allow_html=True)
    
    # Header logic
    status_text = "Chờ đăng nhập..."
    if os.path.exists('token.json'):
        status_text = "Đã xác thực"
        
    st.markdown(f"""
        <div class="card-title">
            <div class="card-icon">👤</div>
            <div>
                <div style="font-weight: 600; font-size: 1.1rem; color: #fff;">Google Drive Copy Tool</div>
                <div style="font-size: 0.85rem; color: #94a3b8;">Sao chép tài nguyên bằng tài khoản của bạn</div>
            </div>
            <div class="status-badge">● {status_text}</div>
        </div>
    """, unsafe_allow_html=True)

    # Inputs
    st.markdown('<div class="input-label">☁️ Your Drive (Drive đích - Nơi lưu)</div>', unsafe_allow_html=True)
    dest_url = st.text_input("dest", placeholder="Nhập đường link folder Google Drive của bạn", label_visibility="collapsed")
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown('<div class="input-label">📂 Shared Drive (Drive nguồn - Cần copy)</div>', unsafe_allow_html=True)
    source_url = st.text_input("src", placeholder="Nhập đường link folder Google Drive shared (Anyone with link)", label_visibility="collapsed")
    st.markdown("<br>", unsafe_allow_html=True)
    
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="input-label">⏱️ Từ trang</div>', unsafe_allow_html=True)
        from_page = st.number_input("from", min_value=0, value=0, label_visibility="collapsed")
    with c2:
        st.markdown('<div class="input-label">🏁 Đến trang</div>', unsafe_allow_html=True)
        to_page = st.number_input("to", min_value=0, value=0, label_visibility="collapsed")
    st.markdown("<br>", unsafe_allow_html=True)
    
    st.markdown('<div class="input-label">💾 Tổng dung lượng tối đa (GB)</div>', unsafe_allow_html=True)
    limit_size = st.number_input("limit", min_value=0, value=500, label_visibility="collapsed")
    st.markdown("<br>", unsafe_allow_html=True)
    
    st.markdown('<div class="input-label">🚫 Bỏ file, folder có chứa (tùy chọn)</div>', unsafe_allow_html=True)
    exclude_str = st.text_input("exclude", placeholder="vd: backup, temp", label_visibility="collapsed")

    # Start Button
    is_logged_in = os.path.exists('token.json')
    btn_label = "🚀 BẮT ĐẦU SAO CHÉP" if is_logged_in else "🔑 ĐĂNG NHẬP VÀ SAO CHÉP"
    
    start_btn = st.button(btn_label)

    # Instructions Box
    st.markdown("""
        <div class="instruction-box">
            <span class="instruction-icon">💡 Hướng dẫn</span>
            <ul class="instruction-list">
                <li>Công cụ sử dụng <strong>quyền truy cập của chính bạn</strong> để sao chép.</li>
                <li>Hỗ trợ copy Link công khai (Anyone with the link) mà KHÔNG cần share quyền manual.</li>
                <li>Lần đầu chạy sẽ mở cửa sổ trình duyệt yêu cầu đăng nhập Google.</li>
            </ul>
        </div>
    """, unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

# Logic Execution
if start_btn:
    if not os.path.exists('client_secret.json'):
         st.toast("⚠️ Lỗi: Không tìm thấy file client_secret.json!", icon="❌")
    elif not source_url or not dest_url:
        st.toast("⚠️ Vui lòng nhập đầy đủ Link!", icon="⚠️")
    else:
        status_bar = st.progress(0, text="Đang khởi tạo...")
        
        def update_ui(msg, prog=None, is_error=False):
            if is_error:
                st.toast(msg, icon="❌")
            if prog is not None:
                status_bar.progress(prog, text=msg)

        excluded_list = [s.strip() for s in exclude_str.split(",") if s.strip()]
        
        # Force User Mode
        worker = DriveCopyWorker('client_secret.json', auth_mode='user', status_callback=update_ui)
        
        with st.spinner("Đang xử lý (Kiểm tra cửa sổ đăng nhập nếu cần)..."):
            worker.run_copy(source_url, dest_url, limit_size, excluded_list, from_page, to_page)
