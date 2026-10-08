{
    "name": "Platform CRM Intake",
    "summary": "The company website's CRM lead intake, Live Chat frame and chat emails",
    "version": "19.0.1.0.0",
    "author": "Platform",
    "license": "LGPL-3",
    "depends": ["crm", "im_livechat"],
    "data": [
        "data/res_groups.xml",
        "data/ir_cron.xml",
        "views/chat_frame.xml",
        "views/chat_emails.xml",
    ],
    "assets": {
        # Loaded with every external chat embed; it acts only inside the
        # website's chat frame, where the page sets platformWebsiteOrigin.
        "im_livechat.assets_embed_external": ["platform_crm_intake/static/src/website_frame_service.js"],
    },
    "installable": True,
    "application": False,
}
