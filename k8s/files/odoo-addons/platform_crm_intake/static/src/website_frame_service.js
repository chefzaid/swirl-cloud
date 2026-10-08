import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";

/**
 * Runs only in the chat page that the company website frames
 * (/platform_crm_intake/chat). With ?open=1 it opens the chat once Odoo has
 * restored its windows; otherwise it only restores them. It reopens the chat
 * when the website asks and tells the website whether a chat window is
 * open, so the website can show or hide the frame.
 */
export const websiteFrameService = {
    dependencies: ["im_livechat.livechat", "mail.store"],
    start(env, { "im_livechat.livechat": livechat, "mail.store": store }) {
        const website = window.odoo?.platformWebsiteOrigin;
        if (!website || window.parent === window) {
            return;
        }
        const post = (data) => window.parent.postMessage({ type: "odoo-livechat", ...data }, website);
        let opening;
        // One open at a time: overlapping requests would start two chats.
        const open = () => (opening ??= openChat().finally(() => (opening = undefined)));
        const openChat = async () => {
            await store.isReady;
            await store.chatHub.initPromise;
            if (!store.livechat_available) {
                return post({ open: false, failed: true });
            }
            const folded = store.chatHub.folded[0];
            const thread = store.activeVisitorLivechats[0];
            if (folded) {
                await folded.open({ focus: true });
            } else if (thread) {
                await thread.openChatWindow({ focus: true });
            } else if (!(await livechat.open())) {
                post({ open: false, failed: true });
            }
        };
        let reported;
        browser.setInterval(() => {
            const isOpen = store.chatHub.opened.length > 0;
            if (isOpen !== reported) {
                reported = isOpen;
                post({ open: isOpen });
            }
        }, 250);
        browser.addEventListener("message", (event) => {
            if (event.origin === website && event.data?.type === "odoo-livechat-open") {
                open().catch(() => post({ open: false, failed: true }));
            }
        });
        if (new URLSearchParams(window.location.search).get("open") === "1") {
            open().catch(() => post({ open: false, failed: true }));
        }
    },
};

registry.category("services").add("platform_crm_intake.website_frame", websiteFrameService);
