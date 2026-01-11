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
if (form) {
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const formData = new FormData(form);

        startBtn.disabled = true;
        startBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ĐANG XỬ LÝ...';
        progressContainer.style.display = 'block';
        logText.textContent = "Đang khởi tạo...";
        progressFill.style.width = "0%";

        const params = new URLSearchParams(formData);

        try {
            const response = await fetch('/api/start_copy?' + params.toString(), { method: 'POST' });
            const result = await response.json();

            if (result.status === 'started') {
                const eventSource = new EventSource('/api/stream_logs');

                eventSource.onmessage = function (event) {
                    const data = JSON.parse(event.data);
                    if (data.done) {
                        eventSource.close();
                        startBtn.disabled = false;
                        startBtn.innerHTML = '<i class="fas fa-rocket"></i> KÍCH HOẠT SAO CHÉP';
                        logText.textContent = data.message || "Hoàn tất!";
                        progressFill.style.width = "100%";
                        return;
                    }
                    logText.textContent = data.message;
                    if (data.progress) {
                        progressFill.style.width = (data.progress * 100) + '%';
                    }
                };

                eventSource.onerror = function () {
                    eventSource.close();
                    startBtn.disabled = false;
                    startBtn.innerHTML = '<i class="fas fa-redo"></i> THỬ LẠI';
                }
            } else if (result.status === 'payment_required') {
                // Show payment modal
                showPaymentModal(result);
                startBtn.disabled = false;
                startBtn.innerHTML = '<i class="fas fa-rocket"></i> KÍCH HOẠT SAO CHÉP';
                progressContainer.style.display = 'none';
            } else if (result.status === 'error') {
                logText.textContent = "Lỗi: " + result.message;
                startBtn.disabled = false;
                startBtn.innerHTML = '<i class="fas fa-rocket"></i> KÍCH HOẠT SAO CHÉP';
                progressContainer.style.display = 'none';
            } else {
                logText.textContent = "Lỗi: " + result.message;
                startBtn.disabled = false;
                startBtn.innerHTML = '<i class="fas fa-rocket"></i> KÍCH HOẠT SAO CHÉP';
            }
        } catch (err) {
            logText.textContent = "Lỗi mạng: " + err;
            startBtn.disabled = false;
            startBtn.innerHTML = '<i class="fas fa-rocket"></i> KÍCH HOẠT SAO CHÉP';
            progressContainer.style.display = 'none';
        }
    });
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
