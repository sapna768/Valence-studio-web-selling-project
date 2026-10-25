// Main JavaScript for Valence Studio

document.addEventListener('DOMContentLoaded', () => {
    // 1. Mobile Menu Toggle
    const mobileMenuBtn = document.getElementById('mobileMenuBtn');
    const mobileMenu = document.getElementById('mobileMenu');
    
    if (mobileMenuBtn && mobileMenu) {
        mobileMenuBtn.addEventListener('click', () => {
            mobileMenu.classList.toggle('hidden');
        });

        document.querySelectorAll('.mobile-nav-link').forEach(link => {
            link.addEventListener('click', () => {
                mobileMenu.classList.add('hidden');
            });
        });
    }

    // 2. Ambient Particle Canvas Effect
    const canvas = document.getElementById('ambientCanvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        let width = canvas.width = window.innerWidth;
        let height = canvas.height = window.innerHeight;

        window.addEventListener('resize', () => {
            width = canvas.width = window.innerWidth;
            height = canvas.height = window.innerHeight;
        });

        const particles = [];
        const particleCount = 45;

        for (let i = 0; i < particleCount; i++) {
            particles.push({
                x: Math.random() * width,
                y: Math.random() * height,
                radius: Math.random() * 2 + 0.5,
                color: Math.random() > 0.5 ? '#d4af37' : '#00d2ff',
                vx: (Math.random() - 0.5) * 0.4,
                vy: (Math.random() - 0.5) * 0.4,
                alpha: Math.random() * 0.5 + 0.2
            });
        }

        function animateCanvas() {
            ctx.clearRect(0, 0, width, height);

            particles.forEach(p => {
                p.x += p.vx;
                p.y += p.vy;

                if (p.x < 0) p.x = width;
                if (p.x > width) p.x = 0;
                if (p.y < 0) p.y = height;
                if (p.y > height) p.y = 0;

                ctx.beginPath();
                ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                ctx.fillStyle = p.color;
                ctx.globalAlpha = p.alpha;
                ctx.fill();
            });

            requestAnimationFrame(animateCanvas);
        }

        animateCanvas();
    }

    // 3. Interactive Pricing & ROI Estimator
    const scopeBtns = document.querySelectorAll('.scope-btn');
    const addonChecks = document.querySelectorAll('.addon-check');
    const totalEstimateEl = document.getElementById('totalEstimate');
    const estTimelineEl = document.getElementById('estTimeline');
    const estLeadBoostEl = document.getElementById('estLeadBoost');

    let selectedBase = 2800; // Default: Full Agency Site

    function updateCalculations() {
        let total = selectedBase;

        addonChecks.forEach(check => {
            if (check.checked) {
                total += parseInt(check.value);
            }
        });

        if (totalEstimateEl) {
            totalEstimateEl.textContent = '$' + total.toLocaleString();
        }

        // Dynamic Timeline and Lead Boost Updates based on Total
        if (estTimelineEl && estLeadBoostEl) {
            if (total < 2500) {
                estTimelineEl.textContent = '1 - 2 Weeks';
                estLeadBoostEl.textContent = '+150% Growth';
            } else if (total < 4500) {
                estTimelineEl.textContent = '2 - 3 Weeks';
                estLeadBoostEl.textContent = '+250% Growth';
            } else if (total < 7000) {
                estTimelineEl.textContent = '3 - 5 Weeks';
                estLeadBoostEl.textContent = '+380% Growth';
            } else {
                estTimelineEl.textContent = '5 - 8 Weeks';
                estLeadBoostEl.textContent = '+500% Growth';
            }
        }
    }

    scopeBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            scopeBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            selectedBase = parseInt(btn.getAttribute('data-val'));
            updateCalculations();
        });
    });

    addonChecks.forEach(check => {
        check.addEventListener('change', updateCalculations);
    });

    // 4. Contact Form Submission Handling
    const contactForm = document.getElementById('contactForm');
    if (contactForm) {
        contactForm.addEventListener('submit', (e) => {
            e.preventDefault();
            alert('Thank you! Your project inquiry has been sent to Valence Studio. We will contact you within 2 hours.');
            contactForm.reset();
        });
    }
});
