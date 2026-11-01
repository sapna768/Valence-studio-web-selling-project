// Valence Studio - shared interactions for every page.
// Wrapped in an IIFE so it never clashes with the inline scripts of login / register / feedback.
(function () {
  // Mobile menu
  const menuButton = document.querySelector(".menu-toggle");
  const navLinks = document.querySelector(".nav-links");
  if (menuButton && navLinks) {
    menuButton.addEventListener("click", () => {
      const isOpen = navLinks.classList.toggle("open");
      menuButton.setAttribute("aria-expanded", String(isOpen));
      menuButton.textContent = isOpen ? "✕" : "☰";
    });
    navLinks.querySelectorAll("a").forEach(link => link.addEventListener("click", () => {
      navLinks.classList.remove("open");
      menuButton.setAttribute("aria-expanded", "false");
      menuButton.textContent = "☰";
    }));
  }

  // Highlight the current page in the menu
  const here = location.pathname.replace(/\/+$/, "") || "/";
  document.querySelectorAll(".nav-links > a").forEach(link => {
    const target = (new URL(link.href, location.origin).pathname.replace(/\/+$/, "")) || "/";
    if (target === here) link.classList.add("active");
  });

  // Footer year
  document.querySelectorAll("[data-year]").forEach(el => el.textContent = new Date().getFullYear());

  // Pricing: "Choose ..." buttons pre-select the package in the contact form
  const packageSelect = document.getElementById("service");
  document.querySelectorAll("[data-package]").forEach(button => {
    button.addEventListener("click", () => {
      if (!packageSelect) return;
      for (const option of packageSelect.options) {
        if (option.value === button.dataset.package) { packageSelect.value = option.value; break; }
      }
    });
  });

  // Our Work: category filter
  const filterButtons = document.querySelectorAll("[data-filter]");
  const projectCards = document.querySelectorAll("[data-category]");
  filterButtons.forEach(button => button.addEventListener("click", () => {
    filterButtons.forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    const filter = button.dataset.filter;
    projectCards.forEach(card => {
      card.hidden = filter !== "all" && card.dataset.category !== filter;
    });
  }));

  // Project enquiry form (logged-in customers only; the server checks the login).
  // Uses the existing /api/contact endpoint. The extra fields are added to the details text.
  window.submitForm = async function (event) {
    event.preventDefault();

    const name = document.getElementById("name").value;
    const service = document.getElementById("service").value;
    let details = document.getElementById("details").value;
    const businessType = (document.getElementById("businessType") || {}).value || "";
    const timeline = (document.getElementById("timeline") || {}).value || "";
    if (businessType.trim()) details += "\n\nBusiness / project type: " + businessType.trim();
    if (timeline) details += "\nExpected timeline: " + timeline;

    const statusEl = document.getElementById("formStatus");
    const submitBtn = document.getElementById("submitBtn");
    const idleLabel = "Submit Project Enquiry ↗";

    submitBtn.disabled = true;
    submitBtn.textContent = "Submitting...";
    statusEl.classList.remove("hidden", "text-red-400", "text-green-400");
    statusEl.classList.add("text-blue-400");
    statusEl.innerText = "Submitting your requirements...";

    try {
      const response = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, service, details })
      });

      let data;
      try { data = await response.json(); }
      catch (_) { data = { success: false, message: "Unexpected server response. Please try again." }; }

      statusEl.classList.remove("text-blue-400");
      if (data.success) {
        statusEl.classList.add("text-green-400");
        statusEl.innerText = data.message;
        document.getElementById("details").value = "";
        if (document.getElementById("businessType")) document.getElementById("businessType").value = "";
        if (document.getElementById("timeline")) document.getElementById("timeline").value = "";
      } else {
        statusEl.classList.add("text-red-400");
        statusEl.innerText = data.message || "Something went wrong while submitting.";
        if (data.login_required) {
          setTimeout(() => { window.location.href = "/login"; }, 1500);
        }
      }
    } catch (error) {
      statusEl.classList.remove("text-blue-400");
      statusEl.classList.add("text-red-400");
      statusEl.innerText = "Network error. Please check your connection and try again.";
    }
    submitBtn.disabled = false;
    submitBtn.textContent = idleLabel;
  };
})();
