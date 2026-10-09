import hmac
import json
import re

from markupsafe import Markup
from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import Response, request

from ..tools import param

ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?")


class WebsiteChat(http.Controller):
    @http.route("/platform_crm_intake/chat/<int:channel_id>", type="http", auth="public", methods=["GET"])
    def chat(self, channel_id, lang=None, **_kwargs):
        """Live Chat page for the website to frame, so the widget runs on Odoo's
        origin instead of under the website's Content Security Policy."""
        origin = param(request.env, "website_origin")
        channel = request.env["im_livechat.channel"].sudo().browse(channel_id).exists()
        if not origin or not ORIGIN.fullmatch(origin) or not channel:
            raise NotFound()
        language = request.env["res.lang"].sudo().search(
            ["|", ("url_code", "=", lang or ""), ("code", "=", lang or "")], limit=1)
        response = request.render("platform_crm_intake.website_chat", {
            "channel": channel,
            # Read by static/src/chat_window_status.js for the status label.
            "html_lang": language.iso_code if language else "en",
            # Validated above, so it is safe to place in the page script.
            "website_origin": Markup(json.dumps(origin)),
        })
        # Only the website may frame this page; it reports chat state to it alone.
        response.headers["Content-Security-Policy"] = f"frame-ancestors {origin}"
        response.headers["Cache-Control"] = "no-store"
        if language:
            # Answer in the language of the page the visitor came from.
            response.set_cookie("frontend_lang", language.code, max_age=86400 * 30,
                                secure=True, samesite="Lax")
        return response

    @http.route("/platform_crm_intake/mail/inbound", type="http", auth="public", methods=["POST"], csrf=False)
    def inbound_mail(self, **_kwargs):
        """Raw replies relayed by the edge's email worker, authenticated by a shared secret."""
        secret = param(request.env, "inbound_secret") or ""
        supplied = request.httprequest.headers.get("Authorization", "").removeprefix("Bearer ")
        if not secret or not hmac.compare_digest(supplied.encode(), secret.encode()):
            return Response(status=401)
        raw = request.httprequest.get_data(cache=False)
        if not raw or len(raw) > 10 * 1024 * 1024:
            return Response(status=413)
        outcome = request.env["discuss.channel"].sudo()._platform_receive_reply(
            raw, request.httprequest.headers.get("X-Envelope-To"))
        # The worker bounces refused replies so a mistaken sender learns about it.
        return Response(outcome, status=403 if outcome == "forbidden" else 200, content_type="text/plain")
