// Re-run the comparison as soon as a plan is changed. The button still works without JavaScript.
(function () {
  var form = document.getElementById("picker-form");
  if (!form) return;
  form.querySelectorAll("select").forEach(function (select) {
    select.addEventListener("change", function () {
      form.submit();
    });
  });
})();
