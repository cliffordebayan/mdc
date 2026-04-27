$(document).on("htmx:load", "#activitySidebar", function (event) {
  $('[data-target="#updateNoteModal"]').click(function (e) {
    $("#updateNoteModal").addClass("oh-modal--show");
  });
});
$(document).on("htmx:load", "#modalContent", function () {
  $(".oh-modal__close").click(function (e) {
    $("#updateNoteModal").removeClass("oh-modal--show");
  });
});

function getCheckedRatingValue($form) {
  var $checked = $form.find(".rating-radio:checked").first();
  return $checked.length ? String($checked.val()) : "";
}

function ensureClearInput($form) {
  var $clearInput = $form.find('input[name="clear_rating"]');
  if (!$clearInput.length) {
    $clearInput = $('<input type="hidden" name="clear_rating" value="0" />');
    $form.append($clearInput);
  }
  return $clearInput;
}

function submitRatingForm($form) {
  if (!$form.length) {
    return;
  }
  var formElement = $form[0];
  if (formElement && typeof formElement.requestSubmit === "function") {
    formElement.requestSubmit();
    return;
  }
  if (formElement && typeof formElement.submit === "function") {
    formElement.submit();
    return;
  }
  $form.trigger("submit");
}

$(document).on("mousedown touchstart", ".oh-rate", function (event) {
  event.stopPropagation();
  var $form = $(this).closest("form");
  $form.data("selected-rating", getCheckedRatingValue($form));
});

$(document).on("click", ".oh-rate", function (event) {
  event.stopPropagation();
});

$(document).on("change", ".rating-radio", function (event) {
  event.stopPropagation();
  var $form = $(this).closest("form");
  ensureClearInput($form).val("0");
  submitRatingForm($form);
});

$(document).on("click", ".rating-radio", function (event) {
  event.stopPropagation();
  var $radio = $(this);
  var $form = $radio.closest("form");
  var selectedBefore = String($form.data("selected-rating") || "");
  var clickedValue = String($radio.val() || "");
  var shouldClear = selectedBefore !== "" && selectedBefore === clickedValue;
  if (!shouldClear) {
    return;
  }
  var $clearInput = ensureClearInput($form);

  $clearInput.val("1");
  $form.find(".rating-radio").prop("checked", false);
  submitRatingForm($form);
});
$(document).on("htmx:load", "#activitySidebar", function (event) {
  $('[data-target="#updateNoteModal"]').click(function (e) {
    $("#updateNoteModal").addClass("oh-modal--show");
  });
});
$(document).on("htmx:load", "#modalContent", function () {
  $(".oh-modal__close").click(function (e) {
    $("#updateNoteModal").removeClass("oh-modal--show");
  });
});
