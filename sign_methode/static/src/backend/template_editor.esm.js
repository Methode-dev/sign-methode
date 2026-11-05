import {Component, onWillStart, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {useService} from "@web/core/utils/hooks";
import {SignPdfPages} from "../common/pdf_pages.esm";

// Default size of a new field, as a fraction of an A4 page.
const DEFAULT_SIZE = {
    signature: {w: 0.26, h: 0.065},
    initials: {w: 0.09, h: 0.045},
    name: {w: 0.24, h: 0.025},
    date: {w: 0.16, h: 0.025},
    text: {w: 0.24, h: 0.025},
    checkbox: {w: 0.025, h: 0.018},
};
const ICONS = {
    signature: "fa-pencil",
    initials: "fa-font",
    name: "fa-user",
    date: "fa-calendar",
    text: "fa-i-cursor",
    checkbox: "fa-check-square-o",
};
const clamp = (v, min, max) => Math.min(Math.max(v, min), max);
let nextLocalId = 1;

/** Drop fields on the template's pages, move and resize them, save. */
export class SignTemplateEditor extends Component {
    static template = "sign_methode.TemplateEditor";
    static components = {SignPdfPages};
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.templateId = this.props.action.params?.template_id || this.props.action.context?.active_id;
        this.icons = ICONS;
        this.state = useState({
            data: null,
            fields: [],
            roleId: null,
            selected: null,
            dirty: false,
            saving: false,
        });
        onWillStart(async () => {
            const data = await this.orm.call("sign.methode.template", "editor_get_data", [
                [this.templateId],
            ]);
            this.state.data = data;
            this.state.fields = data.fields.map((f) => ({...f, key: `f${f.id}`}));
            this.state.roleId = data.roles[0]?.id;
        });
    }

    get readonly() {
        return this.state.data.readonly;
    }

    roleColor(roleId) {
        const role = this.state.data.roles.find((r) => r.id === roleId);
        return role ? role.color : 0;
    }

    roleName(roleId) {
        return this.state.data.roles.find((r) => r.id === roleId)?.name || "";
    }

    typeLabel(type) {
        return this.state.data.types.find((t) => t.key === type)?.label || type;
    }

    fieldsOnPage(page) {
        return this.state.fields.filter((f) => f.page === page);
    }

    fieldStyle(f) {
        return `left:${f.x * 100}%;top:${f.y * 100}%;width:${f.w * 100}%;height:${f.h * 100}%;`;
    }

    get selected() {
        return this.state.fields.find((f) => f.key === this.state.selected);
    }

    // -- palette drag & drop ---------------------------------------------------
    onPaletteDragStart(type, ev) {
        ev.dataTransfer.setData("text/x-sign-type", type);
        ev.dataTransfer.effectAllowed = "copy";
    }

    onPageDragOver(ev) {
        if (!this.readonly && ev.dataTransfer.types.includes("text/x-sign-type")) {
            ev.preventDefault();
        }
    }

    onPageDrop(page, ev) {
        const type = ev.dataTransfer.getData("text/x-sign-type");
        if (!type || this.readonly) {
            return;
        }
        ev.preventDefault();
        const rect = ev.currentTarget.getBoundingClientRect();
        this.addField(
            type,
            page,
            (ev.clientX - rect.left) / rect.width,
            (ev.clientY - rect.top) / rect.height
        );
    }

    /** Click on a palette entry: drop the field in the middle of page 1. */
    onPaletteClick(type) {
        if (!this.readonly) {
            this.addField(type, 0, 0.5, 0.5);
        }
    }

    addField(type, page, cx, cy) {
        if (!this.state.roleId) {
            this.notification.add(_t("Create a role first."), {type: "warning"});
            return;
        }
        const size = DEFAULT_SIZE[type];
        const field = {
            key: `n${nextLocalId++}`,
            id: false,
            role_id: this.state.roleId,
            type,
            required: true,
            page,
            w: size.w,
            h: size.h,
            x: clamp(cx - size.w / 2, 0, 1 - size.w),
            y: clamp(cy - size.h / 2, 0, 1 - size.h),
            placeholder: "",
        };
        this.state.fields.push(field);
        this.state.selected = field.key;
        this.state.dirty = true;
    }

    // -- move / resize ---------------------------------------------------------
    onFieldPointerDown(field, mode, ev) {
        this.state.selected = field.key;
        if (this.readonly || ev.button !== 0) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        const page = ev.currentTarget.closest(".o_sign_page_overlay").getBoundingClientRect();
        const start = {x: ev.clientX, y: ev.clientY, fx: field.x, fy: field.y, fw: field.w, fh: field.h};
        const move = (e) => {
            const dx = (e.clientX - start.x) / page.width;
            const dy = (e.clientY - start.y) / page.height;
            if (mode === "move") {
                field.x = clamp(start.fx + dx, 0, 1 - field.w);
                field.y = clamp(start.fy + dy, 0, 1 - field.h);
            } else {
                field.w = clamp(start.fw + dx, 0.01, 1 - field.x);
                field.h = clamp(start.fh + dy, 0.008, 1 - field.y);
            }
            this.state.dirty = true;
        };
        const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", up);
    }

    onKeydown(ev) {
        const field = this.selected;
        if (!field || this.readonly || ["INPUT", "SELECT", "TEXTAREA"].includes(ev.target.tagName)) {
            return;
        }
        const step = ev.shiftKey ? 0.01 : 0.002;
        const moves = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]};
        if (ev.key === "Delete" || ev.key === "Backspace") {
            this.removeSelected();
        } else if (moves[ev.key]) {
            const [dx, dy] = moves[ev.key];
            field.x = clamp(field.x + dx, 0, 1 - field.w);
            field.y = clamp(field.y + dy, 0, 1 - field.h);
            this.state.dirty = true;
        } else {
            return;
        }
        ev.preventDefault();
    }

    // -- side panel ------------------------------------------------------------
    updateSelected(key, value) {
        const field = this.selected;
        if (field) {
            field[key] = value;
            this.state.dirty = true;
        }
    }

    removeSelected() {
        this.state.fields = this.state.fields.filter((f) => f.key !== this.state.selected);
        this.state.selected = null;
        this.state.dirty = true;
    }

    async save() {
        this.state.saving = true;
        try {
            const saved = await this.orm.call("sign.methode.template", "editor_save_fields", [
                [this.templateId],
                this.state.fields.map(({key, ...vals}) => vals),
            ]);
            this.state.fields = saved.map((f) => ({...f, key: `f${f.id}`}));
            this.state.selected = null;
            this.state.dirty = false;
            this.notification.add(_t("Template saved."), {type: "success"});
        } finally {
            this.state.saving = false;
        }
    }

    async back() {
        if (this.state.dirty) {
            await this.save();
        }
        this.action.restore();
    }
}

registry.category("actions").add("sign_methode.template_editor", SignTemplateEditor);
