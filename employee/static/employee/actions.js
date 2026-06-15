var excelMessages = {
    ar: "هل ترغب في تنزيل ملف Excel؟",
    de: "Möchten Sie die Excel-Datei herunterladen?",
    es: "¿Desea descargar el archivo de Excel?",
    en: "Do you want to download the excel file?",
    fr: "Voulez-vous télécharger le fichier Excel?",
};
var archiveMessages = {
    ar: "هل ترغب حقًا في أرشفة جميع الموظفين المحددين؟",
    de: "Möchten Sie wirklich alle ausgewählten Mitarbeiter archivieren?",
    es: "¿Realmente quieres archivar a todos los empleados seleccionados?",
    en: "Do you really want to archive all the selected employees?",
    fr: "Voulez-vous vraiment archiver tous les employés sélectionnés ?",
};

var unarchiveMessages = {
    ar: "هل ترغب حقًا في إلغاء أرشفة جميع الموظفين المحددين؟",
    de: "Möchten Sie wirklich alle ausgewählten Mitarbeiter aus der Archivierung zurückholen?",
    es: "¿Realmente quieres desarchivar a todos los empleados seleccionados?",
    en: "Do you really want to unarchive all the selected employees?",
    fr: "Voulez-vous vraiment désarchiver tous les employés sélectionnés?",
};

var deleteMessages = {
    ar: "هل ترغب حقًا في حذف جميع الموظفين المحددين؟",
    de: "Möchten Sie wirklich alle ausgewählten Mitarbeiter löschen?",
    es: "¿Realmente quieres eliminar a todos los empleados seleccionados?",
    en: "Do you really want to delete all the selected employees?",
    fr: "Voulez-vous vraiment supprimer tous les employés sélectionnés?",
};

var noRowMessages = {
    ar: "لم يتم تحديد أي صفوف لحذف الموظفين.",
    de: "Es wurden keine Zeilen ausgewählt, um Mitarbeiter zu löschen.",
    es: "No se han seleccionado filas para eliminar empleados.",
    en: "No rows have been selected to delete employees.",
    fr: "Aucune ligne n'a été sélectionnée pour supprimer des employés.",
};

var noRowUpdateMessages = {
    "ar": "لم يتم تحديد أي صفوف لتحديث الموظفين.",
    "de": "Es wurden keine Zeilen ausgewählt, um Mitarbeiter zu aktualisieren.",
    "es": "No se han seleccionado filas para actualizar empleados.",
    "en": "No rows have been selected to update employees.",
    "fr": "Aucune ligne n'a été sélectionnée pour mettre à jour des employés."
};

var rowMessages = {
    ar: " تم الاختيار",
    de: " Ausgewählt",
    es: " Seleccionado",
    en: " Selected",
    fr: " Sélectionné",
};

tickCheckboxes();

function makeListUnique(list) {
    return Array.from(new Set(list));
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            // Does this cookie string begin with the name we want?
            if (cookie.substring(0, name.length + 1) === name + "=") {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

$(".all-employee").change(function (e) {
    var is_checked = $(this).is(":checked");
    var closest = $(this)
        .closest(".oh-sticky-table__thead")
        .siblings(".oh-sticky-table__tbody");
    if (is_checked) {
        $(closest)
            .children()
            .find(".all-employee-row")
            .prop("checked", true)
            .closest(".oh-sticky-table__tr")
            .addClass("highlight-selected");
    } else {
        $(closest)
            .children()
            .find(".all-employee-row")
            .prop("checked", false)
            .closest(".oh-sticky-table__tr")
            .removeClass("highlight-selected");
    }
    addingIds();
});

$(".all-employee-row").change(function () {
    var parentTable = $(this).closest(".oh-sticky-table");
    var body = parentTable.find(".oh-sticky-table__tbody");
    var parentCheckbox = parentTable.find(".all-employee");
    parentCheckbox.prop(
        "checked",
        body.find(".all-employee-row:checked").length ===
        body.find(".all-employee-row").length
    );
    addingIds();
});

function addingIds() {
    var ids = JSON.parse($("#selectedInstances").attr("data-ids") || "[]");
    var selectedCount = 0;

    $(".all-employee-row").each(function () {
        if ($(this).is(":checked")) {
            ids.push(this.id);
        } else {
            var index = ids.indexOf(this.id);
            if (index > -1) {
                ids.splice(index, 1);
            }
        }
    });

    ids = makeListUnique(ids);
    selectedCount = ids.length;
    languageCode = $("#main-section-data").attr("data-lang");
    var message =
        rowMessages[languageCode] ||
        ((languageCode = "en"), rowMessages[languageCode]);
    $("#selectedInstances").attr("data-ids", JSON.stringify(ids));
    if (selectedCount === 0) {
        $("#unselectAllEmployees").css("display", "none");
        $("#exportEmployees").css("display", "none");
        $("#sendBulkPortalLink").css("display", "none");
        $("#sendBulkPasswordReset").css("display", "none");
        $("#sendBulkPinEmail").css("display", "none");
        $("#selectedShow").css("display", "none");
    } else {
        $("#unselectAllEmployees").css("display", "inline-flex");
        $("#exportEmployees").css("display", "inline-flex");
        $("#sendBulkPortalLink").css("display", "inline-flex");
        $("#sendBulkPasswordReset").css("display", "inline-flex");
        $("#sendBulkPinEmail").css("display", "inline-flex");
        $("#selectedShow").css("display", "inline-flex");
        $("#selectedShow").text(selectedCount + " - " + message);
    }
}

function tickCheckboxes() {
    var ids = JSON.parse($("#selectedInstances").attr("data-ids") || "[]");
    var uniqueIds = makeListUnique(ids);
    toggleHighlight(uniqueIds);
    click = $("#selectedInstances").attr("data-clicked");
    if (click === "1") {
        $(".all-employee").prop("checked", true);
    }

    uniqueIds.forEach(function (id) {
        $("#" + id).prop("checked", true);
    });
    var selectedCount = uniqueIds.length;
    languageCode = $("#main-section-data").attr("data-lang");
    var message =
        rowMessages[languageCode] ||
        ((languageCode = "en"), rowMessages[languageCode]);
    if (selectedCount > 0) {
        $("#unselectAllEmployees").css("display", "inline-flex");
        $("#exportEmployees").css("display", "inline-flex");
        $("#sendBulkPortalLink").css("display", "inline-flex");
        $("#sendBulkPasswordReset").css("display", "inline-flex");
        $("#sendBulkPinEmail").css("display", "inline-flex");
        $("#selectedShow").css("display", "inline-flex");
        $("#selectedShow").text(selectedCount + " -" + message);
    } else {
        $("#unselectAllEmployees").css("display", "none");
        $("#exportEmployees").css("display", "none");
        $("#sendBulkPortalLink").css("display", "none");
        $("#sendBulkPasswordReset").css("display", "none");
        $("#sendBulkPinEmail").css("display", "none");
        $("#selectedShow").css("display", "none");
    }
}

function selectAllEmployees() {
    var allEmployeeCount = 0;
    $("#selectedInstances").attr("data-clicked", 1);
    $("#selectedShow").removeAttr("style");
    var savedFilters = JSON.parse(localStorage.getItem("savedFilters"));
    var filterQuery = $("#selectAllEmployees").data("pd");
    if (savedFilters && savedFilters["filterData"] !== null) {
        $.ajax({
            url: "/employee/employee-select-filter?" + filterQuery,
            data: { page: "all" },
            type: "GET",
            dataType: "json",
            success: function (response) {
                var employeeIds = response.employee_ids;

                if (Array.isArray(employeeIds)) {
                    // Continue
                } else {
                    console.error("employee_ids is not an array:", employeeIds);
                }

                allEmployeeCount = employeeIds.length;

                for (var i = 0; i < employeeIds.length; i++) {
                    var empId = employeeIds[i];
                    $("#" + empId).prop("checked", true);
                }
                $("#selectedInstances").attr("data-ids", JSON.stringify(employeeIds));

                count = makeListUnique(employeeIds);
                $("#unselectAllEmployees").css("display", "inline-flex");
                $("#exportEmployees").css("display", "inline-flex");
                tickCheckboxes(count);
            },
            error: function (xhr, status, error) {
                console.error("Error:", error);
            },
        });
    } else {
        $.ajax({
            url: "/employee/employee-select",
            data: { page: "all" },
            type: "GET",
            dataType: "json",
            success: function (response) {
                var employeeIds = response.employee_ids;

                if (Array.isArray(employeeIds)) {
                    // Continue
                } else {
                    console.error("employee_ids is not an array:", employeeIds);
                }

                allEmployeeCount = employeeIds.length;

                for (var i = 0; i < employeeIds.length; i++) {
                    var empId = employeeIds[i];
                    $("#" + empId).prop("checked", true);
                }
                var previousIds = $("#selectedInstances").attr("data-ids");
                $("#selectedInstances").attr(
                    "data-ids",
                    JSON.stringify(
                        Array.from(new Set([...employeeIds, ...JSON.parse(previousIds)]))
                    )
                );

                count = makeListUnique(employeeIds);
                $("#unselectAllEmployees").css("display", "inline-flex");
                $("#exportEmployees").css("display", "inline-flex");
                tickCheckboxes(count);
            },
            error: function (xhr, status, error) {
                console.error("Error:", error);
            },
        });
    }
}

function unselectAllEmployees() {
    $("#selectedInstances").attr("data-clicked", 0);

    $.ajax({
        url: "/employee/employee-select",
        data: { page: "unselect", filter: "{}" },
        type: "GET",
        dataType: "json",
        success: function (response) {
            var employeeIds = response.employee_ids;

            if (Array.isArray(employeeIds)) {
                // Continue
            } else {
                console.error("employee_ids is not an array:", employeeIds);
            }

            for (var i = 0; i < employeeIds.length; i++) {
                var empId = employeeIds[i];
                $("#" + empId).prop("checked", false);
                $("#tick").prop("checked", false);
            }
            var ids = JSON.parse($("#selectedInstances").attr("data-ids") || "[]");
            var uniqueIds = makeListUnique(ids);
            toggleHighlight(uniqueIds);

            $("#selectedInstances").attr("data-ids", JSON.stringify([]));

            count = [];
            $("#unselectAllEmployees").css("display", "none");
            $("#exportEmployees").css("display", "none");
            tickCheckboxes(count);
        },
        error: function (xhr, status, error) {
            console.error("Error:", error);
        },
    });
}

$("#exportEmployees").click(function (e) {
    var currentDate = new Date().toISOString().slice(0, 10);
    var languageCode = null;
    languageCode = $("#main-section-data").attr("data-lang");
    var confirmMessage =
        excelMessages[languageCode] ||
        ((languageCode = "en"), excelMessages[languageCode]);
    ids = [];
    ids.push($("#selectedInstances").attr("data-ids"));
    ids = JSON.parse($("#selectedInstances").attr("data-ids"));
    Swal.fire({
        text: confirmMessage,
        icon: "question",
        showCancelButton: true,
        confirmButtonColor: "#008000",
        cancelButtonColor: "#d33",
        confirmButtonText: "Confirm",
    }).then(function (result) {
        if (result.isConfirmed) {
            $.ajax({
                type: "GET",
                url: "/employee/work-info-export",
                data: {
                    ids: JSON.stringify(ids),
                },
                dataType: "binary",
                xhrFields: {
                    responseType: "blob",
                },
                success: function (response) {
                    const file = new Blob([response], {
                        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    });
                    const url = URL.createObjectURL(file);
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = "employee_export_" + currentDate + ".xlsx";
                    document.body.appendChild(link);
                    link.click();
                },
                error: function (xhr, textStatus, errorThrown) {
                    console.error("Error downloading file:", errorThrown);
                },
            });
        }
    });
});

$("#employeeBulkUpdateId").click(function (e) {
    var languageCode = null;
    languageCode = $("#main-section-data").attr("data-lang");
    var textMessage =
        noRowUpdateMessages[languageCode] ||
        ((languageCode = "en"), noRowUpdateMessages[languageCode]);
    ids = [];
    ids.push($("#selectedInstances").attr("data-ids"));
    ids = JSON.parse($("#selectedInstances").attr("data-ids"));
    if (ids.length === 0) {
        $("#bulkUpdateModal").removeClass("oh-modal--show");
        Swal.fire({
            text: textMessage,
            icon: "warning",
            confirmButtonText: "Close",
        });
    } else {
        $("#id_bulk_employee_ids").val(JSON.stringify(ids));
        $("#bulkUpdateModal").addClass("oh-modal--show");
    }
});

$("#archiveEmployees").click(function (e) {
    e.preventDefault();
    var languageCode = null;
    languageCode = $("#main-section-data").attr("data-lang");
    var confirmMessage =
        archiveMessages[languageCode] ||
        ((languageCode = "en"), archiveMessages[languageCode]);
    var textMessage =
        noRowMessages[languageCode] ||
        ((languageCode = "en"), noRowMessages[languageCode]);
    ids = [];
    ids.push($("#selectedInstances").attr("data-ids"));
    ids = JSON.parse($("#selectedInstances").attr("data-ids"));
    if (ids.length === 0) {
        Swal.fire({
            text: textMessage,
            icon: "warning",
            confirmButtonText: "Close",
        });
    } else {
        Swal.fire({
            text: confirmMessage,
            icon: "info",
            showCancelButton: true,
            confirmButtonColor: "#008000",
            cancelButtonColor: "#d33",
            confirmButtonText: "Confirm",
        }).then(function (result) {
            if (result.isConfirmed) {
                e.preventDefault();
                ids = [];
                ids.push($("#selectedInstances").attr("data-ids"));
                ids = JSON.parse($("#selectedInstances").attr("data-ids"));
                $.ajax({
                    type: "POST",
                    url: "/employee/employee-bulk-archive?is_active=False",
                    data: {
                        csrfmiddlewaretoken: getCookie("csrftoken"),
                        ids: JSON.stringify(ids),
                    },
                    success: function (response, textStatus, jqXHR) {
                        if (jqXHR.status === 200) {
                            location.reload(); // Reload the current page
                        } else {
                            // console.log("Unexpected HTTP status:", jqXHR.status);
                        }
                    },
                });
            }
        });
    }
});

$("#unArchiveEmployees").click(function (e) {
    e.preventDefault();
    var languageCode = null;
    languageCode = $("#main-section-data").attr("data-lang");
    var confirmMessage =
        unarchiveMessages[languageCode] ||
        ((languageCode = "en"), unarchiveMessages[languageCode]);
    var textMessage =
        noRowMessages[languageCode] ||
        ((languageCode = "en"), noRowMessages[languageCode]);
    ids = [];
    ids.push($("#selectedInstances").attr("data-ids"));
    ids = JSON.parse($("#selectedInstances").attr("data-ids"));
    if (ids.length === 0) {
        Swal.fire({
            text: textMessage,
            icon: "warning",
            confirmButtonText: "Close",
        });
    } else {
        Swal.fire({
            text: confirmMessage,
            icon: "info",
            showCancelButton: true,
            confirmButtonColor: "#008000",
            cancelButtonColor: "#d33",
            confirmButtonText: "Confirm",
        }).then(function (result) {
            if (result.isConfirmed) {
                e.preventDefault();

                ids = [];

                ids.push($("#selectedInstances").attr("data-ids"));
                ids = JSON.parse($("#selectedInstances").attr("data-ids"));

                $.ajax({
                    type: "POST",
                    url: "/employee/employee-bulk-archive?is_active=True",
                    data: {
                        csrfmiddlewaretoken: getCookie("csrftoken"),
                        ids: JSON.stringify(ids),
                    },
                    success: function (response, textStatus, jqXHR) {
                        if (jqXHR.status === 200) {
                            location.reload(); // Reload the current page
                        } else {
                            // console.log("Unexpected HTTP status:", jqXHR.status);
                        }
                    },
                });
            }
        });
    }
});

$("#deleteEmployees").click(function (e) {
    e.preventDefault();
    var languageCode = null;
    languageCode = $("#main-section-data").attr("data-lang");
    var confirmMessage =
        deleteMessages[languageCode] ||
        ((languageCode = "en"), deleteMessages[languageCode]);
    var textMessage =
        noRowMessages[languageCode] ||
        ((languageCode = "en"), noRowMessages[languageCode]);
    ids = [];
    ids.push($("#selectedInstances").attr("data-ids"));
    ids = JSON.parse($("#selectedInstances").attr("data-ids"));
    if (ids.length === 0) {
        Swal.fire({
            text: textMessage,
            icon: "warning",
            confirmButtonText: "Close",
        });
    } else {
        Swal.fire({
            text: confirmMessage,
            icon: "error",
            showCancelButton: true,
            confirmButtonColor: "#008000",
            cancelButtonColor: "#d33",
            confirmButtonText: "Confirm",
        }).then(function (result) {
            if (result.isConfirmed) {
                e.preventDefault();
                $("#view-container").html(`<div class="animated-background"></div>`);

                ids = [];
                ids.push($("#selectedInstances").attr("data-ids"));
                ids = JSON.parse($("#selectedInstances").attr("data-ids"));

                $.ajax({
                    type: "POST",
                    url: "/employee/employee-bulk-delete",
                    data: {
                        csrfmiddlewaretoken: getCookie("csrftoken"),
                        ids: JSON.stringify(ids),
                    },
                    success: function (response, textStatus, jqXHR) {
                        if (jqXHR.status === 200) {
                            location.reload(); // Reload the current page
                        } else {
                            // console.log("Unexpected HTTP status:", jqXHR.status);
                        }
                    },
                });
            }
        });
    }
});

$("#select-all-fields").change(function () {
    const isChecked = $(this).prop("checked");
    $('[name="selected_fields"]').prop("checked", isChecked);
});

// ── Bulk email helpers ────────────────────────────────────────────────────────

function sleepMs(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

function getInitials(name) {
    var parts = (name || "?").trim().split(/\s+/);
    var first = parts[0][0] || "?";
    var last  = parts.length > 1 ? parts[parts.length - 1][0] : "";
    return (first + last).toUpperCase();
}

function buildEmpRows(employees, avatarBg, rowBorder) {
    return employees.map(function (e) {
        var ini = getInitials(e.name);
        return (
            "<div style='display:flex;align-items:center;gap:10px;padding:8px 12px;" +
            "border-bottom:1px solid " + rowBorder + "'>" +
            "<span style='width:32px;height:32px;border-radius:50%;background:" + avatarBg + ";" +
            "color:#fff;display:inline-flex;align-items:center;justify-content:center;" +
            "font-size:11px;font-weight:700;flex-shrink:0'>" + ini + "</span>" +
            "<span style='font-size:13px;text-align:left;line-height:1.3'>" + e.name + "</span>" +
            "</div>"
        );
    }).join("");
}

function ajaxPost(url, data) {
    return new Promise(function (resolve, reject) {
        $.ajax({
            type: "POST",
            url: url,
            data: data,
            success: function (resp) { resolve(resp); },
            error: function (xhr, status, err) { reject(err || status); },
        });
    });
}

/**
 * Shared flow for all three bulk email actions.
 * @param {string} type    - "portal" | "password" | "pin"
 * @param {string} label   - Human-readable action label shown in dialogs
 */
async function bulkEmailSendFlow(type, label) {
    var ids = JSON.parse($("#selectedInstances").attr("data-ids") || "[]");
    if (ids.length === 0) {
        Swal.fire({ text: "No employees selected.", icon: "warning", confirmButtonText: "Close" });
        return;
    }

    // ── Step 1: check who already received this email ──────────────────────
    var checkResp;
    try {
        checkResp = await ajaxPost("/employee/employee-bulk-email-check", {
            csrfmiddlewaretoken: getCookie("csrftoken"),
            ids: JSON.stringify(ids),
            type: type,
        });
    } catch (err) {
        Swal.fire({ text: "Failed to check email status. Please try again.", icon: "error", confirmButtonText: "Close" });
        return;
    }

    var alreadySent = checkResp.already_sent || [];
    var notSent     = checkResp.not_sent     || [];
    var finalIds    = ids.slice(); // copy — default: send to all

    // ── Step 2: prompt about already-sent employees ────────────────────────
    if (alreadySent.length > 0) {
        // ── already-sent section ──
        var alreadyRows = buildEmpRows(alreadySent, "#e6a817", "#fff3cd");
        var alreadySection =
            "<div style='margin-bottom:14px'>" +
            "  <div style='display:flex;align-items:center;gap:8px;margin-bottom:8px'>" +
            "    <span style='background:#ffc107;color:#fff;border-radius:50%;width:22px;height:22px;" +
            "          display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0'>!</span>" +
            "    <span style='font-weight:600;font-size:14px'>Already sent — " + alreadySent.length + " employee(s)</span>" +
            "  </div>" +
            "  <div style='max-height:160px;overflow-y:auto;border:1px solid #ffc107;border-radius:8px;background:#fffdf0'>" +
            alreadyRows +
            "  </div>" +
            "</div>";

        // ── new recipients section (only when some exist) ──
        var newSection = "";
        if (notSent.length > 0) {
            var newRows = buildEmpRows(notSent, "#198754", "#d1e7dd");
            newSection =
                "<div style='margin-bottom:14px'>" +
                "  <div style='display:flex;align-items:center;gap:8px;margin-bottom:8px'>" +
                "    <span style='background:#198754;color:#fff;border-radius:50%;width:22px;height:22px;" +
                "          display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0'>&#10003;</span>" +
                "    <span style='font-weight:600;font-size:14px'>New recipients — " + notSent.length + " employee(s)</span>" +
                "  </div>" +
                "  <div style='max-height:110px;overflow-y:auto;border:1px solid #198754;border-radius:8px;background:#f0fff4'>" +
                newRows +
                "  </div>" +
                "</div>";
        }

        var promptHtml =
            "<div style='text-align:left'>" +
            alreadySection +
            newSection +
            "<p style='font-size:13px;color:#555;margin:0'>What would you like to do?</p>" +
            "</div>";

        var promptResult = await Swal.fire({
            title: "<span style='font-size:18px'>&#9888; Already received this email</span>",
            html: promptHtml,
            icon: undefined,
            showConfirmButton: true,
            showDenyButton: notSent.length > 0,
            showCancelButton: true,
            confirmButtonText: "Resend to All (" + ids.length + ")",
            denyButtonText: "Send to New Only (" + notSent.length + ")",
            cancelButtonText: "Cancel",
            confirmButtonColor: "#008000",
            denyButtonColor: "#0d6efd",
            cancelButtonColor: "#d33",
            width: "520px",
            customClass: { popup: "already-sent-popup" },
        });

        if (promptResult.isConfirmed) {
            finalIds = ids.slice();
        } else if (promptResult.isDenied) {
            if (notSent.length === 0) {
                Swal.fire({ text: "All selected employees have already been sent this email.", icon: "info", confirmButtonText: "Close" });
                return;
            }
            finalIds = notSent.map(function (e) { return e.id; });
        } else {
            return; // cancelled
        }
    } else {
        var confirmResult = await Swal.fire({
            text: "Send \"" + label + "\" to " + ids.length + " employee(s)?",
            icon: "info",
            showCancelButton: true,
            confirmButtonColor: "#008000",
            cancelButtonColor: "#d33",
            confirmButtonText: "Confirm",
        });
        if (!confirmResult.isConfirmed) { return; }
    }

    // ── Step 3: build name lookup ──────────────────────────────────────────
    var total = finalIds.length;
    var sentCount = 0;
    var failCount = 0;
    var nameLookup = {};
    (checkResp.already_sent || []).concat(checkResp.not_sent || []).forEach(function (e) {
        nameLookup[String(e.id)] = e.name;
    });

    // ── Step 4: show progress modal (not awaited — stays open) ────────────
    var progressHtml =
        "<div style='text-align:left'>" +
        "  <p style='font-size:14px;margin-bottom:6px'>Sending to: <strong id='bulk-emp-name' style='color:#0d6efd'>—</strong></p>" +
        "  <div style='height:20px;border-radius:10px;background:#e9ecef;overflow:hidden;margin-bottom:6px'>" +
        "    <div id='bulk-pbar' style='height:100%;width:0%;background:#28a745;border-radius:10px;" +
        "         transition:width 0.3s ease;background-image:linear-gradient(45deg,rgba(255,255,255,.15) 25%,transparent 25%," +
        "         transparent 50%,rgba(255,255,255,.15) 50%,rgba(255,255,255,.15) 75%,transparent 75%,transparent);" +
        "         background-size:1rem 1rem'></div>" +
        "  </div>" +
        "  <p style='font-size:13px;margin-bottom:6px'><span id='bulk-sent'>0</span> / " + total + " processed</p>" +
        "  <div id='bulk-log' style='max-height:150px;overflow-y:auto;font-size:12px;" +
        "       border:1px solid #dee2e6;border-radius:6px;padding:6px;background:#f8f9fa'></div>" +
        "</div>";

    Swal.fire({
        title: "Sending " + label + "…",
        html: progressHtml,
        allowOutsideClick: false,
        allowEscapeKey: false,
        showConfirmButton: false,
        customClass: { popup: "bulk-email-progress-popup" },
    });

    // give the browser one tick to render the Swal before we start
    await sleepMs(80);

    // ── Step 5: send one by one ────────────────────────────────────────────
    for (var i = 0; i < finalIds.length; i++) {
        var empId   = finalIds[i];
        var empName = nameLookup[String(empId)] || ("Employee #" + empId);

        var nameEl = document.getElementById("bulk-emp-name");
        if (nameEl) { nameEl.textContent = empName; }

        var resp;
        try {
            resp = await ajaxPost("/employee/employee-send-single-email", {
                csrfmiddlewaretoken: getCookie("csrftoken"),
                emp_id: empId,
                type: type,
            });
        } catch (err) {
            resp = { success: false, employee_name: empName, message: "Request failed." };
        }

        var logEl = document.getElementById("bulk-log");
        if (logEl) {
            var line = document.createElement("div");
            line.style.padding = "2px 0";
            if (resp && resp.success) {
                sentCount++;
                line.innerHTML = "<span style='color:#198754;font-weight:600'>&#10003;</span> " + empName;
            } else {
                failCount++;
                var msg = (resp && resp.message) ? resp.message : "failed";
                line.innerHTML = "<span style='color:#dc3545;font-weight:600'>&#10007;</span> " + empName +
                    " <span style='color:#6c757d'>— " + msg + "</span>";
            }
            logEl.appendChild(line);
            logEl.scrollTop = logEl.scrollHeight;
        }

        var pct = Math.round(((i + 1) / total) * 100);
        var pbarEl = document.getElementById("bulk-pbar");
        if (pbarEl) { pbarEl.style.width = pct + "%"; }
        var sentEl = document.getElementById("bulk-sent");
        if (sentEl) { sentEl.textContent = i + 1; }

        if (i < finalIds.length - 1) {
            await sleepMs(800);
        }
    }

    // ── Step 6: replace progress modal with result ─────────────────────────
    var icon    = sentCount > 0 ? (failCount > 0 ? "warning" : "success") : "error";
    var summary = sentCount + " email(s) sent";
    if (failCount > 0) { summary += ", " + failCount + " failed"; }

    Swal.fire({
        title: label + " — Done",
        text: summary,
        icon: icon,
        confirmButtonText: "Close",
        confirmButtonColor: "#008000",
    });
}

$("#sendBulkPortalLink").click(function (e) {
    e.preventDefault();
    bulkEmailSendFlow("portal", "Profile Portal Link");
});

$("#sendBulkPasswordReset").click(function (e) {
    e.preventDefault();
    bulkEmailSendFlow("password", "Password Reset Link");
});

$("#sendBulkPinEmail").click(function (e) {
    e.preventDefault();
    bulkEmailSendFlow("pin", "PIN to Email");
});
