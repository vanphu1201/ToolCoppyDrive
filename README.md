# Google Drive Copy Web App 📂

Ứng dụng web giúp sao chép thư mục Google Drive (kể cả thư mục Shared) sang Drive của bạn bằng cách sử dụng **Service Account** để vượt qua các giới hạn API cá nhân.

## 🛠️ Cài đặt

1. **Chuẩn bị môi trường Python**
   ```bash
   pip install -r requirements.txt
   ```

2. **Cấu hình Service Account**
   - Vào [Google Cloud Console](https://console.cloud.google.com/).
   - Tạo Project mới (hoặc chọn Project có sẵn).
   - Enable **Google Drive API**.
   - Vào **Credentials** -> **Create Credentials** -> **Service Account**.
   - Tạo Key mới (JSON) và tải về.
   - Đổi tên file thành `service_account.json` và chép vào thư mục mã nguồn này.

3. **Chạy ứng dụng**
   ```bash
   streamlit run app.py
   ```

## 📖 Hướng dẫn sử dụng

1. **Cấp quyền truy cập**:
   - Mở file `service_account.json` bằng Notepad để xem địa chỉ email `client_email` (có dạng `...@project-id.iam.gserviceaccount.com`).
   - Vào Google Drive:
     - Chia sẻ **Thư mục Nguồn** (Folder Source) cho email này (quyền Xem).
     - Chia sẻ **Thư mục Đích** (Folder Destination) cho email này (quyền Chỉnh sửa/Editor).

2. **Sử dụng trên Web**:
   - Dán Link Source và Link Destination vào ô nhập.
   - Bấm **Bắt đầu sao chép**.
   - Theo dõi tiến độ trên màn hình.

## ⚠️ Lưu ý
- Nếu ứng dụng báo lỗi "Không tìm thấy file", hãy chắc chắn `service_account.json` đang nằm cùng thư mục với `app.py`.
- Nếu báo lỗi "User Rate Limit Exceeded", ứng dụng sẽ tự động chờ 5s và thử lại.
