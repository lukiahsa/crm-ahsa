(function () {
    "use strict";

    const navigation = document.querySelector("[data-mobile-navigation]");
    if (navigation) {
        const toggle = navigation.querySelector(".mobile-menu-toggle");
        const menu = navigation.querySelector(".mobile-menu");
        const closeMenu = () => {
            menu.hidden = true;
            toggle.setAttribute("aria-expanded", "false");
            toggle.setAttribute("aria-label", "Buka menu utama");
        };
        const openMenu = () => {
            menu.hidden = false;
            toggle.setAttribute("aria-expanded", "true");
            toggle.setAttribute("aria-label", "Tutup menu utama");
            const active = menu.querySelector(".active") || menu.querySelector("a");
            if (active) active.focus({ preventScroll: true });
        };
        toggle.addEventListener("click", () => menu.hidden ? openMenu() : closeMenu());
        document.addEventListener("keydown", event => {
            if (event.key === "Escape" && !menu.hidden) {
                closeMenu();
                toggle.focus();
            }
        });
        document.addEventListener("click", event => {
            if (!menu.hidden && !navigation.contains(event.target)) closeMenu();
        });
    }

    const labelTables = () => {
        document.querySelectorAll("table").forEach(table => {
            if (table.closest(".mobile-preview-page") || table.dataset.mobileIgnore === "true") return;
            const headers = Array.from(table.querySelectorAll("thead th")).map(cell => cell.textContent.trim());
            if (!headers.length) return;
            const hasEditableItems = Boolean(table.closest("form") && table.querySelector("tbody input, tbody select, tbody textarea"));
            table.classList.add(hasEditableItems ? "mobile-item-table" : "mobile-card-table");
            table.querySelectorAll("tbody tr").forEach(row => {
                Array.from(row.children).forEach((cell, index) => {
                    if (!cell.dataset.mobileLabel) cell.dataset.mobileLabel = headers[index] || "Detail";
                });
            });
        });
    };
    labelTables();

    const observer = new MutationObserver(mutations => {
        if (mutations.some(mutation => Array.from(mutation.addedNodes).some(node => node.nodeType === 1 && (node.matches?.("tr, table") || node.querySelector?.("tr, table"))))) {
            labelTables();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    document.querySelectorAll("input[name*='whatsapp' i], input[name='wa'], input[name*='phone' i]").forEach(input => {
        if (input.type === "text") input.type = "tel";
        input.setAttribute("inputmode", "tel");
        input.setAttribute("autocomplete", "tel");
    });
    document.querySelectorAll("input[name*='email' i]").forEach(input => {
        if (input.type === "text") input.type = "email";
        input.setAttribute("autocomplete", "email");
    });

    document.querySelectorAll("form").forEach(form => {
        const submit = form.querySelector(":scope > .actions button[type='submit'], :scope > .actions input[type='submit']");
        if (submit && !form.closest("dialog")) submit.closest(".actions")?.classList.add("sticky-mobile-action");
    });
})();

