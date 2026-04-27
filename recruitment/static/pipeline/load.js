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

$(document).on("mousedown touchstart", ".oh-rate", function (event) {
  event.stopPropagation();
  var $form = $(this).closest("form");
  $form.data("selected-rating", getCheckedRatingValue($form));
});

$(document).on("click", ".oh-rate", function (event) {
  event.stopPropagation();
});

$(document).on("click", ".rating-radio", function (event) {
  event.stopPropagation();
  var $radio = $(this);
  var $form = $radio.closest("form");
  var selectedBefore = String($form.data("selected-rating") || "");
  var clickedValue = String($radio.val() || "");
  var shouldClear = selectedBefore !== "" && selectedBefore === clickedValue;
  var $clearInput = $form.find('input[name="clear_rating"]');

  if (!$clearInput.length) {
    $clearInput = $('<input type="hidden" name="clear_rating" value="0" />');
    $form.append($clearInput);
  }

  if (shouldClear) {
    $clearInput.val("1");
    $form.find(".rating-radio").prop("checked", false);
  } else {
    $clearInput.val("0");
  }

  var $submitTrigger = $form.find(".rating-submit-trigger").first();
  if ($submitTrigger.length) {
    $submitTrigger.trigger("click");
  } else if ($form.length && typeof $form[0].requestSubmit === "function") {
    $form[0].requestSubmit();
  }
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
