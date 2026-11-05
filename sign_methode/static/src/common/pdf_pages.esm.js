/* global pdfjsLib */
import {Component, onMounted, onWillStart, onWillUnmount, useRef, useState} from "@odoo/owl";
import {loadPDFJSAssets} from "@web/core/utils/pdfjs";

/**
 * Renders every page of a PDF as a canvas, each wrapped in a positioned box
 * the parent fills through the "overlay" slot (slot param: page index).
 * Pages are drawn once at the container width × devicePixelRatio and then
 * scale with CSS, so overlays expressed in % of the page stay aligned.
 */
export class SignPdfPages extends Component {
    static template = "sign_methode.PdfPages";
    static props = {
        url: String,
        onLoaded: {type: Function, optional: true},
        slots: {type: Object, optional: true},
        pageClass: {type: String, optional: true},
    };

    setup() {
        this.root = useRef("root");
        this.state = useState({pages: [], error: false});
        this.canvases = {};
        this.cancelled = false;
        onWillStart(async () => {
            await loadPDFJSAssets();
            pdfjsLib.GlobalWorkerOptions.workerSrc = "/web/static/lib/pdfjs/build/pdf.worker.js";
        });
        onMounted(() => this.load());
        onWillUnmount(() => {
            this.cancelled = true;
            if (this.pdf) {
                this.pdf.destroy();
            }
        });
    }

    async load() {
        try {
            this.pdf = await pdfjsLib.getDocument({url: this.props.url, withCredentials: true})
                .promise;
        } catch {
            this.state.error = true;
            return;
        }
        const pages = [];
        for (let i = 1; i <= this.pdf.numPages; i++) {
            const page = await this.pdf.getPage(i);
            const viewport = page.getViewport({scale: 1});
            pages.push({index: i - 1, page, ratio: viewport.height / viewport.width});
        }
        if (this.cancelled) {
            return;
        }
        this.state.pages = pages.map(({index, ratio}) => ({index, ratio}));
        // OWL renders asynchronously: wait for the canvases before drawing.
        for (let frame = 0; frame < 120; frame++) {
            await new Promise((resolve) => requestAnimationFrame(resolve));
            if (this.cancelled) {
                return;
            }
            if (this.root.el.querySelectorAll("canvas[data-page]").length === pages.length) {
                break;
            }
        }
        const width = Math.min(this.root.el.clientWidth || 800, 1100);
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        for (const {index, page} of pages) {
            if (this.cancelled) {
                return;
            }
            const canvas = this.root.el.querySelector(`canvas[data-page="${index}"]`);
            const base = page.getViewport({scale: 1});
            const viewport = page.getViewport({scale: (width * dpr) / base.width});
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            await page.render({canvasContext: canvas.getContext("2d"), viewport}).promise;
            canvas.closest(".o_sign_page").classList.add("o_sign_page_ready");
        }
        if (this.props.onLoaded) {
            this.props.onLoaded(pages.length);
        }
    }
}
