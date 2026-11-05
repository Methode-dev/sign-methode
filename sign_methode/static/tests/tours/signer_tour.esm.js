import {registry} from "@web/core/registry";

// Class-based selectors only, so the tour runs in any language.
registry.category("web_tour.tours").add("sign_methode_signer_tour", {
    steps: () => [
        {
            content: "The welcome card invites to review and sign",
            trigger: ".o_sign_welcome .o_sign_start",
            run: "click",
        },
        {
            content: "The document pages are drawn",
            trigger: ".o_sign_page.o_sign_page_ready",
        },
        {
            content: "Go straight to the signature field",
            trigger: ".o_sign_btn_goto",
            run: "click",
        },
        {
            content: "Adopt the signature generated from the name",
            trigger: ".modal .modal-footer .btn-primary:enabled",
            run: "click",
        },
        {
            content: "Every field is filled: the finish sheet opens",
            trigger: ".o_sign_sheet .o_sign_consent input",
            run: "click",
        },
        {
            content: "Sign",
            trigger: ".o_sign_submit:enabled",
            run: "click",
        },
        {
            content: "The signed document can be downloaded",
            trigger: ".o_sign_done .o_sign_download",
        },
    ],
});
