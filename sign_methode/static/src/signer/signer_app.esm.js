/* global luxon */
import {Component, onWillStart, useRef, useState} from "@odoo/owl";
import {SignatureDialog} from "@web/core/signature/signature_dialog";
import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {rpc} from "@web/core/network/rpc";
import {useService} from "@web/core/utils/hooks";
import {SignPdfPages} from "../common/pdf_pages.esm";

const RESEND_DELAY = 45;

/**
 * The public signing page: welcome → code → document → done.
 *
 * Everything the server decides (which fields, where, which are required)
 * comes from /sign/<token>/info; the page only collects values.
 */
export class SignerApp extends Component {
    static template = "sign_methode.SignerApp";
    static components = {SignPdfPages};
    static props = {token: String, logo: {type: String, optional: true}};

    setup() {
        this.dialog = useService("dialog");
        this.codeInput = useRef("code");
        this.state = useState({
            step: "loading",
            info: null,
            error: "",
            busy: false,
            code: "",
            resendIn: 0,
            values: {},
            current: null,
            consent: false,
            finishOpen: false,
            declineOpen: false,
            declineReason: "",
            pagesReady: false,
        });
        this.adopted = {signature: null, initials: null};
        onWillStart(() => this.refresh());
    }

    // ------------------------------------------------------------------
    // Server
    // ------------------------------------------------------------------
    get base() {
        return `/sign/${encodeURIComponent(this.props.token)}`;
    }

    async refresh() {
        const info = await rpc(`${this.base}/info`);
        this.applyInfo(info);
    }

    applyInfo(info) {
        this.state.info = info;
        if (info.state === "signed" || info.request_state === "completed") {
            this.state.step = "done";
        } else if (info.state === "declined") {
            this.state.step = "declined";
        } else if (info.state !== "to_sign" || info.request_state !== "sent") {
            this.state.step = info.state === "waiting" ? "waiting" : "closed";
        } else if (this.state.step === "loading") {
            this.state.step = "welcome";
        } else if (info.verified && this.state.step === "otp") {
            this.state.step = "document";
        }
    }

    async call(route, params = {}) {
        this.state.busy = true;
        this.state.error = "";
        try {
            const result = await rpc(`${this.base}/${route}`, params);
            if (result.error) {
                this.state.error = result.error;
                return false;
            }
            this.applyInfo(result.info);
            return result;
        } finally {
            this.state.busy = false;
        }
    }

    // ------------------------------------------------------------------
    // Welcome and code
    // ------------------------------------------------------------------
    async onStart() {
        if (this.state.info.verified) {
            this.state.step = "document";
            return;
        }
        this.state.step = "otp";
        await this.sendCode();
    }

    async sendCode() {
        if (this.state.resendIn > 0) {
            return;
        }
        const ok = await this.call("otp/send");
        if (ok) {
            this.startCountdown();
            setTimeout(() => this.codeInput.el?.focus(), 50);
        }
    }

    startCountdown() {
        this.state.resendIn = RESEND_DELAY;
        clearInterval(this.countdown);
        this.countdown = setInterval(() => {
            this.state.resendIn -= 1;
            if (this.state.resendIn <= 0) {
                clearInterval(this.countdown);
            }
        }, 1000);
    }

    onCodeInput(ev) {
        this.state.code = ev.target.value.replace(/\D/g, "").slice(0, 6);
        ev.target.value = this.state.code;
        if (this.state.code.length === 6) {
            this.verifyCode();
        }
    }

    async verifyCode() {
        if (this.state.busy || this.state.code.length !== 6) {
            return;
        }
        const ok = await this.call("otp/verify", {code: this.state.code});
        if (!ok) {
            this.state.code = "";
            if (this.codeInput.el) {
                this.codeInput.el.value = "";
                this.codeInput.el.focus();
            }
        }
    }

    // ------------------------------------------------------------------
    // Fields
    // ------------------------------------------------------------------
    get items() {
        return this.state.info?.items || [];
    }

    get editableItems() {
        return this.items.filter((i) => !i.auto);
    }

    itemsOnPage(page) {
        return this.items.filter((i) => i.page === page);
    }

    isFilled(item) {
        if (item.auto) {
            return true;
        }
        const value = this.state.values[item.id];
        return item.type === "checkbox" ? value === true : Boolean(value);
    }

    get todo() {
        return this.editableItems.filter((i) => i.required && !this.isFilled(i));
    }

    get requiredCount() {
        return this.editableItems.filter((i) => i.required).length;
    }

    get doneCount() {
        return this.requiredCount - this.todo.length;
    }

    get progressPct() {
        return this.requiredCount ? Math.round((100 * this.doneCount) / this.requiredCount) : 100;
    }

    get isComplete() {
        return this.todo.length === 0;
    }

    itemStyle(item) {
        return `left:${item.x * 100}%;top:${item.y * 100}%;width:${item.w * 100}%;height:${
            item.h * 100
        }%;`;
    }

    itemClass(item) {
        return [
            "o_sign_item",
            `o_sign_item_${item.type}`,
            this.isFilled(item) ? "o_sign_item_filled" : "",
            item.required ? "o_sign_item_required" : "",
            this.state.current === item.id ? "o_sign_item_current" : "",
        ].join(" ");
    }

    autoValue(item) {
        if (item.type === "name") {
            return this.state.info.signer.name;
        }
        return luxon.DateTime.now().toLocaleString(luxon.DateTime.DATE_SHORT);
    }

    scrollTo(item) {
        if (!item) {
            return;
        }
        this.state.current = item.id;
        const el = document.querySelector(`.o_sign_item[data-item="${item.id}"]`);
        if (el) {
            el.scrollIntoView({behavior: "smooth", block: "center"});
            const input = el.querySelector("input, button");
            setTimeout(() => input?.focus({preventScroll: true}), 350);
        }
    }

    /** "Next": the first field left to fill after the current one. */
    onNext() {
        if (this.isComplete) {
            this.openFinish();
            return;
        }
        const todo = this.todo;
        const index = todo.findIndex((i) => i.id === this.state.current);
        this.scrollTo(todo[index + 1] || todo[0]);
    }

    /** "Go to signature": straight to the first unsigned signature field. */
    onGoToSignature() {
        const target =
            this.todo.find((i) => i.type === "signature") ||
            this.todo[0] ||
            this.editableItems.find((i) => i.type === "signature");
        if (target) {
            this.scrollTo(target);
            if (!this.isFilled(target) && ["signature", "initials"].includes(target.type)) {
                // Let the page scroll to the field first, so the signer sees
                // where the signature goes.
                setTimeout(() => this.onClickSignature(target), 450);
            }
        } else {
            this.openFinish();
        }
    }

    onClickSignature(item) {
        this.state.current = item.id;
        const kind = item.type === "initials" ? "initials" : "signature";
        if (this.adopted[kind] && !this.isFilled(item)) {
            this.state.values[item.id] = this.adopted[kind];
            this.afterFill();
            return;
        }
        this.dialog.add(SignatureDialog, {
            defaultName: this.state.info.signer.name,
            nameAndSignatureProps: {
                mode: "auto",
                signatureType: kind === "initials" ? "initial" : "signature",
                displaySignatureRatio: item.w / item.h > 1 ? Math.min(item.w / item.h, 4) : 3,
                // Not a "#hex": core embeds it in an encodeURI'd SVG data URL,
                // where "#" starts a fragment and blanks the image.
                fontColor: "rgb(9,61,168)",
            },
            uploadSignature: ({signatureImage}) => {
                this.adopted[kind] = signatureImage;
                // Adopting a signature applies it everywhere it is still missing.
                for (const other of this.editableItems) {
                    if (other.type === item.type && !this.isFilled(other)) {
                        this.state.values[other.id] = signatureImage;
                    }
                }
                this.state.values[item.id] = signatureImage;
                this.afterFill();
            },
        });
    }

    clearItem(item) {
        delete this.state.values[item.id];
    }

    onTextInput(item, ev) {
        this.state.values[item.id] = ev.target.value;
    }

    onCheck(item, ev) {
        this.state.values[item.id] = ev.target.checked;
        this.afterFill();
    }

    afterFill() {
        if (this.isComplete) {
            this.openFinish();
        }
    }

    // ------------------------------------------------------------------
    // Finish / decline
    // ------------------------------------------------------------------
    openFinish() {
        this.state.finishOpen = true;
        this.state.error = "";
    }

    async onSign() {
        if (!this.state.consent || !this.isComplete || this.state.busy) {
            return;
        }
        const values = {};
        for (const item of this.editableItems) {
            if (item.id in this.state.values) {
                values[item.id] = this.state.values[item.id];
            }
        }
        const ok = await this.call("submit", {values, consent: true});
        if (ok) {
            this.state.finishOpen = false;
            this.state.step = "done";
            window.scrollTo({top: 0, behavior: "smooth"});
        }
    }

    async onDecline() {
        const ok = await this.call("decline", {reason: this.state.declineReason});
        if (ok) {
            this.state.declineOpen = false;
            this.state.step = "declined";
        }
    }

    formatDate(isoDate) {
        return isoDate
            ? luxon.DateTime.fromISO(isoDate).toLocaleString(luxon.DateTime.DATE_FULL)
            : "";
    }

    get downloadUrl() {
        return `${this.base}/download`;
    }

    get documentUrl() {
        return `${this.base}/document`;
    }

    get otpLabel() {
        return this.state.info.otp_channel === "sms"
            ? _t("We sent a 6-digit code by SMS to %s.", this.state.info.otp_target)
            : _t("We sent a 6-digit code to %s.", this.state.info.otp_target);
    }
}

registry.category("public_components").add("sign_methode.signer_app", SignerApp);
