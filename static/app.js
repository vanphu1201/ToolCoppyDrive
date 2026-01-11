// DOM Elements
const authSection = document.getElementById('auth-section');
const authWarning = document.getElementById('auth-warning');
const btnLogin = document.getElementById('btn-login');
const startBtn = document.getElementById('start-btn');
const authStatus = document.getElementById('auth-status');
const form = document.getElementById('copy-form');
const progressContainer = document.getElementById('progress-container');
const progressFill = document.getElementById('progress-fill');
const logText = document.getElementById('log-text');

// Payment Modal Elements
const paymentModal = document.getElementById('payment-modal');
const qrCodeImg = document.getElementById('qr-code-img');
const paymentMessage = document.getElementById('payment-message');
const paymentAmount = document.getElementById('payment-amount');
const paymentCode = document.getElementById('payment-code');
const paymentSuccess = document.getElementById('payment-success');
const closeModalBtn = document.getElementById('close-modal-btn');

let paymentCheckInterval = null;

// Check Auth Status
function checkAuth() {
    fetch('/api/check_auth')
        .then(r => r.json())
        .then(data => {
            if (data.authenticated) {
                // Logged In
                authStatus.innerHTML = '<i class="fas fa-check-circle"></i> Sẵn sàng';
                authStatus.style.color = "#10B981";
                authStatus.style.background = "rgba(16, 185, 129, 0.1)";
                authStatus.style.borderColor = "rgba(16, 185, 129, 0.2)";

                authSection.style.display = 'none';
                startBtn.disabled = false;
            } else {
                // Not Logged In
                authStatus.innerHTML = '<i class="fas fa-times-circle"></i> Chưa kết nối';
                authStatus.style.color = "#EF4444";
                authStatus.style.background = "rgba(239, 68, 68, 0.1)";
                authStatus.style.borderColor = "rgba(239, 68, 68, 0.2)";

                authSection.style.display = 'block';
                startBtn.disabled = true;
            }
        })
        .catch(err => console.error("Auth Check Error:", err));
}

// Polling
setInterval(checkAuth, 3000);
checkAuth();

// Login Button
if (btnLogin) {
    btnLogin.addEventListener('click', async (e) => {
        e.preventDefault();
        btnLogin.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang chuyển hướng...';
        btnLogin.disabled = true;

        try {
            const res = await fetch('/api/login', { method: 'POST' });
            const data = await res.json();

            if (data.status === 'redirect_required' || data.status === 'manual_auth_required') {
                window.location.href = data.auth_url;
            } else if (data.status === 'already_logged_in') {
                checkAuth();
            } else {
                alert("Lỗi: " + data.message);
                btnLogin.innerHTML = '<i class="fab fa-google"></i> Đăng nhập';
                btnLogin.disabled = false;
            }
        } catch (err) {
            alert("Lỗi kết nối server: " + err);
            btnLogin.innerHTML = '<i class="fab fa-google"></i> Đăng nhập';
            btnLogin.disabled = false;
        }
    });
}

// Form Submit Logic
// Form Submit Logic
if (form) {
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(form);
        const destUrl = formData.get('dest_url');

        startBtn.disabled = true;
        startBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ĐANG XỬ LÝ...';
        progressContainer.style.display = 'block';
        logText.textContent = "Đang kết nối & quét dữ liệu nguồn...";
        progressFill.style.width = "0%";

        // Params just for Scan
        const scanBody = {
            source_url: formData.get('source_url'),
            exclude_str: formData.get('exclude_str')
        };

        try {
            // PHASE 1: SCAN
            const scanResponse = await fetch('/api/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(scanBody)
            });
            const scanResult = await scanResponse.json();

            if (scanResult.status === 'payment_required') {
                showPaymentModal(scanResult);
                resetUI();
                return;
            } else if (scanResult.status === 'error') {
                logText.textContent = "Lỗi: " + scanResult.message;
                resetUI();
                return;
            } else if (scanResult.status === 'success') {
                const items = scanResult.items;
                const rootName = scanResult.root_name;
                const totalItems = items.length;
                let processedCount = 0;

                logText.textContent = `Đã tìm thấy ${totalItems} mục. Bắt đầu sao chép...`;
                progressFill.style.width = "5%";

                // PHASE 2: BATCH COPY
                const BATCH_SIZE = 5; // Safe for Vercel (10s limit)

                for (let i = 0; i < totalItems; i += BATCH_SIZE) {
                    const chunk = items.slice(i, i + BATCH_SIZE);

                    try {
                        const batchRes = await fetch('/api/copy-batch', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                items: chunk,
                                dest_url: destUrl,
                                root_folder_name: rootName
                            })
                        });
                        const batchData = await batchRes.json();

                        if (batchData.status !== 'success') {
                            console.error("Batch Error:", batchData);
                            logText.textContent = `Lỗi sao chép batch ${Math.ceil(i / BATCH_SIZE) + 1}: ${batchData.message}`;
                            // Optional: Break or Continue? Continue best effort.
                        } else {
                            processedCount += chunk.length;
                            const percent = Math.min(Math.round((processedCount / totalItems) * 100), 99);
                            progressFill.style.width = percent + "%";
                            logText.textContent = `Đang sao chép... ${processedCount}/${totalItems}`;
                        }

                    } catch (batchErr) {
                        console.error("Network Error Batch:", batchErr);
                        logText.textContent = "Lỗi mạng khi copy, đang thử tiếp...";
                    }
                }

                // DONE
                progressFill.style.width = "100%";
                logText.textContent = "✅ Hoàn tất quá trình sao chép!";
                startBtn.disabled = false;
                startBtn.innerHTML = '<i class="fas fa-rocket"></i> KÍCH HOẠT SAO CHÉP';
            }

        } catch (err) {
            logText.textContent = "Lỗi kết nối: " + err;
            resetUI();
        }
    });
}

function resetUI() {
    startBtn.disabled = false;
    startBtn.innerHTML = '<i class="fas fa-rocket"></i> KÍCH HOẠT SAO CHÉP';
}

// Payment Modal Functions
function showPaymentModal(data) {
    paymentMessage.textContent = data.message;
    qrCodeImg.src = data.qr_url;
    paymentAmount.textContent = data.amount.toLocaleString();
    paymentCode.textContent = data.payment_code;
    paymentModal.style.display = 'flex';
    paymentSuccess.style.display = 'none';

    // Start polling for payment status
    paymentCheckInterval = setInterval(checkPaymentStatus, 3000);
}

const checkPaymentBtn = document.getElementById('check-payment-btn');
if (checkPaymentBtn) {
    checkPaymentBtn.addEventListener('click', checkPaymentManual);
}

async function checkPaymentManual() {
    if (checkPaymentBtn.disabled) return;

    checkPaymentBtn.disabled = true;
    const originalText = checkPaymentBtn.innerHTML;
    checkPaymentBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Đang kỹ tra...';

    try {
        const res = await fetch('/api/force-check-payment', { method: 'POST' });
        const data = await res.json();

        if (data.status === 'success') {
            paymentSuccess.style.display = 'block';
            paymentSuccess.querySelector('p').textContent = "Đã xác nhận thanh toán thành công!";
            clearInterval(paymentCheckInterval);
            setTimeout(() => {
                paymentModal.style.display = 'none';
                location.reload();
            }, 1000);
        } else {
            alert(data.message || "Chưa tìm thấy thanh toán. Vui lòng thử lại sau 30s.");
        }
    } catch (err) {
        alert("Lỗi kết nối: " + err);
    } finally {
        checkPaymentBtn.disabled = false;
        checkPaymentBtn.innerHTML = originalText;
    }
}

function checkPaymentStatus() {
    fetch('/api/payment-status')
        .then(r => r.json())
        .then(data => {
            if (data.paid) {
                paymentSuccess.style.display = 'block';
                clearInterval(paymentCheckInterval);
                setTimeout(() => {
                    paymentModal.style.display = 'none';
                    location.reload();
                }, 2000);
            }
        })
        .catch(err => console.error('Payment check error:', err));
}


closeModalBtn.addEventListener('click', () => {
    paymentModal.style.display = 'none';
    if (paymentCheckInterval) {
        clearInterval(paymentCheckInterval);
    }
});

// Close modal on outside click
paymentModal.addEventListener('click', (e) => {
    if (e.target === paymentModal) {
        paymentModal.style.display = 'none';
        if (paymentCheckInterval) {
            clearInterval(paymentCheckInterval);
        }
    }
});
