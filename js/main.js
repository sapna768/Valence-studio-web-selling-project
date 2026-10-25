document.addEventListener('DOMContentLoaded', () => {
    // 1. ROI Estimator Logic
    const calcOptions = document.querySelectorAll('.calc-option');
    const totalDisplay = document.getElementById('totalEstimate');

    calcOptions.forEach(option => {
        option.addEventListener('click', () => {
            calcOptions.forEach(o => o.classList.remove('active', 'border-blue-600'));
            option.classList.add('active', 'border-blue-600');

            const price = option.getAttribute('data-price');
            if (totalDisplay) {
                totalDisplay.textContent = `$${parseInt(price).toLocaleString()}`;
            }
        });
    });

    // 2. Contact Form Handler (Connected to Flask API)
    const contactForm = document.getElementById('contactForm');
    const submitBtn = document.getElementById('submitBtn');
    const formStatus = document.getElementById('formStatus');

    if (contactForm) {
        contactForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const name = document.getElementById('name').value;
            const email = document.getElementById('email').value;
            const service = document.getElementById('service').value;
            const details = document.getElementById('details').value;

            submitBtn.disabled = true;
            submitBtn.innerText = "Sending Inquiry...";

            try {
                const response = await fetch('/api/contact', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, email, service, details })
                });

                const data = await response.json();

                formStatus.classList.remove('hidden', 'text-red-500', 'text-green-600');
                if (response.ok && data.success) {
                    formStatus.classList.add('text-green-600');
                    formStatus.textContent = data.message;
                    contactForm.reset();
                } else {
                    formStatus.classList.add('text-red-500');
                    formStatus.textContent = data.message || "Failed to send inquiry.";
                }
            } catch (err) {
                formStatus.classList.remove('hidden');
                formStatus.classList.add('text-red-500');
                formStatus.textContent = "Error connecting to server. Is app.py running?";
            } finally {
                submitBtn.disabled = false;
                submitBtn.innerHTML = 'Send Inquiry & Get Roadmap <i class="fa-solid fa-paper-plane text-sm ml-2"></i>';
            }
        });
    }
});