// Enhances the question form: enables Next only for a valid answer and moves
// focus into the "Other" field. The server validates everything again.
(function () {
  var form = document.getElementById("question-form");
  if (!form) return;

  var otherKey = form.getAttribute("data-other-key");
  var nextButton = document.getElementById("next-btn");
  var otherText = document.getElementById("other_text");
  var radios = form.querySelectorAll('input[type="radio"][name="answer"]');

  function isValid() {
    var checked = form.querySelector('input[name="answer"]:checked');
    if (!checked) return false;
    if (checked.value === otherKey) return otherText.value.trim().length > 0;
    return true;
  }

  function refresh() {
    nextButton.disabled = !isValid();
  }

  radios.forEach(function (radio) {
    radio.addEventListener("change", function () {
      if (radio.value === otherKey && radio.checked) otherText.focus();
      refresh();
    });
  });
  otherText.addEventListener("input", refresh);

  // Pressing Enter in the text box must not submit an invalid answer.
  form.addEventListener("submit", function (event) {
    var action = event.submitter ? event.submitter.value : "next";
    if ((action === "next" || action === "save") && !isValid()) event.preventDefault();
  });

  refresh();
})();
