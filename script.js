document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("[data-mark]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var status = btn.getAttribute("data-mark");
      document
        .querySelectorAll('input[type="radio"][value="' + status + '"]')
        .forEach(function (el) {
          el.checked = true;
        });
    });
  });
});
