document.addEventListener("DOMContentLoaded", function () {
  function setStatus(btn, status) {
    var sid = btn.getAttribute("data-student");
    var hidden = document.querySelector('input.status-input[name="status_' + sid + '"]');
    if (!hidden) return;
    hidden.value = status;
    btn.textContent = status;
    btn.classList.toggle("is-present", status === "Present");
    btn.classList.toggle("is-absent", status === "Absent");
  }

  // Individual toggle buttons — click flips Present <-> Absent
  document.querySelectorAll(".toggle-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var isPresent = btn.classList.contains("is-present");
      setStatus(btn, isPresent ? "Absent" : "Present");
    });
  });

  // "Mark All Present" / "Mark All Absent" buttons
  document.querySelectorAll("[data-mark]").forEach(function (markBtn) {
    markBtn.addEventListener("click", function () {
      var status = markBtn.getAttribute("data-mark");
      document.querySelectorAll(".toggle-btn").forEach(function (btn) {
        setStatus(btn, status);
      });
    });
  });
});
