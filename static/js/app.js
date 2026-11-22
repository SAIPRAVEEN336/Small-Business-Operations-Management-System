// Client-side scripts for the Back Office app.

// New invoice page: preview the due date from the selected customer's payment terms.
// The server recalculates the due date on submit, so this is only a convenience.
(function () {
    var customer = document.getElementById("customer_id");
    var issue = document.getElementById("issue_date");
    var due = document.getElementById("due_date");
    if (!customer || !issue || !due) {
        return;
    }

    function pad(n) {
        return (n < 10 ? "0" : "") + n;
    }

    function updateDueDate() {
        var option = customer.options[customer.selectedIndex];
        var days = parseInt(option ? option.getAttribute("data-terms-days") : "", 10);
        if (!issue.value || isNaN(days)) {
            due.value = "";
            return;
        }
        var d = new Date(issue.value + "T00:00:00");
        d.setDate(d.getDate() + days);
        due.value = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
    }

    customer.addEventListener("change", updateDueDate);
    issue.addEventListener("change", updateDueDate);
    updateDueDate();
})();

// Chase page: copy the drafted reminder, keep the WhatsApp link in step with the
// edited text, and send that text with "Mark as sent".
(function () {
    var message = document.getElementById("chase-message");
    var copyButton = document.getElementById("copy-message");
    var whatsapp = document.getElementById("whatsapp-link");
    var sentForm = document.getElementById("mark-sent-form");
    var sentText = document.getElementById("sent-message-text");
    if (!message) {
        return;
    }

    if (copyButton) {
        copyButton.addEventListener("click", function () {
            if (!navigator.clipboard) {
                return;
            }
            navigator.clipboard.writeText(message.value).then(function () {
                copyButton.textContent = "Copied";
            });
        });
    }

    if (whatsapp) {
        var base = whatsapp.getAttribute("data-base");
        var updateWhatsapp = function () {
            whatsapp.setAttribute("href", base + "?text=" + encodeURIComponent(message.value));
        };
        message.addEventListener("input", updateWhatsapp);
    }

    if (sentForm && sentText) {
        sentForm.addEventListener("submit", function () {
            sentText.value = message.value;
        });
    }
})();

