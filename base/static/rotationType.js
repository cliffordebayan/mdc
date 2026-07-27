$(document).on('htmx:load', function () {
  function getRotationTypeMap() {
    var el = document.getElementById('rotation-type-map');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return {};
    }
  }

  function hideCadenceFields(hide) {
    if (hide) {
      $("label[for='id_based_on']").hide();
      $("#id_based_on").hide();
      $("label[for='id_rotate_after_day']").hide();
      $("#id_rotate_after_day").hide();
      $("label[for='id_rotate_every_weekend']").hide();
      $("#id_rotate_every_weekend").hide();
      $("label[for='id_rotate_every']").hide();
      $("#id_rotate_every").hide();
    } else {
      $("label[for='id_based_on']").show();
      $("#id_based_on").show();
      $("#id_based_on").trigger('change');
    }
  }

  function applyRotationType() {
    var select = $('#id_rotating_shift_id');
    if (!select.length) return;
    var map = getRotationTypeMap();
    hideCadenceFields(map[select.val()] === 'date_range');
  }

  var select = $('#id_rotating_shift_id');
  if (select.length) {
    applyRotationType();
    select.on('change', applyRotationType);
  }

  function applyRotatingShiftFormScope() {
    var typeSelect = $('#id_rotation_type');
    if (!typeSelect.length) return;
    var isDateRange = typeSelect.val() === 'date_range';
    $('[data-rotation-scope="sequential"]').toggle(!isDateRange);
    $('[data-rotation-scope="date_range"]').toggle(isDateRange);
  }

  var typeSelect = $('#id_rotation_type');
  if (typeSelect.length) {
    applyRotatingShiftFormScope();
    typeSelect.on('change', applyRotatingShiftFormScope);
  }
});
