from odoo import models
from odoo.http import request
from odoo.tools.misc import get_lang


class ChatbotScript(models.Model):
    _inherit = "chatbot.script"

    def _get_chatbot_language(self):
        # Without Odoo's language cookie (the website's chat frame sets it from
        # the page), follow the visitor's browser, as the widget's interface does.
        if request and not request.httprequest.cookies.get("frontend_lang") and request.best_lang:
            return get_lang(self.env, lang_code=request.best_lang).code
        return super()._get_chatbot_language()


class ChatbotScriptStep(models.Model):
    _inherit = "chatbot.script.step"

    def _process_step_create_lead(self, discuss_channel):
        # The team already receives the chat by email, with a link to the lead,
        # so skip Odoo's separate "you have been assigned" email.
        return super(ChatbotScriptStep, self.with_context(mail_auto_subscribe_no_notify=True)
                     )._process_step_create_lead(discuss_channel)
