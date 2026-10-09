import { ChatWindow } from "@mail/core/common/chat_window";
import { patch } from "@web/core/utils/patch";

/**
 * In the company website's chat frame, show the live chat correspondent as
 * online under their name, in the page's language. The frame sets <html lang>
 * from the website page the visitor came from.
 */
patch(ChatWindow.prototype, {
    get platformStatus() {
        const thread = this.thread;
        if (!window.odoo?.platformWebsiteOrigin || thread?.channel_type !== "livechat" || thread.livechat_end_dt) {
            return "";
        }
        return document.documentElement.lang.startsWith("fr") ? "En ligne" : "Online";
    },
});
